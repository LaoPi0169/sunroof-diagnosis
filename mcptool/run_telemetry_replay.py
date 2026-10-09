"""合成运行期回放 (Week 16) —— 固定时钟、固定剧本的 10 个 task_run_id (两种体温计之二)。

回链: week-16/runtime-sli-spec-v1.md (SLI 口径) + week-16/trace-map-v1.md (两断点)。
不是真实流量: 目的是给运行期 SLI 一个可复现的基线分母 (与考卷金丝雀 60 例分母
无关; 两视图分子分母永不共享)。

十任务剧本 (固定顺序, 六类可见终态 + 两种非终态 + 一例双视图分歧活证据):
  O-01 read answered    (NS-DEV-01, 工具在环, 与第 13 周回归同构)
  O-02 read abstained   (NS-DEV-07, 期望 refused -> 可见终态 abstained)
  O-03 read escalated   (NS-DEV-08, 期望 escalated)
  O-04 write action_completed (预览->批准->执行->核验, 外部效果恰 1)
  O-05 write approval_revoked  (批准后执行前撤销, 0 效果, A-04)
  O-06 write approval_denied   (策略拒绝 executive-review, 0 效果)
  O-07 write action_completed (断连->按幂等键先查后说->独立核验, 恰 1 效果, A-06)
  O-08 write reconciliation_pending (断连+查询不可用, 非终态, 1 效果未确认)
  O-09 read  system_failed     (读路径超时, 诚实失败, 0 效果)
  O-10 read  answered (NS-HOLD-06) —— 运行视图计入 allowed, 考卷视图是硬门失败:
       运行期信号看不见内容错误的活证据 (F12-01)。

记录面: 每个 task_run_id 一条九阶段 TaskTrace (mcptool/telemetry.py);
只存 状态码/版本链/引用 ID/计数/时长 —— query 原文、答案/文档正文、审批载荷、
客户字段、凭据永不入遥测 (scan_spans_for_violations 机械自检)。
细节受限原因 (细分 reason_typed) 只留在 Host 受限审计, 不进遥测。

用法: python -m mcptool.run_telemetry_replay [--out week-16/telemetry-span-sample-v1.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcptool import contract as C
from mcptool import write_contract as W
from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.policy import WritePolicy
from mcptool.telemetry import (  # noqa: E402
    STAGE_APPROVAL, STAGE_GENERATION, STAGE_IDENTITY, STAGE_MCP_CALL,
    STAGE_RECONCILIATION, STAGE_REQUEST, STAGE_RESPONSE, STAGE_RETRIEVAL,
    STAGE_VERIFICATION, TaskTrace, reduce_runtime_metrics,
)
from mcptool.ticket_service import TicketService
from .run_canary import (  # noqa: E402
    ANSWER_KEY_SOURCE, prepare_evaluation_context,
)

FIXED_NOW = "2026-10-09T00:00:00Z"   # 固定时钟: 审批 TTL/时间戳完全可复现
TENANT = "northstar-training"
SESSIONS = {
    "support_agent": TrustedSession(TENANT, "user-zhou-ning", "support_agent"),
    "supervisor": TrustedSession(TENANT, "user-gao-supervisor", "supervisor"),
}
RAG_TERMINAL = "escalate"
UPSTREAM_SCENARIO = "ns-1042-rag-escalate (与第 14/15 周同一场景输入)"

WRITE_ARGS = {"case_id": "NS-1042", "reason_code": "POLICY_CONFLICT",
              "source_refs": ["upgrade-east-v2", "upgrade-notice-v4"],
              "target_queue": "billing-supervisor-review"}
DENIED_ARGS = dict(WRITE_ARGS, target_queue="executive-review")

REVOKE_REASON = "风险复核：执行前撤销（O-05 演示）"
READ_SPECS = (  # (task_no, case_id) —— 三例均在 13 条回归种子内 (工具在环已证一致)
    ("O-01", "NS-DEV-01"),   # expected answered
    ("O-02", "NS-DEV-07"),   # expected refused  -> abstained
    ("O-03", "NS-DEV-08"),   # expected escalated
)
HOLDOUT_DEMO_CASE = "NS-HOLD-06"


def _fixed_clock():
    return FIXED_NOW


def _fresh_write_stack():
    """独立「外部世界」: 每个写任务新建 工单/审批/策略/Host (互不串状态)。"""
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSIONS["support_agent"], clock=_fixed_clock,
                   ticket_service=tickets, approval_service=approvals,
                   policy=policy)
    return host, tickets, approvals, policy


class _OutageAfterCommitTickets(TicketService):
    """A-06 第二段注入缝 (O-08 专用): 提交成功、响应丢失的同一瞬间对账查询不可用。

    与 week-15 静态 query_outage 缝不同: 重放查重 (创建前) 必须通过 —— 否则创建
    本身 fail-closed 拒绝 (0 效果), 到不了「已提交但结果未确认」的对账段。
    本子类在 create_review_ticket(inject_response_loss=True) 抛 ResponseLostError
    的同时把 query_outage 置 True, 精确复现「查得到创建、查不到对账」的窗口。
    """

    def create_review_ticket(self, *, inject_response_loss=False, **kwargs):
        if not inject_response_loss:
            return super().create_review_ticket(**kwargs)
        try:
            return super().create_review_ticket(inject_response_loss=True,
                                                **kwargs)
        finally:
            self.query_outage = True


def _issue_approval(approvals, policy, task_id):
    """受控确认动作 -> 结构化审批 (绑定当前任务 run_id; 不读自然语言)。"""
    return approvals.issue(
        approval_id=f"approval-{task_id}",
        requester=SESSIONS["support_agent"].user_id,
        tenant_id=TENANT, run_id=task_id, tool=W.TOOL_CREATE,
        tool_version=W.TOOL_VERSION, approved_parameters=WRITE_ARGS,
        resource_scope=W.resource_scope(WRITE_ARGS["case_id"]),
        policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW)


def _emit_not_applicable(trace, stages, note):
    """按剧本缺失的阶段落 not_applicable span (缺席也要留痕, 不留空洞)。"""
    for stage in stages:
        trace.span(stage, result="not_applicable", not_applicable=True,
                   safe_refs={"note": note})


def _run_frozen_case(case, label, conn, manifest, meta, restricted):
    """调第 12 周冻结评测的单例装配 (与金丝雀同一函数引用, generation/* 零改动)。"""
    from generation import run_rag_evaluation as runner
    return runner.run_case(case, label, conn, manifest, meta, restricted)


def _run_read_task(task_no, case_id, context, *, seed):
    """O-01..O-03: 工具在环读任务 (MCP 取上下文 -> 冻结 RAG 管线 -> 状态呈现)。"""
    conn, manifest, meta, restricted, eval_rows, frozen_key = context
    case = eval_rows[case_id]
    key = frozen_key[case_id]
    role = case["user_role"]
    task_id = f"run-w16-{seed}-{task_no}"
    trace = TaskTrace(task_id, kind="read", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"case_id": case_id, "split": key["split"],
                          "family": key["family"]})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": role, "tenant_id": TENANT})
    host = McpHost(SESSIONS[role], clock=_fixed_clock)
    envelope = host.call({"case_id": case_id, "fields": list(C.FIELD_ENUM)})
    mcp_ok = envelope.get("status") == "ok"
    trace.span(STAGE_MCP_CALL, result=envelope.get("status"),
               versions={"tool_version": C.TOOL_VERSION},
               safe_refs={"tool": C.TOOL_NAME})
    if not mcp_ok:
        raise RuntimeError(f"{task_id}: 回放剧本假定工具在环成功, 实得 {envelope}")
    tool_ctx = envelope["records"][0]["context"]
    case_via_tool = {**case, "task_context": {
        "category": C.TASK_TYPE_TO_CATEGORY[tool_ctx["task_type"]],
        "region": tool_ctx["region"],
        "business_line": case["task_context"]["business_line"],
        "period": case["task_context"]["period"]}}
    label = (key["expected_state"], ANSWER_KEY_SOURCE, {
        "gold_document_ids": key.get("label_gold_ids", []),
        "forbidden_document_ids": key.get("label_forbidden_ids", [])})
    record = _run_frozen_case(case_via_tool, label, conn, manifest, meta,
                              restricted)
    trace.span(STAGE_RETRIEVAL, result="frozen_retriever",
               versions={"corpus_version": manifest["bindings"]["corpus_version"],
                         "dataset_version": manifest["bindings"]["dataset_version"],
                         "retriever_version": manifest["bindings"]["retriever_version"]},
               safe_refs={"pool_top_doc_ids":
                          record["signals"]["pool_ranked_doc_ids"][:5]})
    trace.span(STAGE_GENERATION, result=record["state"],
               versions={"candidate_id": record["candidate_id"],
                         "prompt_version": manifest["generation"]["prompt_version"]},
               safe_refs={"reason_code": record["reason_code"]})
    trace.span(STAGE_VERIFICATION, result="citation_and_leak_checked",
               versions={"corpus_version": record["corpus_version"]},
               safe_refs={"citation_doc_ids":
                          [c["document_id"] for c in record["citations"]],
                          "citation_issue_count": len(record["citation_issues"]),
                          "leak_event_count": record["leak_events"]})
    _emit_not_applicable(trace, (STAGE_APPROVAL,), "read 任务无审批段")
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "read 任务无对账段")
    trace.span(STAGE_RESPONSE, result=record["mapped_state"])
    return trace.finish(record["mapped_state"])


def _run_holdout_task(task_no, case_id, context, *, seed):
    """O-10: 双视图分歧活证据 —— 运行视图 answered (allowed), 考卷视图硬门失败。

    holdout 案例不在只读工单库 -> mcp_call 段如实 not_applicable (内容管线与
    冻结评测同构; 与第 12 周同一 run_case, 零改动)。
    """
    conn, manifest, meta, restricted, eval_rows, frozen_key = context
    case = eval_rows[case_id]
    key = frozen_key[case_id]
    task_id = f"run-w16-{seed}-{task_no}"
    trace = TaskTrace(task_id, kind="read", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"case_id": case_id, "split": key["split"],
                          "family": key["family"]})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": key["user_role"], "tenant_id": TENANT})
    _emit_not_applicable(trace, (STAGE_MCP_CALL,),
                         "holdout 案例不在只读工单库; 内容管线与冻结评测同构")
    label = (key["expected_state"], ANSWER_KEY_SOURCE, {
        "gold_document_ids": key.get("label_gold_ids", []),
        "forbidden_document_ids": key.get("label_forbidden_ids", [])})
    record = _run_frozen_case(case, label, conn, manifest, meta, restricted)
    trace.span(STAGE_RETRIEVAL, result="frozen_retriever",
               versions={"corpus_version": manifest["bindings"]["corpus_version"],
                         "dataset_version": manifest["bindings"]["dataset_version"],
                         "retriever_version": manifest["bindings"]["retriever_version"]},
               safe_refs={"pool_top_doc_ids":
                          record["signals"]["pool_ranked_doc_ids"][:5]})
    trace.span(STAGE_GENERATION, result=record["state"],
               versions={"candidate_id": record["candidate_id"],
                         "prompt_version": manifest["generation"]["prompt_version"]},
               safe_refs={"reason_code": record["reason_code"]})
    trace.span(STAGE_VERIFICATION, result="citation_and_leak_checked",
               safe_refs={"citation_doc_ids":
                          [c["document_id"] for c in record["citations"]],
                          "citation_issue_count": len(record["citation_issues"]),
                          "leak_event_count": record["leak_events"]})
    _emit_not_applicable(trace, (STAGE_APPROVAL,), "read 任务无审批段")
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "read 任务无对账段")
    trace.span(STAGE_RESPONSE, result=record["mapped_state"])
    trace.safe_refs["canary_link"] = {
        "case_id": case_id,
        "expected_state": key["expected_state"],
        "observed_mapped_state": record["mapped_state"],
        "runtime_view": "answered 计入 allowed_terminal_rate (过程健康)",
        "canary_view": "state_match=false, 硬门失败 (F12-01: 自信作答于无答案例)",
        "lesson": "运行期信号看不见内容错误 —— 两个体温计缺一不可",
    }
    return trace.finish(record["mapped_state"])


def _run_timeout_task(task_no, *, seed):
    """O-09: 读路径超时 -> system_failed (诚实失败; 0 效果; 晚到的好结果也是坏的)。"""
    task_id = f"run-w16-{seed}-{task_no}"
    trace = TaskTrace(task_id, kind="read", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"case_id": "NS-1042", "family": "tooling"})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    host = McpHost(SESSIONS["support_agent"], clock=_fixed_clock,
                   deadline_ms=C.DEADLINE_MS)
    envelope = host.call({"case_id": "NS-1042", "fields": list(C.FIELD_ENUM)},
                         delay_seconds=0.65)
    outcome = envelope.get("error", {}).get("code", envelope.get("status"))
    trace.span(STAGE_MCP_CALL, result=outcome,
               versions={"tool_version": C.TOOL_VERSION},
               safe_refs={"tool": C.TOOL_NAME, "deadline_ms": C.DEADLINE_MS},
               error_class=outcome)
    _emit_not_applicable(trace, (STAGE_RETRIEVAL, STAGE_GENERATION,
                                 STAGE_VERIFICATION, STAGE_APPROVAL,
                                 STAGE_RECONCILIATION),
                         "超时后下游阶段未执行 (晚到结果整体作废)")
    trace.span(STAGE_RESPONSE, result="error_presented", error_class=outcome)
    return trace.finish(outcome)


def _write_trace_shell(task_no, *, seed):
    """写任务公共骨架: request/identity/上游声明 + 独立写栈。"""
    task_id = f"run-w16-{seed}-{task_no}"
    trace = TaskTrace(task_id, kind="write", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="write_request",
               safe_refs={"case_id": WRITE_ARGS["case_id"],
                          "upstream_scenario": UPSTREAM_SCENARIO})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    _emit_not_applicable(trace, (STAGE_RETRIEVAL, STAGE_GENERATION),
                         "写任务不检索; rag_terminal 由上游场景给定 (escalate)")
    return task_id, trace


def _policy_span(trace, host, *, decision):
    """策略判定段 (预览/执行共用 ticket-create-policy-v1; 版本链断点 #1 的一段)。"""
    trace.span(STAGE_VERIFICATION, result=f"policy:{decision}",
               versions={"policy_version": host._policy.POLICY_VERSION},
               safe_refs={"rag_terminal": RAG_TERMINAL,
                          "allowed_queue": host._policy.ALLOWED_QUEUES[0]})


def _approval_span(trace, *, approval_id, decision, revoked=False):
    """审批段 (安全引用: 仅 ID 与决定枚举; 载荷属受限审计不入遥测)。"""
    trace.span(STAGE_APPROVAL, result=f"approval:{decision}",
               versions={"approval_record_version": "approval-record-v1"},
               safe_refs={"approval_id": approval_id, "decision": decision,
                          "revoked": revoked})


def _mcp_span(trace, envelope, *, tool, effects_delta):
    """MCP 调用段 (断点 #2: 与审批 ID + 幂等键精确关联)。"""
    trace.span(STAGE_MCP_CALL, result=_outcome_of(envelope),
               versions={"tool_version": W.TOOL_VERSION,
                         "contract_sha256": W.CONTRACT_SHA256},
               safe_refs={"tool": tool,
                          "idempotency_key": W.idempotency_key(trace.task_run_id),
                          "ticket_id": envelope.get("ticket_id"),
                          "verified_by": envelope.get("verified_by"),
                          "effects_delta": effects_delta},
               error_class=envelope.get("error", {}).get("code"))


def _outcome_of(envelope) -> str:
    if envelope.get("status") == "error":
        return envelope["error"]["code"]
    return str(envelope.get("status"))


def _run_write_approved(task_no, *, seed):
    """O-04: 预览 -> 批准 -> 执行 -> 核验 -> action_completed (外部效果恰 1)。"""
    task_id, trace = _write_trace_shell(task_no, seed=seed)
    host, tickets, approvals, policy = _fresh_write_stack()
    before = tickets.ticket_count()
    preview = host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                              rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    approval = _issue_approval(approvals, policy, task_id)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved")
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "无断连, 无对账段")
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    return trace.finish(_outcome_of(envelope), effects=delta)


def _run_write_revoked(task_no, *, seed):
    """O-05: 批准后执行前撤销 -> approval_revoked (0 效果; A-04)。"""
    task_id, trace = _write_trace_shell(task_no, seed=seed)
    host, tickets, approvals, policy = _fresh_write_stack()
    before = tickets.ticket_count()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                    rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    approval = _issue_approval(approvals, policy, task_id)
    approvals.revoke(approval["approval_id"],
                     revoked_by="risk-officer-wang", revoked_at=FIXED_NOW,
                     reason=REVOKE_REASON)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved_then_revoked", revoked=True)
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "无断连, 无对账段")
    trace.span(STAGE_RESPONSE, result="error_presented",
               error_class=_outcome_of(envelope))
    return trace.finish(_outcome_of(envelope), effects=delta)


def _run_write_denied(task_no, *, seed):
    """O-06: 策略在审批之前拒绝 -> approval_denied (0 效果; 正常安全终态)。"""
    task_id, trace = _write_trace_shell(task_no, seed=seed)
    host, tickets, _approvals, _policy = _fresh_write_stack()
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_PREVIEW, DENIED_ARGS, run_id=task_id,
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _policy_span(trace, host, decision="denied_at_preview")
    _approval_span(trace, approval_id="none_denied_before_issuance",
                   decision="not_issued")
    _mcp_span(trace, envelope, tool=W.TOOL_PREVIEW, effects_delta=delta)
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "无断连, 无对账段")
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    return trace.finish(_outcome_of(envelope), effects=delta)


def _run_write_reconciled(task_no, *, seed):
    """O-07: 断连 -> result_unknown -> 按幂等键先查后说 -> action_completed (A-06)。

    恰 1 个外部效果; reconciliation 段是真实 span (断点 #2 的第二半)。
    """
    task_id, trace = _write_trace_shell(task_no, seed=seed)
    host, tickets, approvals, policy = _fresh_write_stack()
    before = tickets.ticket_count()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                    rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    approval = _issue_approval(approvals, policy, task_id)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved")
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL,
                               fail_after_commit=True)  # 提交成功后响应丢失
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    trace.span(STAGE_RECONCILIATION, result="reconciled_from_result_unknown",
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"idempotency_key": W.idempotency_key(task_id),
                          "ticket_id": envelope.get("ticket_id"),
                          "recovered_from": W.STATUS_UNKNOWN,
                          "verified_by": "ticket.get"})
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    return trace.finish(_outcome_of(envelope), effects=delta)


def _run_write_pending(task_no, *, seed):
    """O-08: 断连 + 对账查询恰好不可用 -> reconciliation_pending (非终态; 1 效果未确认)。

    禁止伪造终态: 该任务不计入 allowed_terminal_rate 分子, 效果按「未确认」单独
    计数 (unconfirmed_effects)。注入缝见 _OutageAfterCommitTickets —— 创建前的
    重放查重通过 (否则创建 fail-closed, 到不了对账段), 提交成功响应对账查询失效。
    """
    task_id, trace = _write_trace_shell(task_no, seed=seed)
    host, tickets, approvals, policy = _fresh_write_stack()
    host._ticket_service = _OutageAfterCommitTickets()
    tickets = host._ticket_service
    before = tickets.ticket_count()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                    rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    approval = _issue_approval(approvals, policy, task_id)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved")
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL,
                               fail_after_commit=True)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    trace.span(STAGE_RECONCILIATION,
               result="reconciliation_query_unavailable",
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"idempotency_key": W.idempotency_key(task_id),
                          "outcome": W.STATUS_RECONCILIATION_PENDING,
                          "unconfirmed_effects": delta,
                          "no_fabricated_terminal": True})
    trace.span(STAGE_RESPONSE, result="pending_presented")
    return trace.finish(_outcome_of(envelope), effects=delta)


def run_replay(context=None, *, seed=None) -> dict:
    """跑十任务剧本; 返回 {seed, spans, tasks, metrics}。

    终态序列/效果计数/ID 关联完全可复现 (固定时钟 + 冻结候选 + 确定性组件);
    duration_ms 为观测值 (perf_counter 间隔), 只作展示, 不作断言门。
    context 缺省自建 (含冻结输入 sha256 fail-closed 校验); 连接由本函数关闭。
    """
    owns_ctx = context is None
    context = context or prepare_evaluation_context()
    seed = seed or time.strftime("%H%M%S")
    traces = []
    try:
        for task_no, case_id in READ_SPECS:
            traces.append(_run_read_task(task_no, case_id, context, seed=seed))
        traces.append(_run_write_approved("O-04", seed=seed))
        traces.append(_run_write_revoked("O-05", seed=seed))
        traces.append(_run_write_denied("O-06", seed=seed))
        traces.append(_run_write_reconciled("O-07", seed=seed))
        traces.append(_run_write_pending("O-08", seed=seed))
        traces.append(_run_timeout_task("O-09", seed=seed))
        traces.append(_run_holdout_task("O-10", HOLDOUT_DEMO_CASE, context,
                                        seed=seed))
    finally:
        if owns_ctx:
            context[0].close()
    spans = [span for trace in traces for span in trace.spans]
    metrics = reduce_runtime_metrics(traces)
    return {
        "seed": seed,
        "spans": spans,
        "tasks": [trace.as_task_row() for trace in traces],
        "task_traces": traces,
        "metrics": metrics,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="合成运行期回放 (Week 16)")
    parser.add_argument("--out", default=None,
                        help="遥测 span 流 jsonl 输出路径 (缺省只打印汇总)")
    args = parser.parse_args(argv)
    result = run_replay()
    if args.out:
        out_path = os.path.join(PROJECT_ROOT, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            for span in result["spans"]:
                f.write(json.dumps(span, ensure_ascii=False) + "\n")
    print(json.dumps({"seed": result["seed"],
                      "tasks": result["tasks"],
                      "metrics": result["metrics"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()






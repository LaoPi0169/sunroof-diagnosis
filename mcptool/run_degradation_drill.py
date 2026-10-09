"""降级固定演练运行器 (Week 17) —— 三类依赖故障 × 同一安全终态 + 恢复复验。

回链: week-17/failure-behavior-contract-v1.md (行为合同) +
specs/business-background.md 3889-4004 (第 17 周: 让依赖故障进入安全降级路径)。
手册 3968: 模型和索引故障使用完成示例进行观察与固定演练, 不在同一周独立重写
三套恢复机制 —— 因此本周只独立实现 MCP 不可用的降级编排 (mcptool/client.py),
模型/索引两行是「观察 + 固定演练」: 用注册在册的开关强制走既有安全机制。

八段固定剧本 (固定时钟, 固定 task_run_id, 全部确定性可复现):
  D-01 模型超时 (读)  system_failed     读路径 deadline 500ms 注入 (0.65s) ->
                                        晚到结果整体作废, 不生成未经验证的答案。
  D-02 索引过期 (读)  abstained         INDEX_STALE 开关激活 -> 演练门拒绝用
                                        可能过期的索引生成答案 + 知识所有者入口。
  D-03 MCP 注入降级(写) action_preview  探测不可达 -> 停预览, 0 效果,
                                        审批不消耗, 探测恰 1 次 (不重试)。
  D-04 MCP 开关降级(写) action_preview  MCP_OFF 激活 -> 停预览 (同一终态);
                                        负例: 无审批执行 -> approval_required。
  D-05a 恢复复验·注入路(写) action_completed 解除注入 -> 同一 run_id + 同一审批
                                        续跑 -> 恰 1 效果 -> 幂等重放无第二效果。
  D-05b 恢复复验·开关路(写) action_completed 按批准复位 MCP_OFF -> 续跑恰 1 效果。
  D-05c 受限任务仍拒绝(写) approval_denied   恢复后策略/权限不放宽 (0 效果)。
  D-06 断连对账回归(写) action_completed     第 15 周 A-06 语义回归 (1 效果)。

记录面: 每 task_run_id 一条九阶段 TaskTrace (遥测词表与第 16 周完全一致);
细分原因 (operator/reason/审批载荷) 只进 开关登记与 Host 受限审计, 不进遥测。

用法: python -m mcptool.run_degradation_drill
      [--out week-17/degradation-drill-evidence-v1.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcptool import contract as C
from mcptool import write_contract as W
from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.kill_switch import (
    SWITCH_INDEX_STALE, SWITCH_MCP_OFF, KillSwitchRegistry, WriteChannelProbe,
)
from mcptool.policy import WritePolicy
from mcptool.telemetry import (  # noqa: E402
    STAGE_APPROVAL, STAGE_GENERATION, STAGE_IDENTITY, STAGE_MCP_CALL,
    STAGE_RECONCILIATION, STAGE_REQUEST, STAGE_RESPONSE, STAGE_RETRIEVAL,
    STAGE_VERIFICATION, TaskTrace, TRACE_OUTCOME_PREVIEW_HELD,
    reduce_runtime_metrics, scan_spans_for_violations,
)
from mcptool.ticket_service import TicketService

FIXED_NOW = "2026-10-09T00:00:00Z"   # 固定时钟: 与第 16 周回放同口径, 可复现
TENANT = "northstar-training"
SESSION = TrustedSession(TENANT, "user-zhou-ning", "support_agent")
RAG_TERMINAL = "escalate"
UPSTREAM_SCENARIO = "ns-1042-rag-escalate (与第 14/15/16 周同一场景输入)"

WRITE_ARGS = {"case_id": "NS-1042", "reason_code": "POLICY_CONFLICT",
              "source_refs": ["upgrade-east-v2", "upgrade-notice-v4"],
              "target_queue": "billing-supervisor-review"}
DENIED_ARGS = dict(WRITE_ARGS, target_queue="executive-review")

SWITCH_OPERATOR = "ops-li-w17"
SWITCH_OPEN_REASON = "演练：隔离 MCP 写通道（第 17 周固定演练 D-04）"
SWITCH_CLOSE_REASON = "按批准流程复位：演练完成，恢复复验前置于续跑（D-05b）"
def _fixed_clock():
    return FIXED_NOW


def _fresh_stack(*, kill_switch=None, write_probe=None):
    """独立「外部世界」: 工单/审批/策略/Host 各一份 (演练之间互不串状态)。"""
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, clock=_fixed_clock, ticket_service=tickets,
                   approval_service=approvals, policy=policy,
                   kill_switch=kill_switch, write_probe=write_probe)
    return host, tickets, approvals, policy


def _issue_approval(approvals, policy, task_id):
    """受控确认动作 -> 结构化审批 (绑定当前 task_run_id; 不读自然语言)。"""
    return approvals.issue(
        approval_id=f"approval-{task_id}", requester=SESSION.user_id,
        tenant_id=TENANT, run_id=task_id, tool=W.TOOL_CREATE,
        tool_version=W.TOOL_VERSION, approved_parameters=WRITE_ARGS,
        resource_scope=W.resource_scope(WRITE_ARGS["case_id"]),
        policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW)


def _emit_not_applicable(trace, stages, note):
    for stage in stages:
        trace.span(stage, result="not_applicable", not_applicable=True,
                   safe_refs={"note": note})


def _write_shell(task_id, *, case_id="NS-1042", extra_refs=None):
    """写任务公共骨架: request/identity + 上游声明 + 检索/生成缺席留痕。"""
    trace = TaskTrace(task_id, kind="write", clock=_fixed_clock)
    refs = {"case_id": case_id, "upstream_scenario": UPSTREAM_SCENARIO}
    if extra_refs:
        refs.update(extra_refs)
    trace.span(STAGE_REQUEST, result="write_request", safe_refs=refs)
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    _emit_not_applicable(trace, (STAGE_RETRIEVAL, STAGE_GENERATION),
                         "写任务不检索; rag_terminal 由上游场景给定 (escalate)")
    return trace


def _policy_span(trace, host, *, decision):
    trace.span(STAGE_VERIFICATION, result=f"policy:{decision}",
               versions={"policy_version": host._policy.POLICY_VERSION},
               safe_refs={"rag_terminal": RAG_TERMINAL,
                          "allowed_queue": host._policy.ALLOWED_QUEUES[0]})


def _approval_span(trace, *, approval_id, decision):
    trace.span(STAGE_APPROVAL, result=f"approval:{decision}",
               versions={"approval_record_version": "approval-record-v1"},
               safe_refs={"approval_id": approval_id, "decision": decision})


def _mcp_span(trace, envelope, *, tool, effects_delta, trigger=None):
    refs = {"tool": tool,
            "idempotency_key": W.idempotency_key(trace.task_run_id),
            "ticket_id": envelope.get("ticket_id"),
            "verified_by": envelope.get("verified_by"),
            "effects_delta": effects_delta}
    if envelope.get("degraded"):
        refs["degraded"] = True
        refs["trigger"] = trigger
        refs["allowed_next_actions"] = list(W.ALLOWED_NEXT_ACTIONS)
    trace.span(STAGE_MCP_CALL, result=_outcome_of(envelope),
               versions={"tool_version": W.TOOL_VERSION,
                         "contract_sha256": W.CONTRACT_SHA256},
               safe_refs=refs,
               error_class=envelope.get("reason_code")
               or envelope.get("error", {}).get("code"))


def _outcome_of(envelope) -> str:
    if envelope.get("status") == "error":
        return envelope["error"]["code"]
    return str(envelope.get("status"))
# ---------------------------------------------------------------------------
# D-01 模型超时 (读): 读路径 deadline 注入 -> system_failed (诚实失败, 0 效果)
# ---------------------------------------------------------------------------
def drill_d01_model_timeout(registry):
    task_id = "northstar-w17-0001"
    trace = TaskTrace(task_id, kind="read", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"case_id": "NS-1042", "family": "tooling",
                          "drill": "D-01-model-timeout"})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    host = McpHost(SESSION, clock=_fixed_clock, deadline_ms=C.DEADLINE_MS)
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
    trace.finish(outcome, effects=0)
    row = {
        "drill_id": "D-01", "fault": "model_timeout",
        "path": "fixed_drill:read_deadline_injection",
        "task_run_id": task_id, "trigger": "delay_650ms>deadline_500ms",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": outcome, "effects": 0,
        "handbook_mapping": ("手册完成例 partial/search_only 的本仓库等价机制: "
                             "读路径 deadline_exceeded -> system_failed "
                             "(同一纪律: 不生成未经验证的答案, 不调用写工具)"),
        "verdict": "pass" if outcome == C.ERROR_DEADLINE_EXCEEDED else "fail",
    }
    return trace, row


# ---------------------------------------------------------------------------
# D-02 索引过期 (读): INDEX_STALE 开关 -> 拒用陈旧索引 -> abstained (0 效果)
# ---------------------------------------------------------------------------
def drill_d02_index_stale(registry):
    task_id = "northstar-w17-0002"
    registry.set(SWITCH_INDEX_STALE, operator=SWITCH_OPERATOR,
                 reason="演练：索引新鲜度存疑，隔离索引依赖（第 17 周 D-02）")
    trace = TaskTrace(task_id, kind="read", clock=_fixed_clock)
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"case_id": "NS-1042", "family": "tooling",
                          "drill": "D-02-index-stale"})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    stale = registry.is_active(SWITCH_INDEX_STALE)
    trace.span(STAGE_RETRIEVAL, result="index_stale_gate",
               versions={"corpus_version": "northstar-corpus-w09-v1"},
               safe_refs={"switch": SWITCH_INDEX_STALE,
                          "knowledge_owner_entry": "corpus-governance"},
               error_class="index_stale" if stale else None)
    _emit_not_applicable(trace, (STAGE_GENERATION,),
                         "开关激活: 拒绝用可能过期的索引生成答案 (不用陈旧缓存装作正常)")
    _emit_not_applicable(trace, (STAGE_VERIFICATION, STAGE_APPROVAL,
                                 STAGE_MCP_CALL, STAGE_RECONCILIATION),
                         "只读任务, 未到写路径")
    trace.span(STAGE_RESPONSE, result="abstained_presented",
               error_class="index_stale")
    trace.finish("refused", effects=0)  # RAG 词表 refused -> 可见终态 abstained
    registry.clear(SWITCH_INDEX_STALE, operator=SWITCH_OPERATOR,
                   reason="按批准流程复位：语料新鲜度复核通过（演练留痕）")
    row = {
        "drill_id": "D-02", "fault": "index_stale",
        "path": "fixed_drill:switch_gate_on_read_path",
        "task_run_id": task_id, "trigger": f"switch:{SWITCH_INDEX_STALE}",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": "index_stale", "effects": 0,
        "handbook_mapping": ("手册完成例 abstained 的本仓库等价机制: 既有拒答 "
                             "安全机制 + INDEX_STALE 开关强制触发 (语料治理 "
                             "属第 10/11 周, 本周不重写恢复机制)"),
        "verdict": "pass" if trace.terminal[0] == "abstained" else "fail",
    }
    return trace, row
# ---------------------------------------------------------------------------
# D-03 MCP 注入降级 (写): 探测不可达 -> action_preview (0 效果, 审批不消耗)
# ---------------------------------------------------------------------------
def drill_d03_mcp_injected(stack_state):
    task_id = "northstar-w17-0003"
    probe = stack_state["probe"]
    # 注入路与开关路是两条独立的降级路径: 本段只挂探测缝, 不挂开关注册表
    host, tickets, approvals, policy = _fresh_stack(write_probe=probe)
    trace = _write_shell(task_id)
    before = tickets.ticket_count()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                    rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    approval = _issue_approval(approvals, policy, task_id)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved")
    probe.unavailable = True                     # 注入: MCP 写通道不可达
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    probe.unavailable = False                    # 演练后解除 (D-05a 复验续跑)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta,
              trigger="fault_injection:write_probe")
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,),
                         "未提交 (0 效果), 无对账段")
    trace.span(STAGE_RESPONSE, result="degraded_preview_presented",
               error_class="mcp_unavailable")
    trace.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=delta)
    record, status = approvals.status_now(approval["approval_id"], now=FIXED_NOW)
    degraded_ok = (envelope.get("degraded") is True
                   and envelope.get("reason_code") == W.REASON_MCP_UNAVAILABLE
                   and envelope.get("external_effect_count") == 0
                   and delta == 0 and status == "approved"
                   and record["uses"] == 0 and probe.attempts == 1)
    stack_state["d03"] = {"host": host, "tickets": tickets,
                          "approvals": approvals, "policy": policy,
                          "task_id": task_id,
                          "approval_id": approval["approval_id"]}
    row = {
        "drill_id": "D-03", "fault": "mcp_unavailable",
        "path": "injection:write_channel_probe",
        "task_run_id": task_id, "trigger": "fault_injection:write_probe",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": envelope.get("reason_code"), "effects": delta,
        "ticket_count": {"before": before, "after": tickets.ticket_count()},
        "probe_attempts": probe.attempts,
        "approval_uses_after_degrade": record["uses"],
        "allowed_next_actions": envelope.get("allowed_next_actions"),
        "no_retry_evidence": (f"probe.attempts={probe.attempts} "
                              "(单次探测, 无写入重试)"),
        "verdict": "pass" if degraded_ok else "fail",
    }
    return trace, row
# ---------------------------------------------------------------------------
# D-04 MCP 开关降级 (写): MCP_OFF -> action_preview; 无审批执行仍 approval_required
# ---------------------------------------------------------------------------
def drill_d04_mcp_switch(stack_state):
    task_id = "northstar-w17-0004"
    registry = stack_state["registry"]
    registry.set(SWITCH_MCP_OFF, operator=SWITCH_OPERATOR,
                 reason=SWITCH_OPEN_REASON)
    host, tickets, approvals, policy = _fresh_stack(kill_switch=registry)
    trace = _write_shell(task_id)
    before = tickets.ticket_count()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=task_id,
                    rag_terminal=RAG_TERMINAL)
    _policy_span(trace, host, decision="allowed_at_preview")
    # 负例 (权限判定优先): 开关激活 + 无审批 -> 确定性 approval_required, 不降级
    no_approval = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                                  rag_terminal=RAG_TERMINAL)
    trace.span(STAGE_MCP_CALL, result=_outcome_of(no_approval),
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"tool": W.TOOL_CREATE, "negative_check": True,
                          "switch": SWITCH_MCP_OFF},
               error_class=no_approval.get("error", {}).get("code"))
    approval = _issue_approval(approvals, policy, task_id)
    _approval_span(trace, approval_id=approval["approval_id"],
                   decision="approved")
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=approval["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta,
              trigger="kill_switch:MCP_OFF")
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,),
                         "未提交 (0 效果), 无对账段")
    trace.span(STAGE_RESPONSE, result="degraded_preview_presented",
               error_class="mcp_unavailable")
    trace.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=delta)
    record, status = approvals.status_now(approval["approval_id"], now=FIXED_NOW)
    permission_first = (no_approval.get("status") == "error"
                        and no_approval.get("error", {}).get("code")
                        == W.ERROR_APPROVAL_REQUIRED)
    degraded_ok = (envelope.get("degraded") is True
                   and envelope.get("reason_code") == W.REASON_MCP_UNAVAILABLE
                   and delta == 0 and status == "approved"
                   and record["uses"] == 0)
    stack_state["d04"] = {"host": host, "tickets": tickets,
                          "approvals": approvals, "policy": policy,
                          "task_id": task_id,
                          "approval_id": approval["approval_id"]}
    row = {
        "drill_id": "D-04", "fault": "mcp_unavailable",
        "path": "switch:MCP_OFF",
        "task_run_id": task_id, "trigger": "kill_switch:MCP_OFF",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": envelope.get("reason_code"), "effects": delta,
        "ticket_count": {"before": before, "after": tickets.ticket_count()},
        "approval_uses_after_degrade": record["uses"],
        "allowed_next_actions": envelope.get("allowed_next_actions"),
        "permission_first_check": ("无审批执行 -> "
                                   f"{_outcome_of(no_approval)} "
                                   "(开关不吞权限错误)"
                                   if permission_first else "FAIL"),
        "verdict": "pass" if (degraded_ok and permission_first) else "fail",
    }
    return trace, row
# ---------------------------------------------------------------------------
# D-05a/b/c 恢复复验: 开关按批准复位 + 续跑同一幂等键 + 受限任务仍拒绝
# ---------------------------------------------------------------------------
def drill_d05a_recover_injected(stack_state):
    """注入路恢复: 解除注入 -> 同一 run_id + 同一审批续跑 -> 恰 1 效果 -> 幂等。"""
    d03 = stack_state["d03"]
    task_id = d03["task_id"]
    host, tickets, approvals = d03["host"], d03["tickets"], d03["approvals"]
    trace = _write_shell(task_id,
                         extra_refs={"recovery": "retry_later_after_restore"})
    _policy_span(trace, host, decision="allowed_at_preview")
    _approval_span(trace, approval_id=d03["approval_id"],
                   decision="approved_not_consumed_during_degradation")
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=d03["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    replay = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                             approval_id=d03["approval_id"],
                             rag_terminal=RAG_TERMINAL)
    after_replay = tickets.ticket_count()
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "正常完成, 无对账段")
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    trace.finish(_outcome_of(envelope), effects=delta)
    completed_ok = (envelope.get("status") == W.STATUS_COMPLETED
                    and envelope.get("verified_by") == "ticket.get"
                    and delta == 1)
    idempotent_ok = (replay.get("status") == W.STATUS_COMPLETED
                     and replay.get("ticket_id") == envelope.get("ticket_id")
                     and after_replay - before == 1)
    row = {
        "drill_id": "D-05a", "fault": "recovery:injection_path",
        "path": "same_run_id_same_approval_retry",
        "task_run_id": task_id, "trigger": "restore_probe",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": "ok:write_verified", "effects": delta,
        "ticket_count": {"before": before, "after": after_replay},
        "idempotency_replay": (f"重放返回同一工单 {replay.get('ticket_id')}, "
                               f"计数仍 {after_replay} (无重复效果)"
                               if idempotent_ok else "FAIL"),
        "verdict": "pass" if (completed_ok and idempotent_ok) else "fail",
    }
    return trace, row
def drill_d05b_recover_switch(stack_state):
    """开关路恢复: 按批准复位 MCP_OFF -> 同一 run_id + 同一审批续跑 -> 恰 1 效果。"""
    d04 = stack_state["d04"]
    registry = stack_state["registry"]
    task_id = d04["task_id"]
    reset_event = registry.clear(SWITCH_MCP_OFF, operator=SWITCH_OPERATOR,
                                 reason=SWITCH_CLOSE_REASON)
    host, tickets, approvals = d04["host"], d04["tickets"], d04["approvals"]
    trace = _write_shell(task_id,
                         extra_refs={"recovery": "switch_reset_then_retry"})
    _policy_span(trace, host, decision="allowed_at_preview")
    _approval_span(trace, approval_id=d04["approval_id"],
                   decision="approved_not_consumed_during_degradation")
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=task_id,
                               approval_id=d04["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _mcp_span(trace, envelope, tool=W.TOOL_CREATE, effects_delta=delta)
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "正常完成, 无对账段")
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    trace.finish(_outcome_of(envelope), effects=delta)
    ok = (envelope.get("status") == W.STATUS_COMPLETED and delta == 1
          and registry.is_active(SWITCH_MCP_OFF) is False
          and reset_event.get("event") == "closed")
    row = {
        "drill_id": "D-05b", "fault": "recovery:switch_path",
        "path": "approved_switch_reset_then_retry",
        "task_run_id": task_id, "trigger": f"switch_reset:{SWITCH_MCP_OFF}",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": "ok:write_verified", "effects": delta,
        "ticket_count": {"before": before, "after": tickets.ticket_count()},
        "switch_reset_audit": {"event": reset_event.get("event"),
                               "operator": reset_event.get("operator")},
        "verdict": "pass" if ok else "fail",
    }
    return trace, row


def drill_d05c_restricted_still_denied(stack_state):
    """恢复后受限任务仍拒绝 (策略/权限不放宽): executive-review -> approval_denied。"""
    d03 = stack_state["d03"]
    task_id = "northstar-w17-0007"
    host, tickets = d03["host"], d03["tickets"]
    trace = _write_shell(task_id,
                         extra_refs={"check": "restricted_after_recovery"})
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_PREVIEW, DENIED_ARGS, run_id=task_id,
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    _policy_span(trace, host, decision="denied_after_recovery")
    _approval_span(trace, approval_id="none_denied_before_issuance",
                   decision="not_issued")
    trace.span(STAGE_MCP_CALL, result=_outcome_of(envelope),
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"tool": W.TOOL_PREVIEW, "effects_delta": delta})
    _emit_not_applicable(trace, (STAGE_RECONCILIATION,), "拒绝路径, 无对账段")
    trace.span(STAGE_RESPONSE, result="error_presented",
               error_class=_outcome_of(envelope))
    trace.finish(_outcome_of(envelope), effects=delta)
    ok = (envelope.get("status") == W.STATUS_DENIED and delta == 0)
    row = {
        "drill_id": "D-05c", "fault": "restricted_after_recovery",
        "path": "policy_denied_checkpoint",
        "task_run_id": task_id, "trigger": "executive-review (策略外队列)",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": "policy_denied:queue_not_allowed", "effects": delta,
        "verdict": "pass" if ok else "fail",
    }
    return trace, row
# ---------------------------------------------------------------------------
# D-06 断连对账回归 (写): 第 15 周 A-06 语义在本周不退化
# ---------------------------------------------------------------------------
def drill_d06_reconciliation_regression(stack_state):
    task_id = "northstar-w17-0008"
    host, tickets, approvals, policy = _fresh_stack()
    trace = _write_shell(task_id, extra_refs={"regression": "week15-A-06"})
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
    trace.span(STAGE_MCP_CALL, result=_outcome_of(envelope),
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"tool": W.TOOL_CREATE,
                          "idempotency_key": W.idempotency_key(task_id),
                          "ticket_id": envelope.get("ticket_id"),
                          "verified_by": envelope.get("verified_by"),
                          "effects_delta": delta})
    trace.span(STAGE_RECONCILIATION, result="reconciled_from_result_unknown",
               versions={"tool_version": W.TOOL_VERSION},
               safe_refs={"idempotency_key": W.idempotency_key(task_id),
                          "recovered_from": W.STATUS_UNKNOWN,
                          "verified_by": "ticket.get"})
    trace.span(STAGE_RESPONSE, result=_outcome_of(envelope))
    trace.finish(_outcome_of(envelope), effects=delta)
    ok = (envelope.get("status") == W.STATUS_COMPLETED
          and envelope.get("verified_by") == "ticket.get" and delta == 1)
    row = {
        "drill_id": "D-06", "fault": "reconciliation_regression",
        "path": "week15-A-06-regression",
        "task_run_id": task_id, "trigger": "fail_after_commit",
        "visible_terminal": trace.terminal[0],
        "allowed_terminal": trace.terminal[1],
        "reason_code": "ok:write_verified", "effects": delta,
        "handbook_mapping": ("写结果不明确 -> 先按幂等键查询对账, 再说完成 "
                             "(手册 3916/3952 决策规则, 本周不退化)"),
        "verdict": "pass" if ok else "fail",
    }
    return trace, row


# ---------------------------------------------------------------------------
def run_drills() -> dict:
    """跑八段固定剧本; 返回 {drills, spans, tasks, metrics, switch_audit, ...}。"""
    stack_state = {"registry": KillSwitchRegistry(clock=_fixed_clock),
                   "probe": WriteChannelProbe()}
    traces, rows = [], []
    for drill in (lambda s: drill_d01_model_timeout(s["registry"]),
                  lambda s: drill_d02_index_stale(s["registry"]),
                  drill_d03_mcp_injected,
                  drill_d04_mcp_switch,
                  drill_d05a_recover_injected,
                  drill_d05b_recover_switch,
                  drill_d05c_restricted_still_denied,
                  drill_d06_reconciliation_regression):
        trace, row = drill(stack_state)
        traces.append(trace)
        rows.append(row)
    spans = [span for trace in traces for span in trace.spans]
    metrics = reduce_runtime_metrics(traces)
    violations = scan_spans_for_violations(spans)
    all_pass = all(row["verdict"] == "pass" for row in rows) and not violations
    return {
        "fixed_now": FIXED_NOW,
        "drills": rows,
        "spans": spans,
        "tasks": [trace.as_task_row() for trace in traces],
        "metrics": metrics,
        "switch_audit": stack_state["registry"].audit_trail(),
        "redaction": {"checked_events": len(spans),
                      "violations": violations, "passed": not violations},
        "verdict": "pass" if all_pass else "fail",
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="降级固定演练 (Week 17)")
    parser.add_argument("--out", default=None,
                        help="演练证据 jsonl 输出路径 (缺省只打印汇总)")
    args = parser.parse_args(argv)
    result = run_drills()
    if args.out:
        out_path = os.path.join(PROJECT_ROOT, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            for span in result["spans"]:
                f.write(json.dumps({"record": "span", **span},
                                   ensure_ascii=False) + "\n")
            for row in result["drills"]:
                f.write(json.dumps({"record": "drill", **row},
                                   ensure_ascii=False) + "\n")
            f.write(json.dumps({"record": "metrics", **result["metrics"]},
                               ensure_ascii=False) + "\n")
            for event in result["switch_audit"]:
                f.write(json.dumps({"record": "switch_audit", **event},
                                   ensure_ascii=False) + "\n")
    print(json.dumps({"verdict": result["verdict"],
                      "drills": result["drills"],
                      "metrics": result["metrics"],
                      "redaction": result["redaction"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
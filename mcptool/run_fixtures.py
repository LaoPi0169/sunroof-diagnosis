"""夹具运行器 (Week 13 第 4 天 R/X + Week 14 第 4 天 W) —— 逐行 JSON 结果。

用法: python -m mcptool.run_fixtures [--suite read|write|all] [--only R-03]
退出码: 全部通过 0; 任一失败 1。
确定性: read_at/now 用固定时钟 (2026-10-08T00:00:00Z), 输出可复现
(demo_lookup 用真实时钟; demo_write_approval 用固定时钟)。
效果口径: 只读夹具外部写效果恒 0 (工单库快照逐字节一致); 写夹具逐条
对账外部效果增量 —— 期望 [W-01:0, W-02:0, W-03:0, W-04:1, W-05:0],
合计恰 1 (唯一成功路径 W-04 在隔离模拟服务创建 1 张复核单)。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import case_store
from . import contract as C
from . import write_contract as W
from .approval import ApprovalService
from .client import McpHost, TrustedSession
from .policy import WritePolicy
from .ticket_service import TicketService

FIXED_NOW = "2026-10-08T00:00:00Z"
AGENT_SESSION = TrustedSession("northstar-training", "user-zhou-ning", "support_agent")
SUPERVISOR_SESSION = TrustedSession(
    "northstar-training", "user-gao-supervisor", "supervisor")
THREE_FIELDS = list(C.FIELD_ENUM)
FIXTURE_IDS = ("R-01", "R-02", "R-03", "R-04", "R-05", "X-01", "X-02", "X-03")


def _user_json(envelope: dict) -> str:
    """用户面封套规范序列化 (逐字节一致性断言用)。"""
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False)


def _result(fixture_id, condition, expected, observed, passed) -> dict:
    return {"fixture_id": fixture_id, "condition": condition,
            "expected": expected, "observed": observed, "pass": bool(passed),
            "external_write_effects": 0}


def run_r01() -> dict:
    """R-01 正常路径: 一线读本租户普通工单 -> ok, 恰好请求字段, 带来源元数据。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call({"case_id": "NS-1042", "fields": THREE_FIELDS})
    record = envelope["records"][0]
    raw = _user_json(envelope)
    checks = {
        "status_ok": envelope["status"] == "ok",
        "one_record": len(envelope["records"]) == 1,
        "exactly_requested_fields": sorted(record["context"]) == sorted(C.FIELD_ENUM),
        "record_version_present": record["source"]["record_version"] == "case-v7",
        "read_at_present": bool(record["source"]["read_at"]),
        "forbidden_content_absent": all(
            token not in raw
            for token in C.FORBIDDEN_FIELDS + ("主管备注", "客户姓名", "支付明细")),
        "trusted_keys_absent": all(k not in raw for k in C.TRUSTED_CONTEXT_KEYS),
        "zero_write_effects": host.external_write_effects == 0,
    }
    return _result(
        "R-01", "support_agent 读本租户普通工单 NS-1042, 请求 3 个枚举字段",
        "ok; 1 条记录; 恰好 task_type/region/purchased_at; 无受限内容; 写效果 0",
        {"status": envelope["status"], "context": record["context"],
         "record_version": record["source"]["record_version"],
         "read_at": record["source"]["read_at"], "checks": checks},
        all(checks.values()))


def run_r02() -> dict:
    """R-02 无权限字段不可得: 请求 supervisor_note -> schema 层 invalid_argument。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call(
        {"case_id": "NS-1042", "fields": ["task_type", "supervisor_note"]})
    raw = _user_json(envelope)
    checks = {
        "invalid_argument": envelope.get("error", {}).get("code") == C.ERROR_INVALID_ARGUMENT,
        "safe_message": (envelope.get("error", {}).get("message")
                         == C.SAFE_MESSAGES[C.ERROR_INVALID_ARGUMENT]),
        "no_note_content": "主管备注" not in raw and "supervisor_note" not in raw,
        "no_records": "records" not in envelope,
        "detail_only_in_audit": host.audit[0]["reason_typed"].startswith("schema:"),
    }
    return _result(
        "R-02", "请求字段含 supervisor_note (禁止字段, 不在枚举)",
        "invalid_argument; 安全文案; 响应不含备注内容; 细节只进受限审计",
        {"error": envelope.get("error"), "checks": checks},
        all(checks.values()))


def run_r03() -> dict:
    """R-03 不可区分性: 不存在/跨租户/角色不可见 -> 用户面响应逐字节一致。"""
    targets = (("nonexistent", case_store.NONEXISTENT_CASE_ID),
               ("cross_tenant", "NS-3001"),
               ("role_invisible", "NS-5001"))
    payloads, reasons = {}, {}
    for label, case_id in targets:
        host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
        envelope = host.call({"case_id": case_id, "fields": ["task_type"]})
        payloads[label] = _user_json(envelope)
        reasons[label] = host.audit[0]["reason_typed"]
    typed_reasons = sorted(reasons.values())
    checks = {
        "all_not_available": all(
            "not_available" in payload for payload in payloads.values()),
        "user_facing_identical": len(set(payloads.values())) == 1,
        "typed_reasons_distinct_in_audit": len(set(typed_reasons)) == 3,
        "no_reason_leak_to_user": all(
            reason not in payload
            for reason in typed_reasons for payload in payloads.values()),
    }
    return _result(
        "R-03", "不存在 NS-9999 / 跨租户 NS-3001 / 角色不可见 NS-5001 (一线请求)",
        "三者 not_available 且用户面逐字节一致; 细分原因只进受限审计",
        {"user_facing_payload": next(iter(payloads.values())),
         "typed_reasons_restricted": reasons, "checks": checks},
        all(checks.values()))

def run_r04() -> dict:
    """R-04 超时: 慢服务端 (0.65s) 超过 deadline 500ms -> deadline_exceeded。"""
    deadline_ms, delay_seconds = C.DEADLINE_MS, 0.65
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW, deadline_ms=deadline_ms)
    envelope = host.call({"case_id": "NS-1042", "fields": THREE_FIELDS},
                         delay_seconds=delay_seconds)
    raw = _user_json(envelope)
    checks = {
        "deadline_exceeded": (envelope.get("error", {}).get("code")
                              == C.ERROR_DEADLINE_EXCEEDED),
        "no_context_fields": all(token not in raw for token in (
            "plan_upgrade_price_difference", "2026-08-11", '"CN"')),
        "no_records": "records" not in envelope,
        "elapsed_recorded_in_audit": host.audit[0]["reason_typed"].startswith("elapsed_ms:"),
    }
    return _result(
        "R-04", f"服务端延迟 {delay_seconds}s > deadline {deadline_ms}ms",
        "deadline_exceeded; 响应不含任何上下文字段; 超时细节只进受限审计",
        {"error": envelope.get("error"),
         "elapsed_typed": host.audit[0]["reason_typed"], "checks": checks},
        all(checks.values()))


def run_r05() -> dict:
    """R-05 超合同结果: 失控服务端返回 2 条记录 -> 整体拒绝 + 安全事件。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call({"case_id": "NS-1042", "fields": THREE_FIELDS},
                         rogue_mode="two_records")
    raw = _user_json(envelope)
    events = host.security_events
    checks = {
        "contract_violation": (envelope.get("error", {}).get("code")
                               == C.ERROR_CONTRACT_VIOLATION),
        "no_records_accepted": "records" not in envelope,
        "violating_data_absent": "2026-08-11" not in raw,
        "security_event_recorded": len(events) == 1 and events[0][
            "violation"].startswith("records_exceed_limit:2>1"),
    }
    return _result(
        "R-05", "失控服务端单次返回 2 条记录 (超 result_limit.records=1)",
        "contract_violation; 超合同结果整体拒绝并记安全事件",
        {"error": envelope.get("error"),
         "security_events_restricted": events, "checks": checks},
        all(checks.values()))


def run_x01() -> dict:
    """X-01 身份走私: 参数携带 tenant_id/role -> Host 层拒绝, 服务端零调用。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call({"case_id": "NS-1042", "fields": THREE_FIELDS,
                          "tenant_id": "partner-tenant-co",
                          "role": "supervisor"})
    checks = {
        "invalid_argument": (envelope.get("error", {}).get("code")
                             == C.ERROR_INVALID_ARGUMENT),
        "server_not_called": host.server_calls == 0,
        "identity_not_granted": host.audit[0]["reason_typed"].startswith(
            "schema:tenant_id"),
        "no_records": "records" not in envelope,
    }
    return _result(
        "X-01", "参数走私 tenant_id/role (身份只能来自可信会话)",
        "invalid_argument; 未到达服务端; 会话身份未被覆盖",
        {"error": envelope.get("error"), "server_calls": host.server_calls,
         "checks": checks},
        all(checks.values()))


def run_x02() -> dict:
    """X-02 未知字段: 请求枚举外字段 nickname -> invalid_argument。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call({"case_id": "NS-1042",
                          "fields": ["task_type", "nickname"]})
    checks = {
        "invalid_argument": (envelope.get("error", {}).get("code")
                             == C.ERROR_INVALID_ARGUMENT),
        "server_not_called": host.server_calls == 0,
        "no_records": "records" not in envelope,
    }
    return _result(
        "X-02", "请求字段含 nickname (封闭枚举外)",
        "invalid_argument; 未到达服务端",
        {"error": envelope.get("error"), "server_calls": host.server_calls,
         "checks": checks},
        all(checks.values()))


def run_x03() -> dict:
    """X-03 版本不匹配: 失控服务端报 tool_version=2 -> version_mismatch。"""
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
    envelope = host.call({"case_id": "NS-1042", "fields": THREE_FIELDS},
                         rogue_mode="wrong_version")
    checks = {
        "version_mismatch": (envelope.get("error", {}).get("code")
                             == C.ERROR_VERSION_MISMATCH),
        "no_records": "records" not in envelope,
        "audit_records_version": host.audit[0]["reason_typed"] == "tool_version:2",
    }
    return _result(
        "X-03", "失控服务端返回 tool_version=2 (合同 pin 1)",
        "version_mismatch; 结果整体作废",
        {"error": envelope.get("error"), "checks": checks},
        all(checks.values()))


RUNNERS = {
    "R-01": run_r01, "R-02": run_r02, "R-03": run_r03,
    "R-04": run_r04, "R-05": run_r05,
    "X-01": run_x01, "X-02": run_x02, "X-03": run_x03,
}


# ===========================================================================
# Week 14 写负向夹具 W-01..W-05 (手册 3375-3381; 每夹具独立隔离模拟服务)
# ===========================================================================

WRITE_FIXTURE_IDS = ("W-01", "W-02", "W-03", "W-04", "W-05")
RUN_ID_W14 = "run-w14-001"
APPROVAL_ID_W14 = "approval-w14-001-1"
DENIAL_ID_W14 = "denial-w14-001-1"
# 乱序 + 尾随空格输入: 证明规范化 (排序/去空格) 在预览/审批/执行三处一致
WRITE_ARGS = {
    "case_id": "NS-1042",
    "reason_code": "POLICY_CONFLICT",
    "source_refs": ["upgrade-notice-v4 ", " upgrade-east-v2"],
    "target_queue": "billing-supervisor-review",
}


def _fresh_write_stack():
    """每个写夹具独立栈: 独立模拟服务/审批/策略实例, 运行互不串状态。"""
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW,
                   ticket_service=tickets, approval_service=approvals,
                   policy=policy)
    return host, tickets, approvals, policy


def _canonical_args():
    canonical, err = W.validate_write_arguments(WRITE_ARGS)
    if err:  # 夹具输入自身必须合法
        raise SystemExit(f"夹具参数非法: {err}")
    return canonical


def _issue_exact_approval(approvals, policy, *, issued_at=FIXED_NOW):
    """模拟用户在确认界面看到准确参数后选择「确认创建主管复核单」。"""
    return approvals.issue(
        approval_id=APPROVAL_ID_W14, requester=AGENT_SESSION.user_id,
        tenant_id=AGENT_SESSION.tenant_id, run_id=RUN_ID_W14,
        tool=W.TOOL_CREATE, tool_version=W.TOOL_VERSION,
        approved_parameters=_canonical_args(),
        resource_scope=W.resource_scope("NS-1042"),
        policy_version=policy.POLICY_VERSION, issued_at=issued_at)


def _w_result(fixture_id, condition, expected, observed, passed,
              count_before, count_after) -> dict:
    return {"fixture_id": fixture_id, "condition": condition,
            "expected": expected, "observed": observed, "pass": bool(passed),
            "external_write_effects": count_after - count_before,
            "external_ticket_count": {"before": count_before,
                                      "after": count_after}}


def run_w01() -> dict:
    """W-01 无审批直接执行: 预览后跳过用户确认 -> approval_required, 0 效果。"""
    host, tickets, _approvals, _policy = _fresh_write_stack()
    preview = host.call_write(W.TOOL_PREVIEW, WRITE_ARGS,
                              run_id=RUN_ID_W14, rag_terminal="escalate")
    count_before = tickets.ticket_count()
    envelope = host.call_write(
        W.TOOL_CREATE, WRITE_ARGS, run_id=RUN_ID_W14, approval_id=None,
        rag_terminal="escalate")
    count_after = tickets.ticket_count()
    checks = {
        "preview_zero_effect": (preview.get("status") == W.STATUS_PREVIEW
                                and preview.get("external_ticket_count") == 0),
        "approval_required": (envelope.get("error", {}).get("code")
                              == W.ERROR_APPROVAL_REQUIRED),
        "no_effects": count_before == 0 and count_after == 0,
        "no_ticket_id": "ticket_id" not in envelope,
        "audit_reason": host.audit[-1]["reason_typed"] == "approval_id_missing",
    }
    return _w_result(
        "W-01", "没有审批，直接执行 (预览后跳过用户确认)",
        "approval_required; 新增外部效果 0",
        {"preview_status": preview.get("status"),
         "error": envelope.get("error"), "checks": checks},
        all(checks.values()), count_before, count_after)


def run_w02() -> dict:
    """W-02 审批已过期: 签发于 2 小时前 (TTL 900s) -> approval_expired, 0 效果。"""
    host, tickets, approvals, policy = _fresh_write_stack()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS,
                    run_id=RUN_ID_W14, rag_terminal="escalate")
    expired_record = _issue_exact_approval(
        approvals, policy, issued_at="2026-10-07T22:00:00Z")
    count_before = tickets.ticket_count()
    envelope = host.call_write(
        W.TOOL_CREATE, WRITE_ARGS, run_id=RUN_ID_W14,
        approval_id=APPROVAL_ID_W14, rag_terminal="escalate")
    count_after = tickets.ticket_count()
    checks = {
        "approval_was_approved_once": expired_record["decision"] == "approved",
        "approval_expired": (envelope.get("error", {}).get("code")
                             == W.ERROR_APPROVAL_EXPIRED),
        "no_effects": count_before == 0 and count_after == 0,
        "audit_reason": host.audit[-1]["reason_typed"] == "approval_expired",
        "safe_message_only": "expires_at" not in json.dumps(envelope),
    }
    return _w_result(
        "W-02", "审批已过期 (签发 2026-10-07T22:00:00Z, TTL 900s, 现在 00:00:00Z)",
        "approval_expired; 新增外部效果 0",
        {"error": envelope.get("error"), "checks": checks},
        all(checks.values()), count_before, count_after)


def run_w03() -> dict:
    """W-03 审批后改变队列: executive-review 语法合法但绑定不符 -> mismatch, 0 效果。"""
    host, tickets, approvals, policy = _fresh_write_stack()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS,
                    run_id=RUN_ID_W14, rag_terminal="escalate")
    _issue_exact_approval(approvals, policy)
    tampered = dict(WRITE_ARGS, target_queue="executive-review")
    count_before = tickets.ticket_count()
    envelope = host.call_write(
        W.TOOL_CREATE, tampered, run_id=RUN_ID_W14,
        approval_id=APPROVAL_ID_W14, rag_terminal="escalate")
    count_after = tickets.ticket_count()
    checks = {
        "approval_mismatch": (envelope.get("error", {}).get("code")
                              == W.ERROR_APPROVAL_MISMATCH),
        "binding_reason": (host.audit[-1]["reason_typed"]
                           == "binding:approved_parameters"),
        "no_effects": count_before == 0 and count_after == 0,
        "no_ticket_id": "ticket_id" not in envelope,
        "original_approval_intact": approvals.get(APPROVAL_ID_W14)["uses"] == 0,
    }
    return _w_result(
        "W-03", "审批后把目标队列改成 executive-review (使用原审批)",
        "approval_mismatch; 新增外部效果 0",
        {"error": envelope.get("error"), "checks": checks},
        all(checks.values()), count_before, count_after)


def run_w04() -> dict:
    """W-04 相同幂等键重复十次: 首次创建 SIM-5001, 重放一律返回原工单, 总数 1。"""
    host, tickets, approvals, policy = _fresh_write_stack()
    host.call_write(W.TOOL_PREVIEW, WRITE_ARGS,
                    run_id=RUN_ID_W14, rag_terminal="escalate")
    _issue_exact_approval(approvals, policy)
    first = host.call_write(
        W.TOOL_CREATE, WRITE_ARGS, run_id=RUN_ID_W14,
        approval_id=APPROVAL_ID_W14, rag_terminal="escalate")
    count_after_first = tickets.ticket_count()
    replay_ids = set()
    replay_states = set()
    for _ in range(10):
        replay = host.call_write(
            W.TOOL_CREATE, WRITE_ARGS, run_id=RUN_ID_W14,
            approval_id=APPROVAL_ID_W14, rag_terminal="escalate")
        replay_ids.add(replay.get("ticket_id"))
        replay_states.add(replay.get("ticket_state"))
    count_final = tickets.ticket_count()
    checks = {
        "created_once": (first.get("status") == W.STATUS_COMPLETED
                         and count_after_first == 1),
        "ticket_id_sim": str(first.get("ticket_id", "")).startswith("SIM-"),
        "verified_by_get": first.get("verified_by") == "ticket.get",
        "idempotency_key_format": (first.get("idempotency_key")
                                   == "run-w14-001:ticket.create:1"),
        "replay_same_ticket": replay_ids == {first.get("ticket_id")},
        "replay_state_stable": replay_states == {"open"},
        "no_extra_effects": count_final == 1,
        "approval_used_once": approvals.get(APPROVAL_ID_W14)["uses"] == 1,
    }
    return _w_result(
        "W-04", "完全匹配后执行, 再以相同幂等键重复执行十次",
        "创建 1 张复核单; 重放返回同一工单; 新增外部效果合计 1",
        {"first": {"status": first.get("status"),
                   "ticket_id": first.get("ticket_id"),
                   "idempotency_key": first.get("idempotency_key"),
                   "ticket_state": first.get("ticket_state"),
                   "verified_by": first.get("verified_by")},
         "replays": 10, "checks": checks},
        all(checks.values()), 0, count_final)


def run_w05() -> dict:
    """W-05 用户拒绝: approval_denied 是正常安全终态; 事后执行仍 approval_required。"""
    host, tickets, approvals, _policy = _fresh_write_stack()
    preview = host.call_write(W.TOOL_PREVIEW, WRITE_ARGS,
                              run_id=RUN_ID_W14, rag_terminal="escalate")
    denial = approvals.record_denial(
        denial_id=DENIAL_ID_W14, requester=AGENT_SESSION.user_id,
        tenant_id=AGENT_SESSION.tenant_id, run_id=RUN_ID_W14,
        proposed_parameters=_canonical_args(), decided_at=FIXED_NOW)
    count_before = tickets.ticket_count()
    # 编排器不得因拒绝自动重问或改道执行; 这里验证即便违规执行也被拒
    envelope = host.call_write(
        W.TOOL_CREATE, WRITE_ARGS, run_id=RUN_ID_W14,
        approval_id=DENIAL_ID_W14, rag_terminal="escalate")
    count_after = tickets.ticket_count()
    checks = {
        "preview_zero_effect": (preview.get("status") == W.STATUS_PREVIEW
                                and preview.get("external_ticket_count") == 0),
        "denial_recorded": denial["decision"] == "denied",
        "denial_not_executable": (envelope.get("error", {}).get("code")
                                  == W.ERROR_APPROVAL_REQUIRED),
        "no_effects": count_before == 0 and count_after == 0,
        "no_ticket_id": "ticket_id" not in envelope,
        "audit_reason": (host.audit[-1]["reason_typed"]
                         == "approval_decision_denied"),
    }
    return _w_result(
        "W-05", "用户在确认界面拒绝 (approval_denied), 随后违规执行尝试",
        "拒绝为正常终态; 执行被拒; 新增外部效果 0",
        {"denial_decision": denial["decision"],
         "error": envelope.get("error"), "checks": checks},
        all(checks.values()), count_before, count_after)


WRITE_RUNNERS = {
    "W-01": run_w01, "W-02": run_w02, "W-03": run_w03,
    "W-04": run_w04, "W-05": run_w05,
}


def run_write_all(only=None):
    """运行写负向夹具; 返回 (逐行结果, 总结)。期望新增外部效果合计恰 1 (W-04)。"""
    if only:
        if only not in WRITE_FIXTURE_IDS:
            raise SystemExit(
                f"未知写夹具: {only} (可选: {', '.join(WRITE_FIXTURE_IDS)})")
        ids = (only,)
    else:
        ids = WRITE_FIXTURE_IDS
    results = [WRITE_RUNNERS[fixture_id]() for fixture_id in ids]
    expected_effects = {"W-01": 0, "W-02": 0, "W-03": 0, "W-04": 1, "W-05": 0}
    summary = {
        "suite": "write",
        "fixtures_total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "expected_new_external_effects": sum(
            expected_effects[r["fixture_id"]] for r in results),
        "observed_new_external_effects": sum(
            r["external_write_effects"] for r in results),
        "read_store_immutable": case_store.check_immutable(),
        "note": ("写效果只允许出现在 W-04 成功路径 (隔离模拟服务 1 张复核单); "
                 "拒绝/过期/不匹配/无审批路径新增效果必须为 0; 只读工单库零搅动"),
    }
    return results, summary


def run_all(only=None):
    """运行夹具集合; 返回 (逐行结果, 总结)。"""
    if only:
        if only not in FIXTURE_IDS:
            raise SystemExit(f"未知夹具: {only} (可选: {', '.join(FIXTURE_IDS)})")
        ids = (only,)
    else:
        ids = FIXTURE_IDS
    results = [RUNNERS[fixture_id]() for fixture_id in ids]
    summary = {
        "fixtures_total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "total_external_write_effects": sum(
            r["external_write_effects"] for r in results),
        "store_immutable": case_store.check_immutable(),
        "note": "只读合同: 外部写效果恒 0; 工单库快照运行前后逐字节一致",
    }
    return results, summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="MCP 工具夹具运行器 (R/X 只读 8 条 + W 写负向 5 条)")
    parser.add_argument("--suite", choices=("read", "write", "all"),
                        default="read",
                        help="夹具套件 (默认 read = 第 13 周 R/X; all = 13 条)")
    parser.add_argument("--only", default=None,
                        help="只运行单个夹具, 如 --only R-03 或 --only W-04")
    args = parser.parse_args(argv)

    if args.only:
        runner = run_all if args.only.startswith(("R-", "X-")) else run_write_all
        results, summary = runner(args.only)
    elif args.suite == "read":
        results, summary = run_all()
    elif args.suite == "write":
        results, summary = run_write_all()
    else:
        results, summary = run_all()
        write_results, write_summary = run_write_all()
        summary["combined"] = {
            "read_fixtures": summary["fixtures_total"],
            "write_fixtures": write_summary["fixtures_total"],
            "read_passed": summary["passed"],
            "write_passed": write_summary["passed"],
            "total_new_external_effects":
                write_summary["observed_new_external_effects"],
        }
        results = results + write_results
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed_count = summary["failed"]
    if "combined" in summary:
        failed_count += write_summary["failed"]
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()


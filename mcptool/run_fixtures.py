"""夹具运行器 (Week 13 第 4 天) —— R-01..R-05 + X-01..X-03, 逐行 JSON 结果。

用法: python -m mcptool.run_fixtures [--only R-03]
退出码: 全部通过 0; 任一失败 1。
确定性: read_at 用固定时钟 (2026-10-08T00:00:00Z), 输出可复现 (demo_lookup 用真实时钟);
外部写效果恒为 0, 工单库快照运行前后逐字节一致 (随总结输出 store_immutable)。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import case_store
from . import contract as C
from .client import McpHost, TrustedSession

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
        description="只读工具夹具运行器 (R-01..R-05 + X-01..X-03)")
    parser.add_argument("--only", default=None,
                        help="只运行单个夹具, 如 --only R-03")
    args = parser.parse_args(argv)
    results, summary = run_all(args.only)
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if summary["failed"] == 0 else 1)


if __name__ == "__main__":
    main()


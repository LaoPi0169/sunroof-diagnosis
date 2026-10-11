"""MCP 工具合同常量 (Week 13/14) —— 只读 case.lookup_context@1 + 受控写 ticket.create@1。

封闭合同 (与测试互锁): effect 分层 read_only / write_once_reversible;
身份只来自可信会话 (参数走私 = invalid_argument); 错误八类封闭枚举。
"""
from __future__ import annotations

READ_TOOL_NAME = "case.lookup_context"
READ_TOOL_VERSION = "1"
READ_EFFECT = "read_only"
READ_FIELDS = ("task_type", "region", "purchased_at")
READ_RESULT_LIMIT_RECORDS = 1
READ_DEADLINE_MS = 500
TRUSTED_CONTEXT_KEYS = ("tenant", "user", "role")  # 只来自可信会话, 参数不可覆盖

WRITE_TOOL_NAME = "ticket.create"
WRITE_TOOL_VERSION = "1"
WRITE_EFFECT = "write_once_reversible"
WRITE_QUEUES = ("billing-supervisor-review",)      # 策略白名单 (schema 枚举超集见下)
WRITE_QUEUE_SCHEMA = ("billing-supervisor-review", "executive-review")
WRITE_ARGS_SCHEMA = {
    "case_id": str, "queue": str, "reason_code": str,
    "source_refs": list, "idempotency_key": str, "approval_id": str,
}
WRITE_FORBIDDEN_EFFECTS = ("multi_ticket", "refund", "customer_send",
                           "permission_change", "real_system_write")
ERROR_CODES = (
    "invalid_argument", "not_available", "deadline_exceeded",
    "contract_violation", "version_mismatch",
    "unauthorized", "approval_required", "approval_mismatch",
    "approval_denied", "mcp_unavailable",
)
APPROVAL_TTL_SECONDS = 900
APPROVAL_MAX_USES = 1
POLICY_SOURCE_REFS = {
    "retrofit-oem-a-2026-04": ["retrofit-bulletin-v2", "retrofit-notice-v4"],
}


def normalize_write_arguments(args: dict) -> dict:
    """单一规范化函数 (预览/审批绑定/执行三处共用):
    case_id 去空白大写; queue 小写去空白; source_refs 去空白+排序+去重; reason_code 大写。
    表示变化要么等价 (放行) 要么实质变化 (approval_mismatch), 无第三种表示。"""
    return {
        "case_id": str(args.get("case_id", "")).strip().upper(),
        "queue": str(args.get("queue", "")).strip().lower(),
        "reason_code": str(args.get("reason_code", "")).strip().upper(),
        "source_refs": sorted({str(ref).strip() for ref in args.get("source_refs", [])}),
        "idempotency_key": str(args.get("idempotency_key", "")).strip(),
    }

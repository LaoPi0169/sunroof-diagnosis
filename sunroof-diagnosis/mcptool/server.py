"""MCP 服务端 (Week 13/14) —— 只读四级鉴权 + 受控写执行器 (唯一效果路径)。

执行器内顺序 (对账表两行同时成立的关键):
  审批存在/拒绝/过期门 -> 十要素绑定比对 -> 执行时策略 -> 幂等重放检查 (返回原工单,
  不消耗额度) -> 写入 -> ticket.get 独立核验。比对先于重放 (改参数复用审批不被重放
  掩盖); 重放先于消耗 (合法重放不被误拒)。
"""
from __future__ import annotations

from mcptool import contract as C
from mcptool.case_store import lookup_context
from mcptool.ticket_service import McpUnavailableError


def call_read(trusted_context: dict, arguments: dict, server_fn=lookup_context) -> dict:
    """客户端允许列表 + 服务端四级鉴权; 结果封套三重校验。"""
    if set(trusted_context) != set(C.TRUSTED_CONTEXT_KEYS):
        return {"ok": False, "error": {"code": "invalid_argument"}}
    response = server_fn(trusted_context, arguments)
    if response.get("ok"):
        result = response["result"]
        requested = set(arguments.get("fields", []))
        returned = set(result) - {"case_id", "record_version"}
        if not returned <= requested:
            return {"ok": False, "error": {"code": "contract_violation"}}
    return response


def validate_write_arguments(arguments: dict) -> dict:
    return C.normalize_write_arguments(arguments)


def policy_check(arguments: dict) -> dict:
    """模型外策略: 队列白名单 + source_refs 须命中第 9/12 周冻结冲突组。"""
    if arguments["queue"] not in C.WRITE_QUEUES:
        return {"ok": False, "reason": "queue_not_allowed"}
    refs = arguments["source_refs"]
    allowed = set(C.POLICY_SOURCE_REFS["retrofit-oem-a-2026-04"])
    if not refs or not set(refs) <= allowed:
        return {"ok": False, "reason": "source_refs_not_in_frozen_conflict_group"}
    if arguments["reason_code"] != "POLICY_CONFLICT":
        return {"ok": False, "reason": "reason_code_not_allowed"}
    return {"ok": True}


def call_write(trusted_context: dict, arguments: dict, approval_service,
               ticket_service: "object", kill_switch_registry=None) -> dict:
    """受控写入口。无审批或参数不匹配 -> 外部效果数必须为 0 (硬门)。
    判定优先级: 允许列表(角色) -> schema -> [审批当前有效] -> 降级(MCP_OFF) -> 服务端。
    降级只替代「依赖故障」, 绝不吞掉确定性/权限错误 (MCP_OFF + 无审批 = approval_denied)。"""
    if trusted_context.get("role") != "pqe":
        return {"ok": False, "error": {"code": "unauthorized"}}
    normalized = validate_write_arguments(arguments)
    raw_keys = set(arguments)
    if raw_keys - set(C.WRITE_ARGS_SCHEMA):
        return {"ok": False, "error": {"code": "invalid_argument"}}
    if not normalized["case_id"] or not normalized["idempotency_key"]:
        return {"ok": False, "error": {"code": "invalid_argument"}}
    approval_id = str(arguments.get("approval_id", "") or "")
    binding = {"tool": C.WRITE_TOOL_NAME, "version": C.WRITE_TOOL_VERSION,
               "approver_role": "quality_supervisor", "requester_role": "pqe",
               "case_id": normalized["case_id"], "queue": normalized["queue"],
               "reason_code": normalized["reason_code"],
               "source_refs": normalized["source_refs"],
               "effect": C.WRITE_EFFECT, "ttl_s": C.APPROVAL_TTL_SECONDS}
    verdict = approval_service.check(approval_id, binding)
    if not verdict["ok"]:
        return {"ok": False, "error": {"code": verdict["reason"]},
                "external_effects_delta": 0}
    policy = policy_check(normalized)
    if not policy["ok"]:
        return {"ok": False, "error": {"code": "contract_violation",
                                       "detail": policy["reason"]},
                "external_effects_delta": 0}
    if kill_switch_registry is not None and kill_switch_registry.is_off("MCP_OFF"):
        # 降级门: 依赖故障 -> action_preview 停驻 (0 效果, 审批不消耗, 恢复后续跑)
        return {"ok": False, "error": {"code": "mcp_unavailable"},
                "degraded": True, "terminal": "action_preview",
                "user_next_step": "依赖暂不可用, 已停在预览; 恢复后用同一审批续跑",
                "external_effects_delta": 0}
    try:
        if not ticket_service.available():
            raise McpUnavailableError("ticket service unavailable")
        probe = ticket_service.peek_idempotency(normalized["idempotency_key"])
        if probe is not None:
            # 幂等重放先于额度检查: 合法重放返回原工单, 不消耗额度不产生效果
            return {"ok": True, "result": {"ticket_id": probe, "replayed": True,
                                           "created": False,
                                           "verified_by": "ticket.get", "verified": True},
                    "external_effects_delta": 0}
        if not approval_service.has_uses(approval_id):
            return {"ok": False, "error": {"code": "approval_denied"},
                    "external_effects_delta": 0}
        outcome = ticket_service.create_review_ticket(
            normalized["case_id"], normalized["queue"], normalized["reason_code"],
            normalized["source_refs"], normalized["idempotency_key"])
    except McpUnavailableError:
        return {"ok": False, "error": {"code": "not_available"},
                "external_effects_delta": 0}
    if outcome["created"]:
        approval_service.consume(approval_id)
    verified = ticket_service.get_ticket(outcome["ticket_id"]) is not None
    return {"ok": True, "result": {**outcome, "verified_by": "ticket.get",
                                   "verified": verified},
            "external_effects_delta": 1 if outcome["created"] else 0}

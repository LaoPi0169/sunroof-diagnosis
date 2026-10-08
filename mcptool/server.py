"""MCP 服务端 (Week 13 只读 + Week 14 审批写) —— 二次鉴权 + 封闭过滤 + 结果封套。

只读路径 (case.lookup_context): 服务端不信任 Host 转述, 每次调用重新执行封闭
校验与 (会话角色 x 租户 x 对象可见性 x 字段) 四级鉴权。细分拒绝原因
(reason_typed) 只进受限审计, 用户面文案统一安全 —— 不存在/跨租户/角色不可见
三种拒绝的用户面响应逐字节一致 (不暴露对象存在性)。

写路径 (ticket.preview_create / ticket.create / ticket.get, Week 14):
  - preview_create 物理隔离: 不调用 ticket_service 任何写方法, 只读外部计数;
  - create 执行前重读当前审批逐项比对 (绑定字段 + 执行时策略再判定),
    不信任预览缓存; 幂等键先于消耗额度查重放; 写后用 ticket.get 语义独立核验;
  - get_ticket 对写 store 的只读核验查询 (不存在/跨租户一律 not_available)。

rogue_mode 仅供夹具模拟「失控服务端」(超合同结果 / 版本漂移), 正常路径恒为 None。
"""
from __future__ import annotations

import time

from . import case_store
from . import contract as C
from . import write_contract as W


def _error(code: str) -> dict:
    """用户面错误封套 (无 run_id/无细节 —— 保证同因异因响应可逐字节一致)。"""
    return {
        "status": "error",
        "tool_version": C.TOOL_VERSION,
        "error": {"code": code, "message": C.SAFE_MESSAGES[code]},
    }


def lookup_context(arguments, session, *, read_at, store=None,
                   delay_seconds=0.0, rogue_mode=None):
    """处理一次只读查询; 返回 (envelope, reason_typed)。

    envelope: ok -> {"status", "tool_version", "records": [record]}
              error -> {"status", "tool_version", "error": {code, message}}
    record:  {"case_id", "context": {枚举字段: 值}, "source": {record_version, read_at}}
    """
    if delay_seconds:
        time.sleep(delay_seconds)  # 慢速夹具: 客户端 deadline 演练用

    # -- 1) 封闭 schema 校验 (服务端独立重做, 不依赖 Host) --
    case_id, fields, err = C.validate_arguments(arguments)
    if err:
        return _error(err[0]), f"rejected_schema:{err[1]}"

    # -- 2) 会话角色识别 (未知角色按不可用处理, 不暴露细节) --
    if session.role not in C.ALLOWED_ROLES:
        return _error(C.ERROR_NOT_AVAILABLE), "caller_not_recognized"

    # -- 3) 对象级鉴权: 不存在 / 跨租户 / 角色不可见 -> 同一文案 not_available --
    book = store if store is not None else case_store.CASE_STORE
    case = book.get(case_id)
    if case is None:
        return _error(C.ERROR_NOT_AVAILABLE), "object_not_found"
    if case["tenant_id"] != session.tenant_id:
        return _error(C.ERROR_NOT_AVAILABLE), "object_in_other_tenant"
    if session.role not in case["visible_to_roles"]:
        return _error(C.ERROR_NOT_AVAILABLE), "object_not_visible_to_caller"

    # -- 4) 字段过滤: 只从封闭枚举取值 (受限字段物理上无法进入响应) --
    context = {field: case["fields"][field] for field in fields}
    record = {
        "case_id": case_id,
        "context": context,
        "source": {
            "record_version": case["record_version"],
            "read_at": read_at,
        },
    }
    envelope = {
        "status": "ok",
        "tool_version": C.TOOL_VERSION,
        "records": [record],
    }
    # -- 5) 失控服务端夹具注入 (仅 rogue_mode; 正常路径不经过) --
    if rogue_mode == "two_records":
        envelope["records"] = [record, dict(record)]  # 超 result_limit.records=1
    elif rogue_mode == "wrong_version":
        envelope["tool_version"] = "2"  # 合同 pin "1" -> 客户端必须拒绝
    return envelope, "ok"


# ===========================================================================
# Week 14 写路径 (ticket.preview_create / ticket.create / ticket.get)
# ===========================================================================


def _write_error(code: str) -> dict:
    """写路径用户面错误封套 (无 run_id/无审批细节; 细分原因只进受限审计)。"""
    return {
        "status": "error",
        "tool_version": W.TOOL_VERSION,
        "error": {"code": code, "message": W.SAFE_MESSAGES[code]},
    }


def preview_create(arguments, session, *, now, ticket_service, policy,
                   rag_terminal=None, rogue_mode=None):
    """无副作用预览: 规范化 + 策略判定 + 展示将创建什么; 外部效果恒 0。

    物理隔离: 本函数不接触 ticket_service 的任何写方法 (只读 ticket_count),
    「打开确认页不创建记录」由夹具以预览前后计数断言。
    返回 (envelope, reason_typed)。
    """
    canonical, err = W.validate_write_arguments(arguments)
    if err:
        return _write_error(W.ERROR_INVALID_ARGUMENT), f"rejected_schema:{err}"

    if session.role not in C.ALLOWED_ROLES:
        return _write_error(W.ERROR_NOT_AVAILABLE), "caller_not_recognized"

    allowed, reason = policy.decide(
        session=session, canonical_parameters=canonical,
        rag_terminal=rag_terminal)
    if not allowed:
        # 策略在审批之前拒绝: approval_denied 是正常安全终态, 0 个效果
        return ({"status": W.STATUS_DENIED, "tool_version": W.TOOL_VERSION,
                 "effect": W.EFFECT, "external_ticket_count": 0},
                f"policy_denied:{reason}")

    envelope = {
        "status": W.STATUS_PREVIEW,
        "tool_version": W.TOOL_VERSION,
        "effect": W.EFFECT,
        "effect_class": W.EFFECT_CLASS,
        "canonical_parameters": canonical,
        "resource_scope": W.resource_scope(canonical["case_id"]),
        "policy_version": policy.POLICY_VERSION,
        "expires_at": W.iso_plus_seconds(now, policy.APPROVAL_TTL_SECONDS),
        "external_ticket_count": ticket_service.ticket_count(),
    }
    if rogue_mode == "wrong_version":
        envelope["tool_version"] = "2"
    return envelope, "ok:preview_zero_effect"


def create(arguments, session, *, now, run_id, approval_id, approval_service,
           ticket_service, policy, rag_terminal=None, rogue_mode=None):
    """执行前再授权 -> 幂等写入 -> 独立核验; 唯一产生外部效果的函数。

    执行器不接受「模型说用户已经同意」: 重新读取当前审批 (status_now),
    逐项比对绑定字段, 再做执行时策略判定; 幂等键先于消耗额度查重放。
    """
    canonical, err = W.validate_write_arguments(arguments)
    if err:
        return _write_error(W.ERROR_INVALID_ARGUMENT), f"rejected_schema:{err}"

    if session.role not in C.ALLOWED_ROLES:
        return _write_error(W.ERROR_NOT_AVAILABLE), "caller_not_recognized"

    # -- 1) 重读当前审批 (不信任预览缓存/模型转述) --
    if not approval_id:
        return _write_error(W.ERROR_APPROVAL_REQUIRED), "approval_id_missing"
    record, status = approval_service.status_now(approval_id, now=now)
    if status == "missing":
        return _write_error(W.ERROR_APPROVAL_REQUIRED), "approval_not_found"
    if status == "denied":
        return _write_error(W.ERROR_APPROVAL_REQUIRED), "approval_decision_denied"
    if status == "expired":
        return _write_error(W.ERROR_APPROVAL_EXPIRED), "approval_expired"
    # 注意: exhausted 不在此处拦截 —— 幂等重放检查 (步骤 4) 须先于用尽检查,
    # 否则合法重放会被误判为 approval_required (手册 W-04 期望返回原工单);
    # 而绑定比对 (步骤 2) 又必须先于重放, 否则改参数复用审批会被重放掩盖。

    # -- 2) 绑定逐项比对 (任一实质变化 -> 原审批失效) --
    bindings = {
        "requester": session.user_id,
        "tenant_id": session.tenant_id,
        "run_id": run_id,
        "tool": W.TOOL_CREATE,
        "tool_version": W.TOOL_VERSION,
        "approved_parameters": canonical,
        "resource_scope": W.resource_scope(canonical["case_id"]),
        "policy_version": policy.POLICY_VERSION,
    }
    for field in W.APPROVAL_BINDING_FIELDS:
        if bindings[field] != record[field]:
            return _write_error(W.ERROR_APPROVAL_MISMATCH), f"binding:{field}"

    # -- 3) 执行时策略再判定 (审批后角色/政策/参数可能已变) --
    allowed, reason = policy.decide(
        session=session, canonical_parameters=canonical,
        rag_terminal=rag_terminal)
    if not allowed:
        return (_write_error(W.ERROR_APPROVAL_MISMATCH),
                f"policy_at_execution:{reason}")

    # -- 4) 幂等键重放检查 (先于消耗额度; 重放不产生新效果) --
    key = W.idempotency_key(run_id)
    existing_id = ticket_service.find_id_by_idempotency_key(key)
    if existing_id is not None:
        verified = ticket_service.get_ticket(existing_id)
        envelope = {
            "status": W.STATUS_COMPLETED,
            "tool_version": W.TOOL_VERSION,
            "effect": W.EFFECT,
            "ticket_id": existing_id,
            "idempotency_key": key,
            "ticket_state": verified["state"] if verified else "unknown",
            "verified_by": "ticket.get",
            "external_ticket_count": ticket_service.ticket_count(),
        }
        if rogue_mode == "wrong_version":
            envelope["tool_version"] = "2"
        return envelope, "idempotent_replay"

    # -- 5) 用尽检查 + 消耗审批额度 (超过 maximum_uses 拒绝) --
    if record["uses"] >= record["maximum_uses"]:
        return _write_error(W.ERROR_APPROVAL_REQUIRED), "approval_exhausted"
    if not approval_service.consume(approval_id):
        return _write_error(W.ERROR_APPROVAL_REQUIRED), "approval_exhausted"

    # -- 6) 带幂等键写入隔离模拟服务 --
    ticket_id, _created = ticket_service.create_review_ticket(
        case_id=canonical["case_id"], reason_code=canonical["reason_code"],
        source_refs=canonical["source_refs"],
        target_queue=canonical["target_queue"], idempotency_key=key,
        requested_by=session.user_id, tenant_id=session.tenant_id,
        created_at=now)

    # -- 7) 独立核验: 完成由外部状态证明, 不由写返回值/模型宣称证明 --
    verified = ticket_service.get_ticket(ticket_id)
    if verified is None or verified.get("ticket_id") != ticket_id:
        # 确定性进程内实现不会走到这里; 真实系统此处进 result_unknown (第 15 周对账)
        return _write_error(W.ERROR_NOT_AVAILABLE), "verify_failed_result_unknown"

    envelope = {
        "status": W.STATUS_COMPLETED,
        "tool_version": W.TOOL_VERSION,
        "effect": W.EFFECT,
        "ticket_id": ticket_id,
        "idempotency_key": key,
        "ticket_state": verified["state"],
        "verified_by": "ticket.get",
        "external_ticket_count": ticket_service.ticket_count(),
    }
    if rogue_mode == "wrong_version":
        envelope["tool_version"] = "2"
    return envelope, "ok:write_verified"


_GET_RECORD_FIELDS = ("ticket_id", "state", "case_id", "reason_code",
                      "source_refs", "target_queue", "requested_by", "created_at")


def get_ticket(arguments, session, *, ticket_service):
    """ticket.get: 对写 store 的只读核验查询 (结果核验与人工查询共用)。

    不存在/跨租户一律 not_available (用户面逐字节一致, 不暴露工单存在性)。
    """
    if not isinstance(arguments, dict) or set(arguments) != {"ticket_id"}:
        return (_write_error(W.ERROR_INVALID_ARGUMENT),
                "rejected_schema:get 需要 ticket_id 单键")
    if C.TRUSTED_CONTEXT_KEYS and set(arguments) & set(C.TRUSTED_CONTEXT_KEYS):
        return (_write_error(W.ERROR_INVALID_ARGUMENT),
                "rejected_schema:可信会话键不得作为参数")
    ticket_id = arguments["ticket_id"]
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        return (_write_error(W.ERROR_INVALID_ARGUMENT),
                "rejected_schema:ticket_id 必须是非空字符串")

    record = ticket_service.get_ticket(ticket_id.strip())
    if record is None or record["tenant_id"] != session.tenant_id:
        return _write_error(W.ERROR_NOT_AVAILABLE), "object_not_or_not_available"
    safe_record = {field: record[field] for field in _GET_RECORD_FIELDS}
    envelope = {
        "status": "ok",
        "tool_version": W.TOOL_VERSION,
        "records": [safe_record],
        "external_ticket_count": ticket_service.ticket_count(),
    }
    return envelope, "ok"

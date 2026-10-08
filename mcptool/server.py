"""case.lookup_context 服务端 (Week 13) —— 二次鉴权 + 封闭字段过滤 + 结果封套。

服务端不信任 Host 转述: 每次调用重新执行封闭校验与
(会话角色 x 租户 x 对象可见性 x 字段) 四级鉴权。细分拒绝原因 (reason_typed)
只进受限审计, 用户面文案统一安全 —— 不存在/跨租户/角色不可见三种拒绝
的用户面响应逐字节一致 (不暴露对象存在性)。

rogue_mode 仅供夹具模拟「失控服务端」(超合同结果 / 版本漂移), 正常路径恒为 None。
"""
from __future__ import annotations

import time

from . import case_store
from . import contract as C


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

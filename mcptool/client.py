"""MCP Host/Client (Week 13) —— 工具允许列表 + 可信会话注入 + deadline + 结果封套校验。

Host 职责 (回链 week-13/trust-boundary-v1.md 六分区):
  1) 工具允许列表默认关闭: 未注册工具/版本一律拒绝 (防误接与越范围工具);
  2) 身份注入: tenant/user/role 只从已认证会话来, 调用参数走私身份键即拒绝
     (服务端另有二次校验, 纵深防御);
  3) deadline 客户端强制: 超时结果整体作废 (晚到的好结果也是坏结果);
  4) 结果封套校验: 版本 pin、记录数 <= 1、返回字段 ⊆ 请求字段 ⊆ 枚举 ——
     超合同结果整体拒绝并记安全事件;
  5) 受限审计: 每次调用落 {run_id, caller, outcome, reason_typed} ——
     细分原因只进审计, 用户面封套永远只有安全文案。
外部写效果恒为 0 (只读合同; 本模块不存在任何写调用)。
"""
from __future__ import annotations

import time

from . import contract as C
from . import server as server_mod


class TrustedSession:
    """已认证会话 (生产中来自登录态; 教学实现由演示/测试显式构造)。"""

    def __init__(self, tenant_id: str, user_id: str, role: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.role = role

    def as_dict(self) -> dict:
        return {"tenant_id": self.tenant_id, "user_id": self.user_id,
                "role": self.role}


class McpHost:
    """进程内 Host: 允许列表 + 会话注入 + 客户端校验 + 受限审计。"""

    def __init__(self, session: TrustedSession, *, clock=None,
                 server_fn=None, store=None, deadline_ms=C.DEADLINE_MS):
        self.session = session
        self._clock = clock or (lambda: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._server_fn = server_fn or server_mod.lookup_context
        self._store = store
        self._deadline_ms = deadline_ms
        self.audit = []            # 受限审计 (含细分 reason_typed; 报告引用须标注 restricted)
        self.security_events = []  # contract_violation 安全事件
        self.external_write_effects = 0  # 只读合同: 恒 0, 任何路径不得递增
        self.server_calls = 0      # 观测: 实际到达服务端的调用次数 (走私应=0)

    # ---- 内部: 落审计并返回用户面错误封套 (不含 run_id/细节) ----
    def _typed_error(self, entry: dict, code: str, reason_typed: str) -> dict:
        entry["outcome"] = code
        entry["reason_typed"] = reason_typed
        return {
            "status": "error",
            "tool_version": C.TOOL_VERSION,
            "error": {"code": code, "message": C.SAFE_MESSAGES[code]},
        }

    # ---- 内部: 结果封套校验 (超合同 -> 返回违规描述) ----
    def _envelope_violation(self, envelope: dict, requested_fields: list):
        records = envelope.get("records")
        if not isinstance(records, list):
            return "records_not_list"
        if len(records) > C.RESULT_LIMIT_RECORDS:
            return f"records_exceed_limit:{len(records)}>{C.RESULT_LIMIT_RECORDS}"
        allowed = set(requested_fields)
        for record in records:
            if not isinstance(record, dict) or set(record) - {"case_id", "context", "source"}:
                return "record_shape_violation"
            context = record.get("context")
            if not isinstance(context, dict):
                return "context_not_object"
            if set(context) - allowed:
                return f"fields_beyond_request:{sorted(set(context) - allowed)}"
            if len(context) > C.RESULT_LIMIT_FIELDS:
                return f"fields_exceed_limit:{len(context)}>{C.RESULT_LIMIT_FIELDS}"
            for field in context:
                if field not in C.FIELD_ENUM:
                    return f"field_outside_enum:{field}"
        return None

    # ---- 主入口 ----
    def call(self, arguments, *, tool=C.TOOL_NAME,
             delay_seconds=0.0, rogue_mode=None) -> dict:
        entry = {
            "run_id": f"mcp-{len(self.audit) + 1:03d}",
            "tool": tool,
            "caller": self.session.as_dict(),
            "outcome": None,
            "reason_typed": None,
            "external_write_effects": 0,
        }
        self.audit.append(entry)

        # 1) 工具允许列表 (默认关闭: 只放行注册过的只读工具)
        if tool != C.TOOL_NAME:
            return self._typed_error(entry, C.ERROR_INVALID_ARGUMENT,
                                     f"tool_not_in_allowlist:{tool}")

        # 2) 参数封闭校验 + 身份走私检查 (Host 层; 服务端再校验一次)
        case_id, fields, err = C.validate_arguments(arguments)
        if err:
            return self._typed_error(entry, err[0], f"schema:{err[1]}")

        # 3) 调用服务端 (deadline 在客户端强制; 晚到结果整体作废)
        self.server_calls += 1
        started = time.perf_counter()
        envelope, reason_typed = self._server_fn(
            arguments, self.session, read_at=self._clock(), store=self._store,
            delay_seconds=delay_seconds, rogue_mode=rogue_mode)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # 4) deadline: 超时即作废 (无论结果好坏)
        if elapsed_ms > self._deadline_ms:
            return self._typed_error(
                entry, C.ERROR_DEADLINE_EXCEEDED,
                f"elapsed_ms:{elapsed_ms:.1f}>{self._deadline_ms}")

        # 5) 版本 pin (合同 version="1"; 失控服务端报 2 -> 整体拒绝)
        if envelope.get("tool_version") != C.TOOL_VERSION:
            return self._typed_error(
                entry, C.ERROR_VERSION_MISMATCH,
                f"tool_version:{envelope.get('tool_version')}")

        # 6) 结果封套校验 (超合同 -> contract_violation + 安全事件)
        if envelope.get("status") == "ok":
            violation = self._envelope_violation(envelope, fields)
            if violation:
                self.security_events.append({
                    "run_id": entry["run_id"],
                    "server_identity": C.SERVER_IDENTITY,
                    "violation": violation,
                })
                return self._typed_error(entry, C.ERROR_CONTRACT_VIOLATION,
                                         violation)

        # 7) 通过: 落审计后原样放行 (外部写效果恒 0)
        entry["outcome"] = envelope.get("status")
        entry["reason_typed"] = reason_typed
        return envelope

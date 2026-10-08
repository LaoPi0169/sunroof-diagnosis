"""MCP Host/Client (Week 13 只读 + Week 14 审批写) —— 允许列表 + 会话注入 + deadline + 封套校验。

Host 职责 (回链 week-13/trust-boundary-v1.md 六分区 + week-14 审批硬门):
  1) 工具允许列表默认关闭: 只读通道仅 case.lookup_context; 写通道仅
     ticket.preview_create / ticket.create / ticket.get (共 4 工具, 版本 pin 1);
  2) 身份注入: tenant/user/role 只从已认证会话来, 调用参数走私身份键即拒绝
     (服务端另有二次校验, 纵深防御);
  3) deadline 客户端强制: 超时结果整体作废 (读路径晚到的好结果也是坏结果;
     写路径超时意味着外部效果未确认 -> 提示先对账, 不盲重试);
  4) 结果封套校验: 版本 pin、记录数/字段上限, 以及写路径硬门 ——
     预览封套外部计数必须为 0、错误封套不得伴随外部效果、action_completed
     必须带 verified_by=ticket.get 与精确幂等键, 违规整体拒绝并记安全事件;
  5) 受限审计: 每次调用落 {run_id, caller, outcome, reason_typed, 效果增量} ——
     细分原因只进审计, 用户面封套永远只有安全文案。
外部效果只可能来自带有效审批的 ticket.create (唯一写入口); 其余路径恒 0。
"""
from __future__ import annotations

import time

from . import contract as C
from . import server as server_mod
from . import write_contract as W
from .approval import ApprovalService
from .policy import WritePolicy
from .ticket_service import TicketService

# Host 全局允许列表 (1 只读 + 3 写; 未注册工具/版本一律拒绝)
ALLOWED_TOOLS = (C.TOOL_NAME,) + W.WRITE_TOOLS


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
                 server_fn=None, store=None, deadline_ms=C.DEADLINE_MS,
                 ticket_service=None, approval_service=None, policy=None,
                 write_deadline_ms=W.DEADLINE_MS):
        self.session = session
        self._clock = clock or (lambda: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._server_fn = server_fn or server_mod.lookup_context
        self._store = store
        self._deadline_ms = deadline_ms
        # 写栈 (Week 14): 缺省各自新建隔离实例; 演示/夹具/测试可注入共享栈
        self._ticket_service = ticket_service or TicketService()
        self._approval_service = approval_service or ApprovalService()
        self._policy = policy or WritePolicy()
        self._write_deadline_ms = write_deadline_ms
        self.audit = []            # 受限审计 (含细分 reason_typed; 报告引用须标注 restricted)
        self.security_events = []  # contract_violation 安全事件
        self.external_write_effects = 0  # 累计外部效果增量 (只读调用恒为 0)
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

    # ==========================================================================
    # Week 14 写通道 (ticket.preview_create / ticket.create / ticket.get)
    # ==========================================================================

    def _typed_write_error(self, entry: dict, code: str, reason_typed: str) -> dict:
        entry["outcome"] = code
        entry["reason_typed"] = reason_typed
        return {
            "status": "error",
            "tool_version": W.TOOL_VERSION,
            "error": {"code": code, "message": W.SAFE_MESSAGES[code]},
        }

    def call_write(self, tool, arguments, *, run_id, approval_id=None,
                   rag_terminal=None, delay_seconds=0.0, rogue_mode=None) -> dict:
        """写工具主入口: 允许列表 + Host 硬门复验 + deadline + 受限审计。

        Host 端硬门 (客户端否决权, 不信任服务端自觉):
          - 预览封套 external_ticket_count != 0 -> contract_violation;
          - 错误封套伴随外部效果增量 != 0 -> contract_violation (拒绝路径零效果);
          - action_completed 必须 verified_by=ticket.get 且幂等键与 run 精确绑定。
        """
        entry = {
            "run_id": run_id,
            "tool": tool,
            "caller": self.session.as_dict(),
            "approval_id": approval_id,
            "outcome": None,
            "reason_typed": None,
            "external_write_effects": 0,
        }
        self.audit.append(entry)

        # 1) 写通道允许列表 (只放行 3 个写工具; 读工具须走 call() 读通道)
        if tool not in W.WRITE_TOOLS:
            return self._typed_write_error(
                entry, W.ERROR_INVALID_ARGUMENT,
                f"tool_not_in_write_allowlist:{tool}")

        # 2) Host 层封闭校验 (预览/执行; 服务端独立再校验一次)
        if tool in (W.TOOL_PREVIEW, W.TOOL_CREATE):
            _canonical, err = W.validate_write_arguments(arguments)
            if err:
                return self._typed_write_error(
                    entry, W.ERROR_INVALID_ARGUMENT, f"schema:{err}")

        # 3) 调用服务端 (deadline 客户端强制; 外部效果以前后计数差观测)
        self.server_calls += 1
        before = self._ticket_service.ticket_count()
        started = time.perf_counter()
        if tool == W.TOOL_PREVIEW:
            envelope, reason_typed = server_mod.preview_create(
                arguments, self.session, now=self._clock(),
                ticket_service=self._ticket_service, policy=self._policy,
                rag_terminal=rag_terminal, rogue_mode=rogue_mode)
        elif tool == W.TOOL_CREATE:
            envelope, reason_typed = server_mod.create(
                arguments, self.session, now=self._clock(), run_id=run_id,
                approval_id=approval_id,
                approval_service=self._approval_service,
                ticket_service=self._ticket_service, policy=self._policy,
                rag_terminal=rag_terminal, rogue_mode=rogue_mode)
        else:
            envelope, reason_typed = server_mod.get_ticket(
                arguments, self.session,
                ticket_service=self._ticket_service)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        after = self._ticket_service.ticket_count()
        delta = after - before

        # 4) deadline: 写超时 -> 外部效果未确认, 提示对账 (不折叠成可盲重试失败)
        if elapsed_ms > self._write_deadline_ms:
            return self._typed_write_error(
                entry, W.ERROR_DEADLINE_EXCEEDED,
                f"elapsed_ms:{elapsed_ms:.1f}>{self._write_deadline_ms};"
                f"effect_delta:{delta}")

        # 5) 版本 pin (合同 version="1")
        if envelope.get("tool_version") != W.TOOL_VERSION:
            return self._typed_write_error(
                entry, W.ERROR_VERSION_MISMATCH,
                f"tool_version:{envelope.get('tool_version')}")

        # 6) 写路径硬门复验 (违规 -> contract_violation + 安全事件)
        violation = None
        if envelope.get("status") == W.STATUS_PREVIEW:
            if envelope.get("effect") != W.EFFECT:
                violation = "preview_effect_mismatch"
            elif envelope.get("external_ticket_count", 0) != 0:
                violation = "preview_nonzero_effects"
        elif envelope.get("status") == W.STATUS_COMPLETED:
            if tool == W.TOOL_CREATE:
                if envelope.get("verified_by") != "ticket.get":
                    violation = "completed_not_verified_by_ticket_get"
                elif envelope.get("idempotency_key") != W.idempotency_key(run_id):
                    violation = "idempotency_key_mismatch"
                elif delta not in (0, 1):
                    violation = f"effect_delta_out_of_range:{delta}"
            else:  # ticket.get 是只读核验, 不得有效果
                if delta != 0:
                    violation = f"readonly_verify_with_effects:{delta}"
        elif envelope.get("status") == "error" and delta != 0:
            violation = f"error_envelope_with_effects:{delta}"
        if violation:
            self.security_events.append({
                "run_id": run_id,
                "server_identity": W.SERVER_IDENTITY,
                "violation": violation,
            })
            return self._typed_write_error(
                entry, W.ERROR_CONTRACT_VIOLATION, violation)

        # 7) 通过: 落审计后原样放行 (效果增量如实入账)
        entry["outcome"] = envelope.get("status")
        entry["reason_typed"] = reason_typed
        entry["external_write_effects"] = delta
        self.external_write_effects += delta
        return envelope

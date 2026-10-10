"""MCP Host/Client (Week 13 只读 + Week 14 审批写 + Week 15 对抗恢复) ——
允许列表 + 能力清单门 + 会话注入 + deadline + 封套校验 + 断连对账。

Host 职责 (回链 week-13/trust-boundary-v1.md 六分区 + week-14 审批硬门 + week-15 对抗增量):
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
     细分原因只进审计, 用户面封套永远只有安全文案;
  6) Week 15: 能力清单门 —— served 工具描述/参数模式摘要与冻结清单
     (week-15/mcp-capability-manifest-v2.json) 不一致 -> capability_quarantined
     隔离, 未冻结版本/字段不得自动启用 (A-02/A-05); 写后断连编排 —— 提交成功
     响应丢失 -> result_unknown -> 按幂等键先查后说 -> action_completed (独立核验)
     或 reconciliation_pending (禁止伪造终态, 恢复第一动作永远是查询不是再次创建,
     A-06); 撤销的审批在执行时重读当前状态 -> approval_revoked (A-04)。
   7) Week 17: 依赖故障安全降级 —— MCP 写通道不可达 (演练注入) 或 MCP_OFF
      开关激活时, ticket.create 停在 action_preview (degraded=true,
      reason_code=mcp_unavailable, 外部效果恒 0, 审批不消耗, 不自动重试);
      判定优先级: 权限拒绝/确定性错误照常走服务端, 不被降级吞掉。
    8) Week 18: 值守冷交接复用层 —— 8018 工作台 (workbench_handoff/) 在本编排
       之上做双角色隔离: 业务用户只见权限内任务操作, 注入/开关/版本包回滚/
       审计/恢复复验要求管理员登录 (服务端 403 强制); 本文件零改动即可支撑
       冷交接演练 (交接只加壳, 不改写通道语义)。
外部效果只可能来自带有效审批的 ticket.create (唯一写入口); 其余路径恒 0。
"""
from __future__ import annotations

import time

from . import contract as C
from . import server as server_mod
from . import write_contract as W
from .approval import ApprovalService
from .capability_manifest import ServedCatalog
from .kill_switch import SWITCH_MCP_OFF
from .policy import WritePolicy
from .ticket_service import (
    McpUnavailableError, QueryUnavailableError, ResponseLostError, TicketService,
)

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
                 write_deadline_ms=W.DEADLINE_MS, catalog=None,
                 kill_switch=None, write_probe=None):
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
        # 能力清单门 (Week 15): served 目录缺省 == 冻结清单; 夹具可注入 rogue 描述
        self._catalog = catalog or ServedCatalog.from_manifest()
        self._quarantined = set()  # 已隔离能力 (解除须人工 restore_capability)
        # 降级缝 (Week 17): kill_switch=KillSwitchRegistry (运营手段);
        # write_probe=WriteChannelProbe (演练注入手段) —— 两条路同一安全终态
        self._kill_switch = kill_switch
        self._write_probe = write_probe
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

    # ==========================================================================
    # Week 17: 依赖故障安全降级 (MCP 不可用 -> 停在 action_preview)
    # ==========================================================================

    def _degradation_gate(self, entry: dict, tool: str, arguments: dict,
                          approval_id):
        """写执行前的降级缝: 返回降级封套 (命中) 或 None (照常走服务端)。

        判定优先级 (合同: 权限拒绝保持拒绝, 降级不得吞掉确定性错误):
          允许列表 -> 能力清单 -> schema -> [审批当前有效?] -> 降级 -> 服务端。
        只有 tool=ticket.create 且 (注入探测不可达 或 MCP_OFF 激活) 且审批当前
        状态为 approved 才降级; 缺审批/审批无效/参数非法等照常走服务端拿
        确定性错误 —— 降级只替代「依赖故障」这一类失败, 不替代任何业务判定。
        """
        if tool != W.TOOL_CREATE:
            return None  # 开关作用域 = 写执行; 预览/核验查询不受阻
        injected = False
        if self._write_probe is not None:
            try:
                self._write_probe.check()
            except McpUnavailableError:
                injected = True
        switch_on = (self._kill_switch is not None
                     and self._kill_switch.is_active(SWITCH_MCP_OFF))
        if not injected and not switch_on:
            return None
        if not approval_id:
            return None  # 无审批 -> 照常 approval_required (权限判定优先)
        _record, status = self._approval_service.status_now(
            approval_id, now=self._clock())
        if status != "approved":
            return None  # 撤销/过期/用尽 -> 照常确定性错误 (权限判定优先)
        trigger = ("fault_injection:write_probe" if injected
                   else "kill_switch:MCP_OFF")
        return self._degraded_preview(
            entry, arguments, reason_typed=f"mcp_unavailable:{trigger}")

    def _degraded_preview(self, entry: dict, arguments: dict, *,
                          reason_typed: str) -> dict:
        """降级封套: 任务停在 action_preview (手册 3924/3959-3966 可观察结果)。

        - 不隐藏失败: degraded=true + reason_code=mcp_unavailable + 明确提示;
        - 不扩大权限/不产生效果: external_effect_count=0, 工单计数前后一致;
        - 不自动重试: 本方法单次返回, 重试决策留给用户 (retry_later);
        - 审批不在此消耗 (深层缝除外): 恢复后同一审批可直接续跑;
        - canonical_parameters 来自已过封闭校验的同一 arguments (预览即合同);
        - 封套由 Host 生成, 不再过服务端封套硬门 (与 _after_response_lost 同理)。
        """
        canonical, _err = W.validate_write_arguments(arguments)
        count = self._ticket_service.ticket_count()
        envelope = {
            "status": W.STATUS_PREVIEW,
            "tool_version": W.TOOL_VERSION,
            "effect": W.EFFECT,
            "degraded": True,
            "reason_code": W.REASON_MCP_UNAVAILABLE,
            "message": W.DEGRADED_PREVIEW_MESSAGE,
            "canonical_parameters": canonical,
            "resource_scope": W.resource_scope(canonical["case_id"]),
            "allowed_next_actions": list(W.ALLOWED_NEXT_ACTIONS),
            "idempotency_key": W.idempotency_key(entry["run_id"]),
            "external_effect_count": 0,
            "external_ticket_count_before": count,
            "external_ticket_count_after": count,
        }
        entry["outcome"] = W.STATUS_PREVIEW
        entry["reason_typed"] = reason_typed
        entry["degraded"] = True
        entry["external_write_effects"] = 0
        return envelope

    def call_write(self, tool, arguments, *, run_id, approval_id=None,
                   rag_terminal=None, delay_seconds=0.0, rogue_mode=None,
                   fail_after_commit=False) -> dict:
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

        # 1.5) 能力清单门 (Week 15, A-02/A-05): served 描述/模式摘要与冻结清单
        #      等值比对; 不一致 -> 隔离 + 安全事件 (先于一切业务判定, server 不被打扰)
        gate_error = self._capability_gate(entry, tool)
        if gate_error is not None:
            return gate_error

        # 2) Host 层封闭校验 (预览/执行; 服务端独立再校验一次)
        if tool in (W.TOOL_PREVIEW, W.TOOL_CREATE):
            _canonical, err = W.validate_write_arguments(arguments)
            if err:
                return self._typed_write_error(
                    entry, W.ERROR_INVALID_ARGUMENT, f"schema:{err}")

        # 2.5) 第 17 周降级缝 (仅 ticket.create; 权限判定优先于降级):
        #      MCP 不可达 (注入) 或 MCP_OFF 开关激活 -> 停在 action_preview,
        #      外部效果恒 0、审批不消耗、绝不自动重试 (决策留给用户);
        #      确定性错误 (schema/审批/权限) 不被降级吞掉 -> 照常走服务端。
        degraded = self._degradation_gate(entry, tool, arguments, approval_id)
        if degraded is not None:
            return degraded

        # 3) 调用服务端 (deadline 客户端强制; 外部效果以前后计数差观测);
        #    A-06 断连夹具: 提交成功后响应丢失 -> 本层捕获并进入对账编排
        self.server_calls += 1
        before = self._ticket_service.ticket_count()
        started = time.perf_counter()
        try:
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
                    rag_terminal=rag_terminal, rogue_mode=rogue_mode,
                    fail_after_commit=fail_after_commit)
            else:
                envelope, reason_typed = server_mod.get_ticket(
                    arguments, self.session,
                    ticket_service=self._ticket_service)
        except ResponseLostError:
            return self._after_response_lost(entry, run_id, before)
        except McpUnavailableError:
            # 深层缝: 依赖在服务端深处失败 (create 第 5 步已消耗审批) -> 同一
            # 安全终态; 幂等键不变, 恢复后重新确认审批再续跑, 绝无第二个效果。
            return self._degraded_preview(
                entry, arguments,
                reason_typed="mcp_unavailable:fault_injection:deep_seam")
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
        elif (envelope.get("status") == W.STATUS_RECONCILIATION_PENDING
                and delta != 0):
            violation = f"pending_envelope_with_effects:{delta}"
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

    # ==========================================================================
    # Week 15: 能力清单门 (A-02/A-05) + 写后断连对账 (A-06) + 能力状态面板
    # ==========================================================================

    @property
    def catalog(self) -> ServedCatalog:
        """served 目录访问 (工作台/夹具注入 rogue 描述用; 比对基准是冻结清单)。"""
        return self._catalog

    def _capability_gate(self, entry: dict, tool: str):
        """允许列表之后、一切业务判定之前: served 描述/模式摘要与冻结清单等值比对。

        不一致 -> 该能力隔离 (capability_quarantined) + 安全事件; 已隔离能力
        后续调用直接拒绝 (server_calls 不增加)。隔离不消耗已签发审批的额度。
        """
        if tool in self._quarantined:
            return self._typed_write_error(
                entry, W.ERROR_CAPABILITY_QUARANTINED, f"quarantined:{tool}")
        ok, reason = self._catalog.verify(tool)
        if not ok:
            self._quarantined.add(tool)
            self.security_events.append({
                "run_id": entry.get("run_id"),
                "server_identity": W.SERVER_IDENTITY,
                "violation": f"capability_quarantined:{tool}:{reason}",
            })
            return self._typed_write_error(
                entry, W.ERROR_CAPABILITY_QUARANTINED,
                f"capability_descriptor_mismatch:{tool}:{reason}")
        return None

    def inject_rogue_descriptor(self, tool: str, descriptor: dict) -> None:
        """夹具/工作台: 模拟 Server 目录未经评审的变化 (A-02 描述篡改 / A-05 版本+schema 变化)。"""
        self._catalog.register(tool, descriptor)

    def restore_capability(self, tool: str) -> None:
        """解除隔离并恢复冻结描述 (教学演示的人工解冻; 真实系统须评审流程)。"""
        self._quarantined.discard(tool)
        self._catalog.restore(tool)

    def capability_status(self) -> dict:
        """工具能力状态面板 (工具 -> 是否隔离 / served 是否与冻结清单一致)。"""
        status = {}
        for tool in ALLOWED_TOOLS:
            ok, reason = self._catalog.verify(tool)
            status[tool] = {
                "quarantined": tool in self._quarantined,
                "served_matches_manifest": ok,
                "reason": reason,
            }
        return status

    # ---- A-06: 写后断连 -> result_unknown -> 先查后说 (恢复第一动作是查询) ----

    @staticmethod
    def _pending_envelope(key: str) -> dict:
        return {
            "status": W.STATUS_RECONCILIATION_PENDING,
            "tool_version": W.TOOL_VERSION,
            "effect": W.EFFECT,
            "idempotency_key": key,
            "message": W.RECONCILIATION_PENDING_MESSAGE,
        }

    def _reconcile_lookup(self, run_id: str):
        """按幂等键先查后说: 查询外部事实并独立核验; 绝不调用创建。

        返回 (envelope, reason_typed); envelope ∈ action_completed
        (recovered_from=result_unknown) / reconciliation_pending (查询不可用/
        未查到/核验失败 —— 外部事实不足, 保持未知, 禁止伪造终态)。
        """
        key = W.idempotency_key(run_id)
        try:
            ticket_id = self._ticket_service.find_id_by_idempotency_key(key)
        except QueryUnavailableError:
            return self._pending_envelope(key), "reconciliation_query_unavailable"
        if ticket_id is None:
            return self._pending_envelope(key), "reconciliation_ticket_not_found"
        verified = self._ticket_service.get_ticket(ticket_id)
        if verified is None or verified.get("ticket_id") != ticket_id:
            return self._pending_envelope(key), "reconciliation_verify_failed"
        self._ticket_service.mark_reconciled(
            ticket_id, run_id=run_id, at=self._clock())
        envelope = {
            "status": W.STATUS_COMPLETED,
            "tool_version": W.TOOL_VERSION,
            "effect": W.EFFECT,
            "ticket_id": ticket_id,
            "idempotency_key": key,
            "ticket_state": verified["state"],
            "verified_by": "ticket.get",
            "recovered_from": W.STATUS_UNKNOWN,
            "external_ticket_count": self._ticket_service.ticket_count(),
        }
        return envelope, f"reconciled_from_result_unknown:{ticket_id}"

    def _after_response_lost(self, entry: dict, run_id: str, before: int) -> dict:
        """ResponseLostError 捕获后的编排: result_unknown -> 对账 -> 终态或保持待对账。

        顺序 (合同 v2): write_requested -> connection_lost -> result_unknown
        -> query_by_idempotency_key -> (found -> action_completed | 否则 pending)。
        """
        entry["outcome"] = W.STATUS_UNKNOWN
        entry["reason_typed"] = "connection_lost_after_commit"
        delta = self._ticket_service.ticket_count() - before  # 已提交但未核验
        envelope, reason = self._reconcile_lookup(run_id)
        if envelope["status"] == W.STATUS_COMPLETED:
            if (delta not in (0, 1)
                    or envelope.get("verified_by") != "ticket.get"
                    or envelope.get("idempotency_key") != W.idempotency_key(run_id)):
                self.security_events.append({
                    "run_id": entry["run_id"],
                    "server_identity": W.SERVER_IDENTITY,
                    "violation": "reconciled_envelope_out_of_contract",
                })
                return self._typed_write_error(
                    entry, W.ERROR_CONTRACT_VIOLATION,
                    "reconciled_envelope_out_of_contract")
            entry["outcome"] = W.STATUS_COMPLETED
            entry["reason_typed"] = f"connection_lost_after_commit->{reason}"
            entry["external_write_effects"] = delta
            self.external_write_effects += delta
            return envelope
        # 查询也不可用 / 未查到: 停在 reconciliation_pending, 禁止伪造终态
        entry["outcome"] = W.STATUS_RECONCILIATION_PENDING
        entry["reason_typed"] = f"connection_lost_after_commit->{reason}"
        entry["external_write_effects"] = delta
        self.external_write_effects += delta
        return envelope

    def reconcile(self, run_id: str) -> dict:
        """人工/编排对账入口 (恢复第一动作永远是查询, 不是再次创建)。

        只读幂等键查询 + ticket.get 独立核验 + 追加 reconciled 事件 (同 run 幂等);
        对账本身零外部效果 (效果增量 != 0 -> contract_violation)。
        """
        entry = {
            "run_id": run_id,
            "tool": "ticket.reconcile",
            "caller": self.session.as_dict(),
            "approval_id": None,
            "outcome": None,
            "reason_typed": None,
            "external_write_effects": 0,
        }
        self.audit.append(entry)
        before = self._ticket_service.ticket_count()
        envelope, reason = self._reconcile_lookup(run_id)
        delta = self._ticket_service.ticket_count() - before
        if envelope["status"] == W.STATUS_COMPLETED:
            if delta != 0:
                self.security_events.append({
                    "run_id": entry["run_id"],
                    "server_identity": W.SERVER_IDENTITY,
                    "violation": f"reconcile_with_effects:{delta}",
                })
                return self._typed_write_error(
                    entry, W.ERROR_CONTRACT_VIOLATION,
                    f"reconcile_with_effects:{delta}")
            entry["outcome"] = W.STATUS_COMPLETED
            entry["reason_typed"] = reason
            return envelope
        entry["outcome"] = W.STATUS_RECONCILIATION_PENDING
        entry["reason_typed"] = reason
        return envelope

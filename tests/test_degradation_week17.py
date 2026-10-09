"""降级与关闭开关测试 (Week 17) —— MCP 不可用安全降级 + 三类固定演练 + 恢复复验。

用例 (16):
  01 开关注册表: 未知开关拒绝 + set/clear 审计 (operator/reason) + 重复 set 幂等
  02 开关审计快照不可变 (外部改写副本不影响注册表)
  03 MCP_OFF 降级封套合同: action_preview + reason_code + 0 效果 + 审批不消耗
  04 权限判定优先: 无审批/审批撤销 -> 确定性错误, 不被降级吞掉
  05 注入降级: 探测恰 1 次 (不重试) + 服务端不被打扰 (server_calls 不增)
  06 深层缝: 服务端深处不可达 -> 同一安全终态 (0 效果; 幂等键防重复效果)
  07 降级不产生任何 store 变更: 快照前后逐字节一致
  08 trace/终态: action_preview_held -> 允许终态 action_preview;
     降级终态携带效果 = 合同违规被 SLI 抓住
  09 恢复复验·注入路: 同一 run_id + 同一审批续跑 -> 恰 1 效果 + 幂等重放
  10 恢复复验·开关路: 按批准复位 -> 续跑恰 1 效果; 复位审计留痕
  11 恢复后受限任务仍拒绝 + 正常任务完成 (权限不放宽)
  12 D-01 模型超时固定演练: system_failed + 0 效果 + deadline_exceeded
  13 D-02 索引过期固定演练: abstained + INDEX_STALE 开关 set/clear 审计
  14 演练全套: 8 段全 pass + 效果合计 3 + 脱敏 0 违规 + action_preview=2
  15 允许列表与冻结合同不变 (1 读 + 3 写; CONTRACT_SHA256 与 week-15 冻结一致)
  16 工作台 API 端到端: 正常对照组/注入降级/开关/续跑/重放/演练/追踪/脱敏 + 404

边界: 固定时钟 (审批 TTL/时间戳完全可复现); D-01 注入 0.65s 真实睡眠
(读路径 deadline 演练), 全套 < 6s; 工作台为进程内单会话 (module 级 _STATE)。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcptool import contract as C  # noqa: E402
from mcptool import write_contract as W  # noqa: E402
from mcptool.approval import ApprovalService  # noqa: E402
from mcptool.client import ALLOWED_TOOLS, McpHost, TrustedSession  # noqa: E402
from mcptool.kill_switch import (  # noqa: E402
    SWITCH_INDEX_STALE, SWITCH_MCP_OFF, SWITCH_MODEL_OFF, KillSwitchRegistry,
    SWITCH_NAMES, WriteChannelProbe,
)
from mcptool.policy import WritePolicy  # noqa: E402
from mcptool.run_degradation_drill import (  # noqa: E402
    FIXED_NOW, WRITE_ARGS, drill_d01_model_timeout, drill_d02_index_stale,
    run_drills,
)
from mcptool.telemetry import (  # noqa: E402
    TERMINAL_ACTION_PREVIEW, TaskTrace, TRACE_OUTCOME_PREVIEW_HELD,
    reduce_runtime_metrics, scan_spans_for_violations,
)
from mcptool.ticket_service import (  # noqa: E402
    McpUnavailableError, TicketService, UnavailableWriteTickets,
)

TENANT = "northstar-training"
SESSION = TrustedSession(TENANT, "user-zhou-ning", "support_agent")
RAG_TERMINAL = "escalate"
DENIED_ARGS = dict(WRITE_ARGS, target_queue="executive-review")


def _clock():
    return FIXED_NOW


def _stack(*, kill_switch=None, write_probe=None, tickets=None):
    tickets = tickets or TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, clock=_clock, ticket_service=tickets,
                   approval_service=approvals, policy=policy,
                   kill_switch=kill_switch, write_probe=write_probe)
    return host, tickets, approvals, policy


def _approve(approvals, policy, run_id):
    return approvals.issue(
        approval_id=f"approval-{run_id}", requester=SESSION.user_id,
        tenant_id=TENANT, run_id=run_id, tool=W.TOOL_CREATE,
        tool_version=W.TOOL_VERSION, approved_parameters=WRITE_ARGS,
        resource_scope=W.resource_scope(WRITE_ARGS["case_id"]),
        policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW)
class Week17DegradationTest(unittest.TestCase):
    """类级固定时钟; 每个用例独立写栈 (互不串状态)。"""

    # ---- 01 开关注册表 ----
    def test_01_switch_registry_audit_and_unknown_rejected(self):
        reg = KillSwitchRegistry(clock=_clock)
        for unknown in ("MCP_ON", "mcp_off", ""):
            with self.assertRaises(ValueError):
                reg.set(unknown, operator="ops", reason="x")
        self.assertEqual(reg.active_switches, ())
        event = reg.set(SWITCH_MCP_OFF, operator="ops-li",
                        reason="MCP 写通道隔离")
        self.assertEqual((event["event"], event["switch"], event["operator"]),
                         ("opened", SWITCH_MCP_OFF, "ops-li"))
        self.assertTrue(event["reason"] and event["at"] == FIXED_NOW)
        self.assertTrue(reg.is_active(SWITCH_MCP_OFF))
        dup = reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="again")
        self.assertEqual(dup["event"], "already_open")
        self.assertEqual(len(reg.audit_trail()), 1, "重复 set 不重复落审计")
        close = reg.clear(SWITCH_MCP_OFF, operator="ops-li", reason="按批准复位")
        self.assertEqual(close["event"], "closed")
        self.assertFalse(reg.is_active(SWITCH_MCP_OFF))
        with self.assertRaises(ValueError):  # 无因操作 = 未批准
            reg.set(SWITCH_MODEL_OFF, operator="ops-li", reason="")
        self.assertIn(SWITCH_MODEL_OFF, SWITCH_NAMES)
        self.assertIn(SWITCH_INDEX_STALE, SWITCH_NAMES)

    # ---- 02 审计快照不可变 ----
    def test_02_switch_audit_snapshot_immutable(self):
        reg = KillSwitchRegistry(clock=_clock)
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        trail = reg.audit_trail()
        trail[0]["event"] = "tampered"
        trail.append({"fake": True})
        self.assertEqual(reg.audit_trail()[0]["event"], "opened")
        self.assertEqual(len(reg.audit_trail()), 1)

    # ---- 03 MCP_OFF 降级封套合同 ----
    def test_03_kill_switch_degrades_to_action_preview_envelope(self):
        reg = KillSwitchRegistry(clock=_clock)
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        host, tickets, approvals, policy = _stack(kill_switch=reg)
        run_id = "northstar-w17-t03"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertEqual(envelope["status"], W.STATUS_PREVIEW)
        self.assertTrue(envelope["degraded"])
        self.assertEqual(envelope["reason_code"], W.REASON_MCP_UNAVAILABLE)
        self.assertEqual(envelope["allowed_next_actions"],
                         ["copy_preview", "submit_manually", "retry_later"])
        self.assertEqual(envelope["external_effect_count"], 0)
        self.assertEqual(envelope["external_ticket_count_before"],
                         envelope["external_ticket_count_after"])
        self.assertEqual(envelope["idempotency_key"],
                         f"{run_id}:ticket.create:1")
        self.assertEqual(tickets.ticket_count(), 0)
        record, status = approvals.status_now(approval["approval_id"],
                                              now=FIXED_NOW)
        self.assertEqual((status, record["uses"]), ("approved", 0),
                         "降级不消耗审批额度")
        entry = next(e for e in host.audit if e["tool"] == W.TOOL_CREATE
                     and e.get("degraded"))
        self.assertEqual(entry["outcome"], W.STATUS_PREVIEW)
        self.assertEqual(entry["reason_typed"],
                         "mcp_unavailable:kill_switch:MCP_OFF")
        self.assertEqual(entry["external_write_effects"], 0)
    # ---- 04 权限判定优先 ----
    def test_04_permission_errors_not_swallowed_by_degradation(self):
        reg = KillSwitchRegistry(clock=_clock)
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        host, tickets, approvals, policy = _stack(kill_switch=reg)
        run_id = "northstar-w17-t04"
        # 无审批 -> approval_required (不是降级)
        env = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                              rag_terminal=RAG_TERMINAL)
        self.assertEqual((env["status"], env["error"]["code"]),
                         ("error", W.ERROR_APPROVAL_REQUIRED))
        self.assertFalse(env.get("degraded", False))
        # 策略拒绝队列 -> approval_denied (不是降级)
        env = host.call_write(W.TOOL_PREVIEW, DENIED_ARGS, run_id=run_id,
                              rag_terminal=RAG_TERMINAL)
        self.assertEqual(env["status"], W.STATUS_DENIED)
        # 撤销的审批 -> approval_revoked (不是降级)
        approval = _approve(approvals, policy, run_id)
        approvals.revoke(approval["approval_id"], revoked_by="risk-officer",
                         revoked_at=FIXED_NOW, reason="执行前撤销")
        env = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                              approval_id=approval["approval_id"],
                              rag_terminal=RAG_TERMINAL)
        self.assertEqual((env["status"], env["error"]["code"]),
                         ("error", W.ERROR_APPROVAL_REVOKED))
        self.assertEqual(tickets.ticket_count(), 0)

    # ---- 05 注入降级: 单次探测, 不重试, 服务端不被打扰 ----
    def test_05_injection_degrades_single_attempt_no_server_call(self):
        probe = WriteChannelProbe()
        host, tickets, approvals, policy = _stack(write_probe=probe)
        run_id = "northstar-w17-t05"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        calls_before = host.server_calls  # 仅预览到达过服务端
        probe.unavailable = True
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertTrue(envelope["degraded"])
        self.assertEqual(envelope["reason_code"], W.REASON_MCP_UNAVAILABLE)
        self.assertEqual(probe.attempts, 1, "一次用户动作 = 恰一次探测, 无重试")
        self.assertEqual(host.server_calls, calls_before,
                         "注入命中时编排不落服务端")
        self.assertEqual(tickets.ticket_count(), 0)
        # 用户可复制预览: canonical_parameters 原样保留 (预览即合同)
        self.assertEqual(envelope["canonical_parameters"],
                         {"case_id": "NS-1042",
                          "reason_code": "POLICY_CONFLICT",
                          "source_refs": ["upgrade-east-v2",
                                          "upgrade-notice-v4"],
                          "target_queue": "billing-supervisor-review"})
    # ---- 06 深层缝: 服务端深处不可达 ----
    def test_06_deep_seam_same_terminal_and_idempotent_recovery(self):
        tickets = UnavailableWriteTickets()
        host, _t, approvals, policy = _stack(tickets=tickets)
        run_id = "northstar-w17-t06"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertTrue(envelope["degraded"])
        self.assertEqual(envelope["external_effect_count"], 0)
        self.assertEqual(tickets.ticket_count(), 0)
        record, _status = approvals.status_now(approval["approval_id"],
                                               now=FIXED_NOW)
        self.assertEqual(record["uses"], 1,
                         "深层缝在消耗之后失败, 用量如实入账 (不伪造未消耗)")
        # 同一审批续跑 -> 已用尽 -> 确定性拒绝, 绝不产生第二个效果
        env = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                              approval_id=approval["approval_id"],
                              rag_terminal=RAG_TERMINAL)
        self.assertEqual(env["error"]["code"], W.ERROR_APPROVAL_REQUIRED)
        self.assertEqual(tickets.ticket_count(), 0)
        # 依赖修复 (替换为健康服务) + 重新确认审批 (同一 run_id, 同一幂等键)
        # -> 恰 1 效果; 幂等键保证修复后重放也不会有第二个效果
        healthy = TicketService()
        host._ticket_service = healthy
        approval2 = _approve(approvals, policy, run_id)
        env = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                              approval_id=approval2["approval_id"],
                              rag_terminal=RAG_TERMINAL)
        self.assertEqual(env["status"], W.STATUS_COMPLETED)
        self.assertEqual(healthy.ticket_count(), 1)
        entry = next(e for e in host.audit
                     if (e.get("reason_typed") or "").endswith("deep_seam"))
        self.assertEqual(entry["external_write_effects"], 0)

    # ---- 07 降级零 store 变更 ----
    def test_07_degradation_touches_no_external_state(self):
        reg = KillSwitchRegistry(clock=_clock)
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        host, tickets, approvals, policy = _stack(kill_switch=reg)
        run_id = "northstar-w17-t07"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        before = tickets.snapshot()
        env = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                              approval_id=approval["approval_id"],
                              rag_terminal=RAG_TERMINAL)
        self.assertTrue(env["degraded"])
        self.assertEqual(tickets.snapshot(), before, "降级路径 store 逐字节不变")
    # ---- 08 trace/终态分类 + SLI 抓违规 ----
    def test_08_trace_terminal_and_forbidden_effect(self):
        from mcptool.telemetry import STAGES
        trace = TaskTrace("northstar-w17-t08", kind="write", clock=_clock)
        trace.span(STAGES[0], result="write_request")
        trace.span(STAGES[-1], result="degraded_preview_presented",
                   error_class="mcp_unavailable")
        trace.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=0)
        self.assertEqual(trace.terminal, (TERMINAL_ACTION_PREVIEW, True))
        held = TaskTrace("northstar-w17-t08b", kind="write", clock=_clock)
        held.span(STAGES[0], result="write_request")
        held.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=0)
        ok = TaskTrace("northstar-w17-t08c", kind="write", clock=_clock)
        ok.span(STAGES[0], result="write_request")
        ok.finish("action_completed", effects=1)
        metrics = reduce_runtime_metrics([trace, held, ok])
        self.assertEqual(metrics["terminal_distribution"]
                         [TERMINAL_ACTION_PREVIEW], 2)
        self.assertEqual(metrics["allowed_terminal_rate"],
                         {"numerator": 3, "denominator": 3, "value": 1.0})
        self.assertEqual(metrics["forbidden_effect_count"], 0)
        self.assertEqual(scan_spans_for_violations(trace.spans), [])
        # 违规样本: 降级终态携带效果 -> 必须被 SLI 抓住
        bad = TaskTrace("northstar-w17-t08d", kind="write", clock=_clock)
        bad.span(STAGES[0], result="write_request")
        bad.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=1)
        self.assertEqual(reduce_runtime_metrics([bad])["forbidden_effect_count"],
                         1)

    # ---- 09 恢复复验·注入路 ----
    def test_09_recovery_injection_path_same_run_id_no_duplicate(self):
        probe = WriteChannelProbe()
        host, tickets, approvals, policy = _stack(write_probe=probe)
        run_id = "northstar-w17-t09"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        probe.unavailable = True
        degraded = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertTrue(degraded["degraded"])
        probe.unavailable = False  # 恢复依赖
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertEqual(envelope["status"], W.STATUS_COMPLETED)
        self.assertEqual(envelope["verified_by"], "ticket.get")
        ticket_id = envelope["ticket_id"]
        self.assertEqual(tickets.ticket_count(), 1)
        replay = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                 approval_id=approval["approval_id"],
                                 rag_terminal=RAG_TERMINAL)
        self.assertEqual(replay["ticket_id"], ticket_id)
        self.assertEqual(tickets.ticket_count(), 1, "幂等重放无重复效果")

    # ---- 10 恢复复验·开关路 ----
    def test_10_recovery_switch_path_approved_reset(self):
        reg = KillSwitchRegistry(clock=_clock)
        host, tickets, approvals, policy = _stack(kill_switch=reg)
        run_id = "northstar-w17-t10"
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        degraded = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertTrue(degraded["degraded"])
        reg.clear(SWITCH_MCP_OFF, operator="ops-lead",
                  reason="按批准流程复位")
        self.assertFalse(reg.is_active(SWITCH_MCP_OFF))
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertEqual(envelope["status"], W.STATUS_COMPLETED)
        self.assertEqual(tickets.ticket_count(), 1)
        events = reg.audit_trail()
        self.assertEqual([e["event"] for e in events], ["opened", "closed"])
        self.assertEqual(events[1]["operator"], "ops-lead")
    # ---- 11 恢复后受限任务仍拒绝 + 正常任务完成 ----
    def test_11_after_recovery_restricted_still_denied_normal_completes(self):
        reg = KillSwitchRegistry(clock=_clock)
        reg.set(SWITCH_MCP_OFF, operator="ops-li", reason="演练隔离")
        host, tickets, approvals, policy = _stack(kill_switch=reg)
        run_id = "northstar-w17-t11"
        host.call_write(W.TOOL_PREVIEW, WRITE_ARGS, run_id=run_id,
                        rag_terminal=RAG_TERMINAL)
        approval = _approve(approvals, policy, run_id)
        self.assertTrue(host.call_write(
            W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
            approval_id=approval["approval_id"],
            rag_terminal=RAG_TERMINAL)["degraded"])
        reg.clear(SWITCH_MCP_OFF, operator="ops-lead", reason="按批准复位")
        # 受限任务仍拒绝 (策略外队列, 权限不放宽)
        denied = host.call_write(W.TOOL_PREVIEW, DENIED_ARGS, run_id=run_id,
                                 rag_terminal=RAG_TERMINAL)
        self.assertEqual(denied["status"], W.STATUS_DENIED)
        # 同一故障任务续跑 -> 正常完成, 恰 1 效果
        envelope = host.call_write(W.TOOL_CREATE, WRITE_ARGS, run_id=run_id,
                                   approval_id=approval["approval_id"],
                                   rag_terminal=RAG_TERMINAL)
        self.assertEqual(envelope["status"], W.STATUS_COMPLETED)
        self.assertEqual(tickets.ticket_count(), 1)

    # ---- 12 D-01 模型超时固定演练 ----
    def test_12_drill_d01_model_timeout_system_failed(self):
        trace, row = drill_d01_model_timeout(KillSwitchRegistry(clock=_clock))
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual(trace.terminal[0], "system_failed")
        self.assertFalse(trace.terminal[1])
        self.assertEqual(row["reason_code"], C.ERROR_DEADLINE_EXCEEDED)
        self.assertEqual(trace.effects, 0)

    # ---- 13 D-02 索引过期固定演练 ----
    def test_13_drill_d02_index_stale_abstained_with_switch_audit(self):
        reg = KillSwitchRegistry(clock=_clock)
        trace, row = drill_d02_index_stale(reg)
        self.assertEqual(row["verdict"], "pass")
        self.assertEqual(trace.terminal[0], "abstained")
        self.assertTrue(trace.terminal[1])
        self.assertEqual(row["reason_code"], "index_stale")
        self.assertEqual(trace.effects, 0)
        self.assertEqual([e["event"] for e in reg.audit_trail()],
                         ["opened", "closed"])
        self.assertFalse(reg.is_active(SWITCH_INDEX_STALE))

    # ---- 14 演练全套 ----
    def test_14_full_drill_suite_passes_with_clean_telemetry(self):
        result = run_drills()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(len(result["drills"]), 8)
        self.assertTrue(all(r["verdict"] == "pass" for r in result["drills"]))
        m = result["metrics"]
        self.assertEqual(m["tasks_total"], 8)
        self.assertEqual(m["terminal_distribution"][TERMINAL_ACTION_PREVIEW], 2)
        self.assertEqual(m["terminal_distribution"]["action_completed"], 3)
        self.assertEqual(m["forbidden_effect_count"], 0)
        self.assertEqual(m["unconfirmed_effects"], 0)
        self.assertEqual(sum(t["external_effects"] for t in result["tasks"]), 3)
        self.assertTrue(result["redaction"]["passed"])
        self.assertEqual(len(result["switch_audit"]), 4)  # 2 开关 × open/close
        # 演练 trace 脱敏零违规 (正文/载荷/凭据不入遥测)
        self.assertEqual(scan_spans_for_violations(result["spans"]), [])

    # ---- 15 允许列表与冻结合同不变 ----
    def test_15_allowlist_and_frozen_contract_unchanged(self):
        self.assertEqual(ALLOWED_TOOLS, (
            C.TOOL_NAME, "ticket.preview_create", "ticket.create",
            "ticket.get"))
        # week-15 冻结合同 sha256 未动 (降级是编排层行为, 不是新工具/新参数)
        self.assertEqual(W.CONTRACT_SHA256,
                         "f9afabdc2efdab5e10c20dda23a2d64b16223b88cc443bdcabff"
                         "a1a65fe931f4")
        with self.assertRaises(McpUnavailableError):
            UnavailableWriteTickets().create_review_ticket(
                case_id="x", reason_code="POLICY_CONFLICT", source_refs=["a"],
                target_queue="billing-supervisor-review",
                idempotency_key="k", requested_by="u", tenant_id="t",
                created_at=FIXED_NOW)

    # ---- 16 工作台 API 端到端 (浏览器页面背后的同一组端点) ----
    def test_16_workbench_api_end_to_end(self):
        from fastapi.testclient import TestClient
        from workbench_degradation.app import app
        client = TestClient(app)
        state = client.post("/api/reset").json()
        self.assertEqual(state["ticket_total"], 0)
        self.assertFalse(state["degraded"]["degraded_now"])
        # 正常对照组: 预览 -> 批准 -> 执行 (0 故障)
        preview = client.post("/api/preview").json()
        self.assertEqual(preview["envelope"]["status"], "action_preview")
        client.post("/api/decide", json={"decision": "approve"})
        normal = client.post("/api/execute").json()
        self.assertEqual(normal["envelope"]["status"], "action_completed")
        self.assertEqual(normal["state"]["ticket_total"], 1)
        # 注入 -> 新任务 -> 降级停在 action_preview (0 效果)
        client.post("/api/fault", json={"action": "inject"})
        client.post("/api/preview")
        client.post("/api/decide", json={"decision": "approve"})
        degraded = client.post("/api/execute").json()
        self.assertTrue(degraded["envelope"]["degraded"])
        self.assertEqual(degraded["envelope"]["reason_code"], "mcp_unavailable")
        self.assertEqual(degraded["envelope"]["external_effect_count"], 0)
        self.assertEqual(degraded["state"]["ticket_total"], 0,
                         "降级不加效果 (当前任务独立 store 仍 0)")
        self.assertTrue(degraded["state"]["degraded"]["degraded_now"])
        # 解除注入 -> 续跑同一任务 -> action_completed (恰 1 效果), 幂等重放不变
        client.post("/api/fault", json={"action": "restore"})
        retried = client.post("/api/retry").json()
        self.assertEqual(retried["envelope"]["status"], "action_completed")
        self.assertEqual(retried["state"]["ticket_total"], 1)
        replay = client.post("/api/replay").json()
        self.assertTrue(replay["effect_count_unchanged"])
        self.assertEqual(replay["ticket_total"], 1, "幂等重放无重复效果")
        # 开关开/复位 -> 审计留痕
        client.post("/api/switch", json={"action": "set"})
        state = client.post("/api/switch", json={"action": "clear"}).json()
        self.assertFalse(state["degraded"]["mcp_off_active"])
        self.assertEqual([e["event"] for e in state["state"]["switch_audit"]],
                         ["opened", "closed"])
        # 演练 + 追踪 + 脱敏 + 404
        drills = client.post("/api/drills").json()
        self.assertEqual(drills["verdict"], "pass")
        tasks_now = client.get("/api/state").json()["tasks"]
        self.assertTrue(tasks_now)
        trace = client.get(f"/api/trace/{tasks_now[0]['task_run_id']}")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(
            client.get("/api/trace/run-w17-unknown").status_code, 404)
        redaction = client.post("/api/redaction-check").json()
        self.assertTrue(redaction["passed"])
        # 受控确认前置门: 新预览未批准直接执行 -> 400 (不执行, 不降级)
        client.post("/api/preview")
        resp = client.post("/api/execute")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
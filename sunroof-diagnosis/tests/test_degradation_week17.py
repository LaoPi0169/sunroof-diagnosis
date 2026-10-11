"""Week 17 降级测试 —— 关闭开关 / 降级门 / 恢复续跑 / 演练证据。"""
import json
import os
import unittest

from mcptool.kill_switch import SWITCHES, KillSwitchRegistry
from mcptool.run_fixtures import BASE_ARGS, BINDING_OK, EAST_PQE, _fresh_write_stack
from mcptool.server import call_write

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDegradationWeek17(unittest.TestCase):
    def test_05_registry_closed_enum_and_audit(self):
        registry = KillSwitchRegistry()
        self.assertEqual(set(SWITCHES), {"MCP_OFF", "MODEL_OFF", "INDEX_STALE"})
        with self.assertRaises(ValueError):
            registry.set_state("ALL_OFF", True, "op", "reason")
        with self.assertRaises(ValueError):
            registry.set_state("MCP_OFF", True, "", "")
        registry.set_state("MCP_OFF", True, "admin_li", "依赖故障注入演练")
        self.assertTrue(registry.is_off("MCP_OFF"))
        self.assertEqual(len(registry.audit()), 1)
        registry.set_state("MCP_OFF", False, "admin_li", "恢复复验通过")
        self.assertEqual(len(registry.audit()), 2, "追加式审计: 历史不删除")

    def test_06_degradation_gate_priority(self):
        approvals, tickets = _fresh_write_stack()
        registry = KillSwitchRegistry()
        registry.set_state("MCP_OFF", True, "admin_li", "演练")
        no_approval = call_write(EAST_PQE, dict(BASE_ARGS), approvals, tickets, registry)
        self.assertEqual(no_approval["error"]["code"], "approval_denied",
                         "权限判定优先于降级")
        approval_id = approvals.issue("supervisor_li", BINDING_OK)
        degraded = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                              approvals, tickets, registry)
        self.assertFalse(degraded["ok"])
        self.assertEqual(degraded["error"]["code"], "mcp_unavailable")
        self.assertEqual(degraded["terminal"], "action_preview")
        self.assertEqual(tickets.external_effects(), 0)
        self.assertTrue(approvals.has_uses(approval_id), "降级不消耗审批额度")

    def test_07_recovery_resume_with_same_approval(self):
        approvals, tickets = _fresh_write_stack()
        registry = KillSwitchRegistry()
        registry.set_state("MCP_OFF", True, "admin_li", "演练")
        approval_id = approvals.issue("supervisor_li", BINDING_OK)
        call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                   approvals, tickets, registry)
        registry.set_state("MCP_OFF", False, "admin_li", "恢复复验通过")
        resumed = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                             approvals, tickets, registry)
        self.assertTrue(resumed["ok"] and resumed["result"]["created"])
        self.assertEqual(tickets.external_effects(), 1)
        replay = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                            approvals, tickets, registry)
        self.assertTrue(replay["result"]["replayed"], "恢复后同一幂等键重放无第二效果")

    def test_08_drill_evidence(self):
        from mcptool.run_degradation_drill import run_drill
        out = os.path.join(PROJECT_ROOT, "week-17", "degradation-drill-evidence-v1.jsonl")
        report = run_drill(out)
        self.assertEqual(report["drills"], 4)
        self.assertEqual(report["forbidden"], 0)
        self.assertTrue(os.path.exists(out))
        rows = [json.loads(line) for line in open(out, encoding="utf-8") if line.strip()]
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()

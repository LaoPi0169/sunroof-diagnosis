"""业务用户交互工作台 API 测试 (Week 14 增补) —— HTTP 薄壳复用第 14 周硬门。

覆盖四条 (与 run_fixtures W-01..W-05 一一对应的用户可见子集):
  01 重置+预览: 三重规范化 + 外部效果 0 (W-01 用户面)
  02 拒绝后执行: approval_required + 外部效果 0 (W-05 用户面)
  03 批准后改队列执行: approval_mismatch + 外部效果 0, 额度不消耗 (W-03 用户面)
  04 批准后匹配执行 + 幂等重放 + 核验: SIM-5001, 效果恰 1, 安全事件 0 (W-02/W-04)

运行: python -m unittest tests.test_write_workbench -v
边界: 进程内单会话 (module 级 _STATE), 每个用例 setUp 先 /api/reset 保证确定性;
审批 TTL 用真实时钟, 测试不等待过期路径 (过期由写夹具 W-02 以固定时钟覆盖)。
"""
from __future__ import annotations

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from workbench_write.app import app  # noqa: E402


def _default_form(**overrides) -> dict:
    """与页面默认表单一致 (故意未规范化: 小写原因/乱序引用/大小写混合队列)。"""
    form = {
        "case_id": "NS-1042",
        "reason_code": "policy_conflict",
        "source_refs": "upgrade-notice-v4, upgrade-east-v2",
        "target_queue": "Billing-Supervisor-Review",
    }
    form.update(overrides)
    return form


class WriteWorkbenchApiTest(unittest.TestCase):
    """工作台端到端 API 用例 (浏览器页面背后的同一组端点)。"""

    def setUp(self):
        self.client = TestClient(app)
        resp = self.client.post("/api/reset", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.state = resp.json()
        self.assertEqual(self.state["ticket_total"], 0)

    def _post(self, path, payload):
        resp = self.client.post(path, json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _decide(self, decision):
        return self._post("/api/decide", {"decision": decision})

    def test_01_reset_and_preview_normalized_zero_effect(self):
        data = self._post("/api/preview", _default_form())
        envelope = data["envelope"]
        self.assertEqual(envelope["status"], "action_preview")
        canonical = envelope["canonical_parameters"]
        self.assertEqual(canonical["case_id"], "NS-1042")
        self.assertEqual(canonical["reason_code"], "POLICY_CONFLICT")
        self.assertEqual(canonical["source_refs"],
                         ["upgrade-east-v2", "upgrade-notice-v4"])
        self.assertEqual(canonical["target_queue"], "billing-supervisor-review")
        self.assertEqual(envelope["resource_scope"], "case:NS-1042")
        self.assertEqual(envelope["external_ticket_count"], 0)
        state = data["state"]
        self.assertEqual(state["ticket_total"], 0)
        self.assertEqual(state["external_write_effects"], 0)
        self.assertEqual(state["idempotency_key"].split(":")[1:], ["ticket.create", "1"])

    def test_02_deny_then_execute_rejected_zero_effect(self):
        self._post("/api/preview", _default_form())
        data = self._decide("deny")
        self.assertEqual(data["record"]["decision"], "denied")
        self.assertTrue(data["record"]["approval_id"].startswith("denial-run-w14-"))
        data = self._post("/api/execute", _default_form())
        envelope = data["envelope"]
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "approval_required")
        self.assertEqual(set(envelope), {"status", "tool_version", "error"},
                         "用户面错误封套不得携带审批内部细节")
        state = data["state"]
        self.assertEqual(state["decision_effective_status"], "denied")
        self.assertEqual(state["ticket_total"], 0)
        self.assertEqual(state["external_write_effects"], 0)


    def test_03_approve_then_tampered_queue_is_mismatch(self):
        self._post("/api/preview", _default_form())
        data = self._decide("approve")
        self.assertEqual(data["record"]["decision"], "approved")
        data = self._post("/api/execute", _default_form(target_queue="executive-review"))
        envelope = data["envelope"]
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "approval_mismatch")
        state = data["state"]
        self.assertEqual(state["ticket_total"], 0)
        self.assertEqual(state["external_write_effects"], 0)
        self.assertEqual(state["decision_record_restricted"]["uses"], 0,
                         "不匹配执行不得消耗审批额度")
        self.assertTrue(any(
            e["tool"] == "ticket.create" and e["outcome"] == "error"
            and (e.get("reason_typed") or "").startswith("binding:")
            for e in state["audit_restricted"]),
            "细分原因 (binding:approved_parameters) 只进受限审计")

    def test_04_execute_replay_verify_effect_exactly_one(self):
        run_id = self.state["run_id"]
        self._post("/api/preview", _default_form())
        self._decide("approve")
        first = self._post("/api/execute", _default_form())["envelope"]
        self.assertEqual(first["status"], "action_completed")
        self.assertEqual(first["ticket_id"], "SIM-5001")
        self.assertEqual(first["verified_by"], "ticket.get")
        self.assertEqual(first["idempotency_key"], f"{run_id}:ticket.create:1")
        self.assertEqual(first["external_ticket_count"], 1)

        replay = self._post("/api/execute", _default_form())["envelope"]
        self.assertEqual(replay["status"], "action_completed")
        self.assertEqual(replay["ticket_id"], "SIM-5001")

        verify = self._post("/api/verify", {"ticket_id": "SIM-5001"})["envelope"]
        self.assertEqual(verify["status"], "ok")
        record = verify["records"][0]
        self.assertEqual(record["state"], "open")
        self.assertEqual(record["target_queue"], "billing-supervisor-review")
        self.assertEqual(record["requested_by"], "user-zhou-ning")

        state = self._post("/api/verify", {"ticket_id": "SIM-5001"})["state"]
        self.assertEqual(state["ticket_total"], 1)
        self.assertEqual(state["external_write_effects"], 1,
                         "四次调用 (创建/重放/查询×2) 合计外部效果恰 1")
        self.assertEqual(state["security_events"], [])
        self.assertEqual(state["decision_record_restricted"]["uses"], 1)


if __name__ == "__main__":
    unittest.main()


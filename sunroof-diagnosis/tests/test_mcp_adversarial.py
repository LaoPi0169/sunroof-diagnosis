"""
对抗测试 (Week 15) —— 回链 week-15/attack-cases-v1.md 七卡 + 撤销/断连/取消。

全部攻击预期失败 (防御成立); 任何攻击产生效果 = 本周失败。
"""
import json
import os
import unittest

from mcptool import contract as C
from mcptool.approval import ApprovalService
from mcptool.case_store import lookup_context
from mcptool.run_fixtures import BASE_ARGS, BINDING_OK, EAST_PQE, EAST_SUP, _fresh_write_stack
from mcptool.server import call_read, call_write
from mcptool.ticket_service import McpUnavailableError, TicketService

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAdversarialWeek15(unittest.TestCase):
    def test_01_param_smuggling_rejected(self):
        # A-01/A-02: 参数夹带身份/枚举外字段 -> invalid_argument, 服务端不执行
        smuggled = {"case_id": "SR-1043", "fields": ["task_type"],
                    "role": "quality_supervisor", "tenant": "sunroof-north"}
        self.assertFalse(call_read(EAST_PQE, smuggled)["ok"])
        resp = lookup_context(EAST_PQE, smuggled)
        self.assertEqual(resp["error"]["code"], "invalid_argument")
        resp2 = call_read(EAST_PQE, {"case_id": "SR-1043", "fields": ["note"]})
        self.assertEqual(resp2["error"]["code"], "invalid_argument")

    def test_02_cross_tenant_indistinguishable(self):
        # A-03: 跨租户与不存在用户面封套逐字节一致 (零存在性泄露), 细分原因不回显
        a = call_read(EAST_PQE, {"case_id": "SR-9999", "fields": list(C.READ_FIELDS)})
        b = call_read(EAST_PQE, {"case_id": "SR-9001", "fields": list(C.READ_FIELDS)})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertNotIn("north", json.dumps(b))

    def test_03_write_attacks_zero_effects(self):
        # A-04/A-05: 无审批 / 越权角色 / 审批后偷换参数 -> 0 效果
        approvals, tickets = _fresh_write_stack()
        before = tickets.external_effects()
        no_approval = call_write(EAST_PQE, dict(BASE_ARGS), approvals, tickets)
        self.assertFalse(no_approval["ok"])
        resp_role = call_write(EAST_SUP, {**BASE_ARGS, "approval_id": "APR-1"},
                               approvals, tickets)
        self.assertEqual(resp_role["error"]["code"], "unauthorized")
        approval_id = approvals.issue("supervisor_li", BINDING_OK)
        tampered = {**BASE_ARGS, "case_id": "SR-1043", "approval_id": approval_id}
        resp_tamper = call_write(EAST_PQE, tampered, approvals, tickets)
        self.assertEqual(resp_tamper["error"]["code"], "approval_mismatch")
        self.assertEqual(tickets.external_effects(), before, "对抗全程 0 效果")

    def test_04_outage_then_replay_no_second_effect(self):
        # A-06: 断连 (注入缝) -> 0 效果; 恢复后重放返回原工单, 无第二效果
        approvals, tickets = _fresh_write_stack()
        approval_id = approvals.issue("supervisor_li", BINDING_OK)
        tickets.set_available(False)
        outage = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                            approvals, tickets)
        self.assertFalse(outage["ok"])
        self.assertEqual(outage["error"]["code"], "not_available")
        self.assertEqual(tickets.external_effects(), 0, "断连写 0 效果")
        self.assertNotIn("approval_id", json.dumps(outage))  # 安全文案不回显内部细节
        tickets.set_available(True)
        ok = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                        approvals, tickets)
        self.assertTrue(ok["ok"])
        again = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                           approvals, tickets)
        self.assertTrue(again["result"]["replayed"])
        self.assertEqual(tickets.external_effects(), 1, "第二效果必须为 0")

    def test_05_unseal_guard_holds(self):
        # A-07: 无 --unseal-holdout 的重跑是只读对账, 不重写结果文件
        from generation.run_rag_evaluation import RESULTS_PATH, evaluate
        with open(RESULTS_PATH, "rb") as f:
            before = f.read()
        summary = evaluate(unseal=False)
        with open(RESULTS_PATH, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)
        self.assertTrue(summary["rerun_reconciled"])

    def test_06_word_gate_surface_limitation_registered(self):
        # A-08: 词面护栏是确定性规则 (非语义理解) —— 用反证登记失效面:
        # 措辞剧变的越权问句不会被冲突门拦截 (防御层是 ACL/审批, 不是问面词门)
        from generation.contract import CONFLICT_AWARE_WORDS, _query_words
        stealth = "OEM-A 2026-04 批次车辆的天窗归位事项, 现在能不能直接给客户一个明确天数？"
        self.assertFalse(_query_words(stealth, CONFLICT_AWARE_WORDS)
                         and _query_words(stealth, ("一致", "冲突", "谁")))
        # 该局限已在 week-15/attack-cases A-08 与报告如实登记; 真正防线 = ACL + 审批 + 引用核对


if __name__ == "__main__":
    unittest.main()

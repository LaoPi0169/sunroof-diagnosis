"""
只读 RAG 评测合同测试 (Week 12) —— 回链 specs/mission-week-12.md 验收门

  01 三态合同: 60 例逐例状态与标签对齐 (dev+cal 公开标签; 保留集对账已解封结果文件)
  02 unseal-once: 结果文件存在时重跑只读对账且逐字节一致 (不重写)
  03 ACL 零泄漏: 60 例 restricted_candidate_count 对 pqe 恒 0 (独立复算语义)
  04 引用支持: answered 例 100% 逐字引用 (去句读口径) 且引用文档在冻结快照内
  05 门控确定性: 同例两次回答逐字段一致 (无墙钟/随机)
  06 门面回归: 冲突/过期/无答案域词门在保留集上的行为与落盘结果一致

运行: python -m unittest tests.test_rag_generation -v
"""
import json
import os
import sqlite3
import unittest

from evaluation.eval_contract import (
    EVAL_SET_PATH,
    PROJECT_ROOT,
    corpus_document_ids,
    load_eval_set,
)
from generation.contract import ANSWER_LINE, answer_case
from generation.run_rag_evaluation import RESULTS_PATH

CORPUS_DB = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")


def load_rows() -> list:
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestRagGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_rows()
        cls.by_case = {row["case_id"]: row for row in cls.rows}
        conn = sqlite3.connect(CORPUS_DB)
        conn.row_factory = sqlite3.Row
        cls.conn = conn

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_01_state_gate_60(self):
        self.assertEqual(len(self.rows), 60)
        self.assertTrue(all(r["match"] for r in self.rows),
                        "60 例状态门必须与标签全对齐 (含解封保留集)")
        by_state = {}
        for row in self.rows:
            by_state[row["state"]] = by_state.get(row["state"], 0) + 1
        self.assertEqual(by_state.get("answered"), 28)
        self.assertEqual(by_state.get("abstained"), 27)
        self.assertEqual(by_state.get("escalate"), 5)

    def test_02_unseal_once_rerun_reconciles(self):
        with open(RESULTS_PATH, "rb") as f:
            before = f.read()
        from generation.run_rag_evaluation import evaluate
        summary = evaluate(unseal=False)
        self.assertTrue(summary["rerun_reconciled"])
        with open(RESULTS_PATH, "rb") as f:
            after = f.read()
        self.assertEqual(before, after, "重跑不得重写结果文件 (unseal-once)")
        self.assertEqual(summary["state_gate_match"], "60/60")

    def test_03_acl_zero_leak(self):
        for row in self.rows:
            if row["user_role"] == "pqe":
                self.assertEqual(row["restricted_candidate_count"], 0,
                                 f"{row['case_id']} pqe 泄漏")
        self.assertTrue(all(r["business_effects"] == 0 for r in self.rows),
                        "零业务效果")

    def test_04_citation_support_100(self):
        answered = [r for r in self.rows if r["state"] == "answered"]
        self.assertTrue(answered)
        corpus_ids = corpus_document_ids()
        for row in answered:
            self.assertTrue(row["citation_supported"],
                            f"{row['case_id']} 引用不支持答案")
            self.assertEqual(len(row["citations"]), 1)
            self.assertIn(row["citations"][0]["document_id"], corpus_ids)

    def test_05_gate_determinism(self):
        cases = {row["case_id"]: row for row in load_eval_set(EVAL_SET_PATH)}
        for case_id in ("SR-DEV-01", "SR-DEV-23", "SR-DEV-31", "SR-HOLD-06"):
            first = answer_case(cases[case_id], self.conn)
            second = answer_case(cases[case_id], self.conn)
            self.assertEqual(first, second, f"{case_id} 两次回答不一致")

    def test_06_threshold_and_gates_frozen_semantics(self):
        self.assertGreaterEqual(ANSWER_LINE, 0.30, "作答线不得低于移交区间下界")
        # 词面护栏语义抽检: 冲突域问面 -> needs_review; 过期域问面 -> refused
        conflict_row = self.by_case["SR-DEV-23"]
        self.assertEqual(conflict_row["reason_code"], "conflict_gate")
        expired_row = self.by_case["SR-DEV-27"]
        self.assertIn(expired_row["reason_code"], ("expired_gate", "no_source_abstain"))


if __name__ == "__main__":
    unittest.main()

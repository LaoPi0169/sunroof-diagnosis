"""
评测合同冻结测试 (Week 10) —— 回链 specs/mission-week-10.md 验收标准

覆盖六项:
  01 数量矩阵: 36/12/12 与 24/10/8/8/10 精确成立 (split × 期望状态族, 验收门第 3/4 条)
  02 输入 schema: 每行必备输入字段齐全, case_id 唯一, 角色/类别/版本绑定合法 (第 6 条)
  03 语料引用: gold/forbidden 文档必须存在于第 9 周快照清单 (考卷回到冻结语料)
  04 近重复与封存: 归一化 query 跨 split 零重复; 输入行不携带任何标签字段
  05 保留集封存: 标签文件 sha256 与冻结记录一致, 键与保留集 case_id 一一对应 (第 7/13 条)
  06 确定性基线: 两次运行逐字节一致, 逐例含精确 release 且零业务效果 (第 10/13 条)

运行: python -m unittest tests.test_eval_contract -v
说明: 零模型调用; 权限与关键事实由确定性规则裁决 (第 9 条)。
"""
import hashlib
import json
import os
import re
import unittest

from evaluation import eval_contract as ec
from evaluation.run_deterministic_baseline import main as run_baseline_main

PROJECT_ROOT = ec.PROJECT_ROOT


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestEvalContract(unittest.TestCase):
    """60 条评测集与冻结件的合同测试。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = ec.load_eval_set()
        cls.holdout_labels = ec.load_holdout_labels()

    # -- 01 数量矩阵 -------------------------------------------------------
    def test_01_matrix_counts_exact(self):
        by_split = {}
        by_family = {}
        by_cell = {}
        for row in self.rows:
            split = row["split"]
            family = ec.family_of(row)
            by_split[split] = by_split.get(split, 0) + 1
            by_family[family] = by_family.get(family, 0) + 1
            cell = by_cell.setdefault(split, {})
            cell[family] = cell.get(family, 0) + 1
        self.assertEqual(by_split, ec.SPLIT_TOTALS,
                         "split 总数必须精确等于 36/12/12")
        self.assertEqual(by_family, ec.FAMILY_TOTALS,
                         "状态族总数必须精确等于 24/10/8/8/10")
        for split, expected in ec.FAMILY_BY_SPLIT.items():
            self.assertEqual(by_cell[split], expected,
                             f"{split} 的状态族细分必须精确匹配")

    # -- 02 输入 schema ----------------------------------------------------
    def test_02_input_schema_complete(self):
        seen_ids = set()
        for row in self.rows:
            for field in ec.REQUIRED_INPUT_FIELDS:
                self.assertIn(field, row, f"{row.get('case_id')} 缺少输入字段 {field}")
            for label_field in ec.LABEL_FIELDS:
                self.assertNotIn(label_field, row,
                                 f"{row['case_id']} 输入行不得携带标签字段 (封存)")
            self.assertEqual(row["dataset_version"], ec.DATASET_VERSION)
            self.assertEqual(row["corpus_version"], ec.CORPUS_VERSION)
            self.assertIn(row["split"], ec.SPLIT_TOTALS)
            self.assertIn(row["user_role"], ec.ROLES)
            self.assertIn(row["task_context"]["category"], ec.CATEGORY_CONTRACTS)
            self.assertTrue(row["query"].strip())
            self.assertTrue(row["risk_tags"], f"{row['case_id']} 缺少风险标签")
            self.assertTrue(row["provenance"])
            self.assertNotIn(row["case_id"], seen_ids, "case_id 必须唯一")
            seen_ids.add(row["case_id"])

    # -- 03 语料引用 -------------------------------------------------------
    def test_03_corpus_references_resolve(self):
        corpus_ids = ec.corpus_document_ids()
        self.assertTrue(corpus_ids, "语料清单为空, 先构建第 9 周快照")
        # 类别 -> 参考文档的语义绑定必须落在第 9 周快照内 (合同常量自检)
        for category, contract in ec.CATEGORY_CONTRACTS.items():
            for doc_id in contract["reference_docs"]:
                self.assertIn(doc_id, corpus_ids,
                              f"类别 {category} 的参考文档不在快照: {doc_id}")
        # 封存标签中的 gold/forbidden 引用也必须可解析
        for case_id, label in self.holdout_labels.items():
            for doc_id in label.get("gold_document_ids", []):
                self.assertIn(doc_id, corpus_ids,
                              f"{case_id} gold 文档不在第 9 周快照: {doc_id}")
            for doc_id in label.get("forbidden_document_ids", []):
                self.assertIn(doc_id, corpus_ids,
                              f"{case_id} forbidden 文档不在第 9 周快照: {doc_id}")
        self.assertEqual(len(self.rows), 60)

    # -- 04 近重复与封存边界 ------------------------------------------------
    def test_04_near_duplicate_and_sealed_inputs(self):
        def normalize(text: str) -> str:
            return re.sub(r"[\s，。、,.?？!！：:；;（）()\-—]", "", text).lower()

        seen = {}
        for row in self.rows:
            key = normalize(row["query"])
            self.assertNotIn(key, seen,
                             f"归一化 query 重复: {seen.get(key)} vs {row['case_id']}")
            seen[key] = row["case_id"]
        # 封存证据: 输入行不携带任何标签字段 (标签只在封存件与指南附录)
        for row in self.rows:
            for label_field in ec.LABEL_FIELDS:
                self.assertNotIn(label_field, row,
                                 f"{row['case_id']} 输入行泄漏标签字段 {label_field}")

    # -- 05 保留集封存 ------------------------------------------------------
    def test_05_holdout_labels_sealed(self):
        holdout_ids = {row["case_id"] for row in self.rows if row["split"] == "holdout"}
        self.assertEqual(set(self.holdout_labels.keys()), holdout_ids,
                         "封存标签键必须与保留集 case_id 一一对应")
        corpus_ids = ec.corpus_document_ids()
        for case_id, label in self.holdout_labels.items():
            self.assertIn(label["expected_state"], ec.EXPECTED_STATES,
                          f"{case_id} 期望状态非法")
            for doc_id in label.get("gold_document_ids", []):
                self.assertIn(doc_id, corpus_ids, f"{case_id} gold 文档不在快照: {doc_id}")
            for doc_id in label.get("forbidden_document_ids", []):
                self.assertIn(doc_id, corpus_ids,
                              f"{case_id} forbidden 文档不在快照: {doc_id}")
            self.assertTrue(label.get("required_facts"), f"{case_id} 缺必要事实")
            self.assertTrue(label.get("forbidden_facts"), f"{case_id} 缺禁止事实")
        # sha256 与冻结记录一致 (防封存件被静默改动)
        freeze_path = os.path.join(PROJECT_ROOT, "evaluation-freeze-record-v1.md")
        self.assertTrue(os.path.exists(freeze_path), "evaluation-freeze-record-v1.md 缺失")
        with open(freeze_path, "r", encoding="utf-8") as f:
            freeze_text = f.read()
        sealed_sha = _sha256(ec.HOLDOUT_LABELS_PATH)
        match = re.search(r"eval-holdout-labels-sealed-v1\.json\s*\|*\s*`?([0-9a-f]{64})`?",
                          freeze_text)
        self.assertIsNotNone(match, "冻结记录未登记封存标签 sha256")
        self.assertEqual(match.group(1), sealed_sha,
                         "封存标签文件已被改动: sha256 与冻结记录不一致")

    # -- 06 确定性基线可复现 ------------------------------------------------
    def test_06_deterministic_baseline_reproducible(self):
        rerun_path = ec.BASELINE_RESULTS_PATH + ".rerun"
        run_baseline_main(["--out", ec.BASELINE_RESULTS_PATH])
        with open(ec.BASELINE_RESULTS_PATH, "rb") as f:
            first = f.read()
        run_baseline_main(["--out", rerun_path])
        with open(rerun_path, "rb") as f:
            second = f.read()
        self.assertEqual(first, second, "确定性基线两次运行必须逐字节一致")
        os.remove(rerun_path)
        binding = ec.load_release_binding()
        lines = first.decode("utf-8").strip().splitlines()
        self.assertEqual(len(lines), 60, "基线结果必须逐例覆盖 60 条")
        statuses = {}
        for line in lines:
            record = json.loads(line)
            self.assertEqual(record["baseline_release"], binding["release_id"],
                             "逐例输出必须携带精确 release")
            self.assertEqual(record["business_effects"], 0, "基线探针零业务效果")
            statuses[record["status"]] = statuses.get(record["status"], 0) + 1
        self.assertEqual(statuses.get("sealed"), 12, "保留集 12 条必须全部 sealed")
        self.assertEqual(statuses.get("not_applicable"), 20,
                         "引擎域外 20 条必须显式 not_applicable (不硬跑)")
        self.assertEqual(statuses.get("answered"), 24, "引擎内 24 条 answered")
        self.assertEqual(statuses.get("needs_review"), 4,
                         "主管可见特殊退款夹具双版本 -> 4 条 needs_review (POLICY_CONFLICT)")
        self.assertNotIn("refused", statuses,
                         "60 条内无 unknown_role 案例 (基线如实输出, 无 refused 路径)")
        self.assertNotIn("escalated", statuses,
                         "确定性引擎无升级路径 (受限语义由标签评估, 非引擎状态)")


if __name__ == "__main__":
    unittest.main()



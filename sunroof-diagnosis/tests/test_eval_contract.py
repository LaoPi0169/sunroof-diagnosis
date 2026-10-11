"""
评测合同冻结测试 (Week 10) —— 回链 specs/mission-week-10.md 验收标准

覆盖六项:
  01 数量矩阵: 36/12/12 与 24/10/8/8/10 精确成立 (split × 期望状态族, 验收门第 3/4 条)
  02 输入 schema: 每行必备输入字段齐全, case_id 唯一, 角色/类别/版本绑定合法 (第 6 条)
  03 语料引用: gold/forbidden 文档必须存在于第 9 周快照清单 (考卷回到冻结语料)
  04 近重复与封存: 归一化 query 跨 split 零重复; 输入行不携带任何标签字段
  05 保留集封存: 标签文件 sha256(LF) 与冻结记录一致, 键与保留集 case_id 一一对应 (第 7/13 条)
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


def _sha256_lf(path: str) -> str:
    """LF 归一化 sha256 (行尾不敏感, 内容锁定强度不变; 防 CRLF 搅动误报)。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


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
        for category, contract in ec.CATEGORY_CONTRACTS.items():
            for doc_id in contract["reference_docs"]:
                self.assertIn(doc_id, corpus_ids,
                              f"类别 {category} 参考文档不在快照: {doc_id}")
        # 校验器全量复核 (退出码 0 = VALID)
        from evaluation.validate_eval_set import main as validate_main
        try:
            validate_main([])
        except SystemExit as exc:
            self.assertEqual(exc.code, 0, "评测集校验器必须 verdict=VALID")

    # -- 04 近重复与封存边界 ------------------------------------------------
    def test_04_near_duplicate_and_seal_boundary(self):
        from evaluation.validate_eval_set import normalize_query
        seen = {}
        for row in self.rows:
            normalized = normalize_query(row["query"])
            self.assertNotIn(normalized, seen,
                             f"{row['case_id']} 与 {seen.get(normalized)} 归一化 query 重复")
            seen[normalized] = row["case_id"]
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
        # sha256(LF) 与冻结记录一致 (防封存件被静默改动; 口径: LF 归一化, 行尾不敏感)
        freeze_path = os.path.join(PROJECT_ROOT, "evaluation-freeze-record-v1.md")
        self.assertTrue(os.path.exists(freeze_path), "evaluation-freeze-record-v1.md 缺失")
        with open(freeze_path, "r", encoding="utf-8") as f:
            freeze_text = f.read()
        sealed_sha = _sha256_lf(ec.HOLDOUT_LABELS_PATH)
        row = next((ln for ln in freeze_text.splitlines()
                    if "eval-holdout-labels-sealed-v1.json" in ln and "sha256" not in ln), "")
        hashes = re.findall(r"[0-9a-f]{64}", row)
        self.assertTrue(hashes, "冻结记录未登记封存标签 sha256")
        self.assertIn(sealed_sha, hashes,
                      "封存标签文件已被改动: sha256(LF) 与冻结记录不一致")

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
        reasons = {}
        for line in lines:
            record = json.loads(line)
            self.assertEqual(record["baseline_release"], binding["release_id"],
                             "逐例输出必须携带精确 release")
            self.assertEqual(record["business_effects"], 0, "基线探针零业务效果")
            statuses[record["status"]] = statuses.get(record["status"], 0) + 1
            if record["reason_code"]:
                reasons[record["reason_code"]] = reasons.get(record["reason_code"], 0) + 1
        self.assertEqual(statuses.get("sealed"), 12, "保留集 12 条必须全部 sealed")
        self.assertEqual(statuses.get("not_applicable"), 20,
                         "引擎域外 20 条必须显式 not_applicable (不硬跑)")
        # 本仓库第 2 周引擎真实 ACL 语义 (与参考模板 24/4 预设不同, 如实锁定):
        #   answered 20 = 16 例 answerable-pqe(唯一现行命中) + 4 例 restricted-supervisor;
        #   needs_review 8 = 4 例 answerable-supervisor(跨项目双现行版本 SPEC_CONFLICT)
        #                    + 4 例 restricted-pqe(OEM-B 专属件零命中 NO_MATCHING_SPEC)。
        self.assertEqual(statuses.get("answered"), 20, "引擎内 20 条 answered")
        self.assertEqual(statuses.get("needs_review"), 8, "引擎内 8 条 needs_review")
        self.assertEqual(reasons.get("SPEC_CONFLICT"), 4,
                         "主管跨项目可见同零件双现行版本 -> 4 条 SPEC_CONFLICT")
        self.assertEqual(reasons.get("NO_MATCHING_SPEC"), 4,
                         "pqe 对 OEM-B 专属件 ACL 零命中 -> 4 条 NO_MATCHING_SPEC")
        self.assertNotIn("refused", statuses,
                         "60 条内无 unknown_role 案例 (基线如实输出, 无 refused 路径)")
        self.assertNotIn("escalated", statuses,
                         "确定性引擎无升级路径 (受限语义由标签评估, 非引擎状态)")


if __name__ == "__main__":
    unittest.main()


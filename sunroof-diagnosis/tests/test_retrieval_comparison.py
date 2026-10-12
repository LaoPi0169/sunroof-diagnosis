"""
检索比较合同测试 (Week 11) —— 回链 specs/mission-week-11.md 验收门

  01 清单互锁: 三输入件 sha256(LF) 与冻结清单一致; 协议字段齐备 (门 2)
  02 结果 schema: 20 字段 × 96 行逐字段断言; ACL 零泄漏用 candidates_for 独立复算 (门 4/5)
  03 确定性: 规范投影两次运行逐行一致且与落盘文件一致 (门 6)
  04 holdout 零执行: 96 行 split 全为 dev/cal (门 3)
  05 失败登记对账: 6 条 F11 与切片可复算 (门 8)
  06 回归种子: SR-01..12 逐条对结果文件复验 (门 9)

运行: python -m unittest tests.test_retrieval_comparison -v
"""
import csv
import hashlib
import json
import os
import unittest

from corpus.build_corpus import build_corpus  # noqa: E402  (冷启动自愈)
from corpus.check_role_candidates import candidates_for
from evaluation.eval_contract import CORPUS_MANIFEST_PATH, EVAL_SET_PATH, PROJECT_ROOT
from retrieval.labels import GUIDE_PATH, load_public_labels

WEEK11_DIR = os.path.join(PROJECT_ROOT, "week-11")
MANIFEST_PATH = os.path.join(WEEK11_DIR, "retrieval-comparison-manifest-v1.json")
RESULT_FILES = {
    "keyword": os.path.join(WEEK11_DIR, "keyword-results-v1.jsonl"),
    "vector": os.path.join(WEEK11_DIR, "vector-results-v1.jsonl"),
}
RESULT_FIELDS = (
    "case_id", "retriever_id", "user_role", "split", "category", "top_k",
    "pool_size", "retrieved_chunk_ids", "retrieved_document_ids",
    "gold_document_ids", "recall_denominator", "recall_at_5", "best_gold_rank",
    "rank1_hit", "restricted_candidate_count", "confidence_top1",
    "corpus_snapshot", "request_id", "cost_estimate_usd", "latency_ms",
)


def sha256_lf(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def load_rows() -> list:
    rows = []
    for retriever, path in RESULT_FILES.items():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


class TestRetrievalComparison(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_rows()
        corpus_db = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
        if not os.path.exists(corpus_db):
            # 冷启动自愈 (全新 checkout / Linux CI: data/ 不入版本库):
            # test_02/test_03 直连语料库, 先确定性重建, 不触碰冻结清单
            build_corpus(corpus_db, write_manifest=False)

    def test_01_manifest_interlock_and_protocol(self):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        binding = manifest["input_bindings"]
        self.assertEqual(binding["eval_set"]["sha256_lf"], sha256_lf(EVAL_SET_PATH),
                         "eval-set sha256 与清单不一致")
        self.assertEqual(binding["labeling_guide"]["sha256_lf"], sha256_lf(GUIDE_PATH),
                         "labeling-guide sha256 与清单不一致")
        self.assertEqual(binding["corpus_manifest"]["sha256_lf"],
                         sha256_lf(CORPUS_MANIFEST_PATH), "corpus-manifest sha256 与清单不一致")
        protocol = manifest["protocol"]
        self.assertEqual(protocol["top_k"], 5)
        self.assertEqual(protocol["holdout_policy"], "not_executed")
        self.assertIn("Recall@5", protocol["primary_metric"])
        self.assertIn("vector-hashed-ngram-v1", json.dumps(protocol["retrievers"]))
        # 标签装载器: 48 例公开标签与考卷 dev+cal 一一对应
        labels = load_public_labels()
        self.assertEqual(len(labels), 48)

    def test_02_schema_20_fields_and_acl_independent(self):
        self.assertEqual(len(self.rows), 96, "48 例 × 2 检索器 = 96 行")
        import sqlite3
        conn = sqlite3.connect(os.path.join(PROJECT_ROOT, "data", "corpus-w09.db"))
        conn.row_factory = sqlite3.Row
        try:
            allowed = {
                role: {c["chunk_id"] for c in candidates_for(conn, role, "sunroof-corpus-w09-v1")}
                for role in ("pqe", "quality_supervisor")
            }
        finally:
            conn.close()
        for row in self.rows:
            for field in RESULT_FIELDS:
                self.assertIn(field, row, f"{row.get('case_id')} 缺字段 {field}")
            self.assertEqual(row["top_k"], 5)
            self.assertEqual(row["cost_estimate_usd"], 0, "零生成零成本")
            self.assertEqual(len(row["retrieved_chunk_ids"]), 5, "top-5 恒 5 条")
            # ACL 独立复算: 不轻信实现输出的 restricted_candidate_count
            for chunk_id in row["retrieved_chunk_ids"]:
                self.assertIn(chunk_id, allowed[row["user_role"]],
                              f"{row['case_id']} 检索到角色不可见 chunk: {chunk_id}")
            if row["user_role"] == "pqe":
                self.assertEqual(row["restricted_candidate_count"], 0,
                                 "pqe 受限候选逐例必须为 0")

    def test_03_deterministic_projection(self):
        from retrieval.run_retrieval_comparison import RESULT_FILES as rf, main as run_main
        before = {}
        for name, path in rf.items():
            with open(path, "rb") as f:
                before[name] = f.read()
        rerun_dir = os.path.join(WEEK11_DIR, "_rerun")
        os.makedirs(rerun_dir, exist_ok=True)
        # 独立第二遍: 用同协议重跑 keyword 单检索器并比对落盘 (投影口径)
        import sqlite3
        from retrieval.contract import RETRIEVER_IDS, TOP_K, retrieve
        from evaluation.eval_contract import load_eval_set
        conn = sqlite3.connect(os.path.join(PROJECT_ROOT, "data", "corpus-w09.db"))
        conn.row_factory = sqlite3.Row
        try:
            cases = [r for r in load_eval_set(EVAL_SET_PATH)
                     if r["split"] in ("development", "calibration")]
            labels = load_public_labels()
            from retrieval.run_retrieval_comparison import execute_case
            rerun = [execute_case(case, labels[case["case_id"]], "keyword", conn)
                     for case in cases]
        finally:
            conn.close()
        stored = [json.loads(line) for line in before["keyword"].decode("utf-8").splitlines()]
        proj = lambda row: {k: row[k] for k in RESULT_FIELDS if k != "latency_ms"}
        self.assertEqual([proj(r) for r in rerun], [proj(r) for r in stored],
                         "重跑规范投影与落盘文件必须逐行一致")
        for name, data in before.items():
            with open(rf[name], "wb") as f:
                f.write(data)
        self.assertEqual(sha256_lf(rf["keyword"]),
                         sha256_lf(os.path.join(WEEK11_DIR, "keyword-results-v1.jsonl")))

    def test_04_holdout_never_executed(self):
        for row in self.rows:
            self.assertIn(row["split"], ("development", "calibration"),
                          f"{row['case_id']} 保留集被执行!")
        with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
            eval_rows = [json.loads(line) for line in f if line.strip()]
        holdout = {r["case_id"] for r in eval_rows if r["split"] == "holdout"}
        executed = {row["case_id"] for row in self.rows}
        self.assertEqual(len(holdout & executed), 0, "保留集 12 条必须零执行")

    def test_05_failure_register_reconciles(self):
        with open(os.path.join(WEEK11_DIR, "retrieval-failure-register-v1.md"),
                  encoding="utf-8") as f:
            text = f.read()
        for fid in ("F11-01", "F11-02", "F11-03", "F11-04", "F11-05", "F11-06"):
            self.assertIn(fid, text, f"失败登记缺 {fid}")
        # 切片对账: conflict/expired 族 recall=0 且 gold chunk 恒 0 (corpus 层事实)
        for row in self.rows:
            if row["category"] == "retrofit_conflict":
                self.assertEqual(row["recall_at_5"], 0.0,
                                 "冲突件零 chunk, recall 必为 0 (F11-01)")
            if row["category"] == "expired_reference":
                self.assertEqual(row["recall_at_5"], 0.0,
                                 "过期件零 chunk, recall 必为 0 (F11-02)")

    def test_06_regression_seeds_hold(self):
        by_key = {(row["case_id"], row["retriever_id"]): row for row in self.rows}
        with open(os.path.join(WEEK11_DIR, "retrieval-regression-seeds-v1.jsonl"),
                  encoding="utf-8") as f:
            seeds = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(seeds), 12, "回归种子 12 条 (SR-01..12)")
        for seed in seeds:
            row = by_key.get((seed["case_id"], seed["retriever_id"]))
            self.assertIsNotNone(row, f"种子无对应结果行: {seed['case_id']}")
            for clause in seed["locked_expectation"].split(";"):
                key, _, value = clause.partition("=")
                if key == "gold":
                    for doc_id in value.split("+"):
                        self.assertIn(doc_id, row["gold_document_ids"])
                elif key == "best_gold_rank":
                    self.assertEqual(row["best_gold_rank"], int(value),
                                     f"{seed['case_id']} 名次漂移")
                elif key == "recall_at_5":
                    self.assertEqual(row["recall_at_5"], float(value))
                elif key == "recall_denominator":
                    self.assertEqual(row["recall_denominator"], int(value))
                elif key == "restricted_candidate_count":
                    self.assertEqual(row["restricted_candidate_count"], int(value))
                elif key == "confidence_top1>=":
                    self.assertGreaterEqual(row["confidence_top1"], float(value))
                elif key == "confidence_top1<=":
                    self.assertLessEqual(row["confidence_top1"], float(value))


if __name__ == "__main__":
    unittest.main()


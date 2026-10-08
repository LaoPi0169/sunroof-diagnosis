"""
检索比较合同测试 (Week 11) —— 回链 specs/mission-week-11.md 验收标准

覆盖六项:
  01 比较清单冻结: manifest 输入 sha256 与当前文件一致, 协议字段与合同常量互锁
  02 统一合同与 ACL 零泄漏: 48 例 x 双检索器 schema 完整, 每个 ranked chunk 均在
     candidates_for(role) 允许池内, agent 受限观测数逐例 0 (独立复算)
  03 确定性重跑: 排除 latency_ms 的规范投影两次运行一致, 且与已落盘文件一致
  04 保留集未运行: 两个结果文件各 48 行, 零 NS-HOLD-*; manifest 声明 not_executed
  05 失败登记交叉核验: >=5 条, 层枚举合法, case/retriever 真实存在,
     retrieval 层条目的 gold 确实缺失 (与结果文件对账)
  06 回归种子: 8-12 条种子在当前结果文件中 gold 命中且最佳名次 <= max_rank

运行: python -m unittest tests.test_retrieval_comparison -v
边界: 检索层零生成零业务效果; 保留集不解封; latency 只记录不断言。
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest

from evaluation import eval_contract as ec
from retrieval import contract as rc
from retrieval.labels import load_public_labels
from retrieval.run_retrieval_comparison import main as run_main

PROJECT_ROOT = ec.PROJECT_ROOT
WEEK11_DIR = os.path.join(PROJECT_ROOT, "week-11")
RESULT_FILES = {
    "keyword": os.path.join(WEEK11_DIR, "keyword-results-v1.jsonl"),
    "vector": os.path.join(WEEK11_DIR, "vector-results-v1.jsonl"),
}
MANIFEST_PATH = os.path.join(WEEK11_DIR, "retrieval-comparison-manifest-v1.json")
REGISTER_PATH = os.path.join(WEEK11_DIR, "retrieval-failure-register-v1.md")
SEEDS_PATH = os.path.join(WEEK11_DIR, "retrieval-regression-seeds-v1.jsonl")
CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
GUIDE_PATH = os.path.join(PROJECT_ROOT, "labeling-guide-v1.md")
VALID_LAYERS = {"corpus", "acl", "retrieval", "ranking_context", "label"}
RECORD_FIELDS = ("case_id", "dataset_version", "split", "user_role", "family",
                 "corpus_version", "retriever", "retriever_version", "top_k",
                 "pool_size", "query", "gold_document_ids", "ranked_document_ids",
                 "ranked_chunks", "gold_found", "missing_gold",
                 "restricted_candidate_count", "latency_ms", "cost_estimate_usd",
                 "synthetic")


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_jsonl(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _projection(rows: list) -> list:
    """确定性规范投影: 排除 latency_ms (遥测) 后 sort_keys 序列化。"""
    return [json.dumps({k: v for k, v in row.items() if k != "latency_ms"},
                       sort_keys=True, ensure_ascii=False) for row in rows]


class TestRetrievalComparison(unittest.TestCase):
    """第 11 周检索比较合同测试 (对照 week-11 冻结工件)。"""

    @classmethod
    def setUpClass(cls):
        cls.records = {name: _load_jsonl(path) for name, path in RESULT_FILES.items()}
        cls.labels = load_public_labels()
        cls.conn = sqlite3.connect(CORPUS_DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _allowed_chunks(self, role: str) -> set:
        return {row["chunk_id"] for row in rc.candidates_for(self.conn, role,
                                                             ec.CORPUS_VERSION)}

    # -- 01 比较清单冻结 -----------------------------------------------------
    def test_01_manifest_binding_frozen(self):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["corpus"]["snapshot"], ec.CORPUS_VERSION)
        self.assertEqual(manifest["dataset"]["version"], ec.DATASET_VERSION)
        self.assertTrue(manifest["dataset"]["holdout"].startswith("not_executed"),
                        "manifest 必须声明保留集未运行")
        self.assertEqual(manifest["dataset"]["eval_set_sha256"],
                         _sha256(ec.EVAL_SET_PATH), "考卷 sha256 绑定漂移")
        self.assertEqual(manifest["labels"]["sha256"], _sha256(GUIDE_PATH),
                         "公开标签源 sha256 绑定漂移")
        self.assertEqual(manifest["corpus"]["manifest_sha256"],
                         _sha256(ec.CORPUS_MANIFEST_PATH), "语料清单 sha256 绑定漂移")
        self.assertEqual(manifest["protocol"]["top_k"], rc.TOP_K)
        self.assertEqual(manifest["protocol"]["top_k"], 5)
        for retriever in ("keyword", "vector"):
            self.assertEqual(manifest["retrievers"][retriever]["id"],
                             rc.RETRIEVER_IDS[retriever])
        self.assertIn("honesty_note", manifest["retrievers"]["vector"])
        self.assertIn("impl", manifest["retrievers"]["keyword"])

    # -- 02 统一合同与 ACL 零泄漏 ---------------------------------------------
    def test_02_unified_contract_and_acl_zero_leak(self):
        allowed = {role: self._allowed_chunks(role)
                   for role in ("support_agent", "supervisor")}
        self.assertTrue(allowed["support_agent"], "语料库为空请先构建")
        for retriever, rows in self.records.items():
            self.assertEqual(len(rows), 48, f"{retriever} 必须逐例覆盖 48 条")
            for row in rows:
                for field in RECORD_FIELDS:
                    self.assertIn(field, row, f"{row.get('case_id')} 缺字段 {field}")
                self.assertEqual(row["retriever"], retriever)
                self.assertEqual(row["retriever_version"], rc.RETRIEVER_IDS[retriever])
                self.assertEqual(row["top_k"], rc.TOP_K)
                self.assertEqual(row["pool_size"], len(allowed[row["user_role"]]),
                                 f"{row['case_id']} 候选池必须等于 ACL 允许池")
                self.assertTrue(row["synthetic"])
                self.assertEqual(row["cost_estimate_usd"], 0.0)
                self.assertIsInstance(row["latency_ms"], (int, float))
                self.assertGreaterEqual(row["latency_ms"], 0.0)
                # 名次连续, 且逐 chunk 回到 ACL 允许池 (独立于实现复算)
                self.assertEqual([c["rank"] for c in row["ranked_chunks"]],
                                 list(range(1, rc.TOP_K + 1)))
                for chunk in row["ranked_chunks"]:
                    self.assertIn(chunk["chunk_id"], allowed[row["user_role"]],
                                  f"{row['case_id']} 排名块不在允许池: {chunk['chunk_id']}")
                    self.assertIn(chunk["effective_status"],
                                  ("accepted", "accepted_restricted"))
                # 受限零泄漏: agent 行独立复算, 不轻信实现输出
                if row["user_role"] == "support_agent":
                    leaks = [c for c in row["ranked_chunks"]
                             if c["classification"] != "internal-general"]
                    self.assertEqual(leaks, [], f"{row['case_id']} agent 出现受限块")
                    self.assertEqual(row["restricted_candidate_count"], 0)
                # 文档级去重排名与 gold 对照 (与公开标签逐字段一致)
                docs = row["ranked_document_ids"]
                self.assertEqual(len(docs), len(set(docs)), "文档级排名必须去重")
                label = self.labels[row["case_id"]]
                self.assertEqual(row["gold_document_ids"], label["gold_document_ids"])
                self.assertEqual(row["gold_found"],
                                 [g for g in row["gold_document_ids"] if g in docs])
                self.assertEqual(row["missing_gold"],
                                 [g for g in row["gold_document_ids"] if g not in docs])

    # -- 03 确定性重跑 ---------------------------------------------------------
    def test_03_deterministic_rerun_projection(self):
        with tempfile.TemporaryDirectory(prefix="fde-w11-rerun-") as out_dir_a, \
                tempfile.TemporaryDirectory(prefix="fde-w11-rerun-") as out_dir_b:
            run_main(["--out-dir", out_dir_a])
            run_main(["--out-dir", out_dir_b])
            for retriever in RESULT_FILES:
                first = _projection(_load_jsonl(
                    os.path.join(out_dir_a, f"{retriever}-results-v1.jsonl")))
                second = _projection(_load_jsonl(
                    os.path.join(out_dir_b, f"{retriever}-results-v1.jsonl")))
                self.assertEqual(first, second,
                                 f"{retriever} 两次运行规范投影必须逐行一致")
                self.assertEqual(first, _projection(self.records[retriever]),
                                 f"{retriever} 与已落盘结果的投影不一致")

    # -- 04 保留集未运行 ---------------------------------------------------------
    def test_04_holdout_not_executed(self):
        holdout_ids = {row["case_id"] for row in ec.load_eval_set()
                       if row["split"] == "holdout"}
        self.assertEqual(len(holdout_ids), 12)
        dev_cal_ids = {row["case_id"] for row in ec.load_eval_set()
                       if row["split"] != "holdout"}
        self.assertEqual(len(dev_cal_ids), 48)
        for retriever, rows in self.records.items():
            executed = {row["case_id"] for row in rows}
            self.assertEqual(holdout_ids & executed, set(),
                             f"{retriever} 不得出现保留集 case")
            self.assertEqual(executed, dev_cal_ids,
                             f"{retriever} 必须恰好执行 dev+cal 48 条")

    # -- 05 失败登记交叉核验 -------------------------------------------------------
    def test_05_failure_register_crosscheck(self):
        with open(REGISTER_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        entries = []
        for line in text.splitlines():
            if line.startswith("| NS-"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                self.assertEqual(len(cells), 8, f"登记行必须 8 列: {cells[0]}")
                entries.append(cells)
        self.assertGreaterEqual(len(entries), 5, "失败登记必须 >= 5 条")
        by_key = {(row["retriever"], row["case_id"]): row
                  for rows in self.records.values() for row in rows}
        for cells in entries:
            case_id, retriever, layer = cells[0], cells[1], cells[4]
            self.assertIn(retriever, ("keyword", "vector"),
                          f"{case_id} 检索器枚举非法")
            self.assertIn((retriever, case_id), by_key,
                          f"登记条目 {case_id}/{retriever} 不在结果文件中")
            self.assertIn(layer, VALID_LAYERS, f"{case_id} 失败层枚举非法: {layer}")
            for col in (2, 3, 5, 6, 7):
                self.assertTrue(cells[col], f"{case_id} 登记字段第 {col} 列不得为空")
            if layer == "retrieval":
                row = by_key[(retriever, case_id)]
                # 检索层失败 = gold 缺失, 或 (无 gold 例) 无可分离的置信信号
                self.assertTrue(row["missing_gold"] or not row["gold_document_ids"],
                                f"{case_id} 标记 retrieval 层失败但 gold 并未缺失")
            if layer == "acl":
                row = by_key[(retriever, case_id)]
                self.assertEqual(row["restricted_candidate_count"], 0)

    # -- 06 回归种子 --------------------------------------------------------------
    def test_06_regression_seeds_hold(self):
        seeds = _load_jsonl(SEEDS_PATH)
        self.assertTrue(8 <= len(seeds) <= 12, "回归种子必须 8-12 条")
        seen = set()
        for seed in seeds:
            for field in ("seed_id", "case_id", "retriever",
                          "required_gold_document_ids", "max_rank"):
                self.assertIn(field, seed, f"种子缺字段 {field}")
            key = (seed["retriever"], seed["case_id"])
            self.assertNotIn(key, seen, "种子 (retriever, case) 不得重复")
            seen.add(key)
            self.assertTrue(1 <= seed["max_rank"] <= rc.TOP_K,
                            f"{seed['seed_id']} max_rank 越界")
            rows = [row for row in self.records[seed["retriever"]]
                    if row["case_id"] == seed["case_id"]]
            self.assertEqual(len(rows), 1,
                             f"种子 case 不在结果文件中: {seed['case_id']}")
            row = rows[0]
            for gold in seed["required_gold_document_ids"]:
                self.assertIn(gold, row["ranked_document_ids"],
                              f"{seed['seed_id']}: gold 跌出 top-5: {gold}")
                ranks = [c["rank"] for c in row["ranked_chunks"]
                         if c["document_id"] == gold]
                self.assertLessEqual(min(ranks), seed["max_rank"],
                                     f"{seed['seed_id']}: {gold} 名次回退")


if __name__ == "__main__":
    unittest.main()



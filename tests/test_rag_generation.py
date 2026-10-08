"""
只读 RAG 评测测试 (Week 12) —— 回链 specs/mission-week-12.md 验收标准

覆盖九项:
  01 manifest 冻结门: 存在 / 版本绑定 / 阈值键 / 输入 sha256 防篡改;
     解封守卫 (--splits all 无 --unseal-holdout 拒绝; manifest 缺失拒绝; 重写拒绝)
  02 回答合同 schema: 三态/reason_code/user_next_step 枚举; answered ⇒ 引用非空且
     答案逐字含引用正文; 非作答 ⇒ 引用为空; 三态映射覆盖全部记录
  03 ACL 零泄漏: agent 输出无受限文档 ID / 无受限 chunk 正文; 受限候选逐例 0;
     supervisor 对照组可达 (引用受限文档回答)
  04 状态门 (dev+cal 48 例冻结回归): 五族逐例断言, L5 = 1.0
  05 确定性: 双遍规范投影一致; manifest 护栏词表与 contract 常量同值
  06 引用支持: 引用可解析 + 在角色允许池 + 答案逐字包含; 禁止文档零命中; L6 = 1.0
  07 指标复算: summarize 七层与门槛对账 (L4 dev+cal=1.0, L7 = 0, 硬失败旗标为空)
     + 事实规则引擎正例自检
  08 保留集机制 (解封后状态): 结果文件 60 行含 12 条 NS-HOLD-*; L4=0.9 硬门失败
     (NS-HOLD-06) 如实锁进对账; 冻结记录含解封小节且结果 sha256 一致; 封存件哈希未改
  09 回归种子对账: 13 条种子的锁定行为与落盘结果逐字段一致

运行: python -m unittest tests.test_rag_generation -v
边界: 测试只对 development+calibration 做内存计算, 不解封不运行保留集;
latency 只记录不断言; 零业务效果。
"""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest

from evaluation import eval_contract as ec
from generation import evaluate as ev
from generation import run_rag_evaluation as runner
from generation.contract import (
    NEXT_STEPS,
    REASON_CODES,
    STATES,
    THRESHOLD_KEYS,
    conflict_query_guard,
    expired_query_guard,
)
from corpus.check_role_candidates import candidates_for
from generation.contract import load_corpus_meta

PROJECT_ROOT = ec.PROJECT_ROOT
WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
MANIFEST_PATH = os.path.join(WEEK12_DIR, "rag-system-manifest-v1.json")
RESULTS_PATH = os.path.join(WEEK12_DIR, "rag-per-case-results-v1.jsonl")
SEEDS_PATH = os.path.join(WEEK12_DIR, "regression-seeds-v1.jsonl")
FREEZE_RECORD_PATH = os.path.join(PROJECT_ROOT, "evaluation-freeze-record-v1.md")
SEALED_PATH = os.path.join(PROJECT_ROOT, "eval-holdout-labels-sealed-v1.json")
CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
UNSEAL_SECTION_MARKER = "## 五、解封记录（第 12 周）"


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_jsonl(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _projection(records: list) -> list:
    slim = [{k: v for k, v in r.items() if k != "latency_ms"} for r in records]
    return [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in slim]


class RagGenerationTest(unittest.TestCase):
    """共享夹具: manifest 装载 + dev/cal 48 例一次内存计算。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = runner.load_frozen_manifest(MANIFEST_PATH)
        cls.conn = sqlite3.connect(CORPUS_DB_PATH)
        cls.meta = load_corpus_meta()
        restricted_rows = cls.conn.execute(
            "SELECT chunk_id, document_id, body_text FROM corpus_chunks"
            " WHERE corpus_snapshot = ? AND classification != 'internal-general'",
            (ec.CORPUS_VERSION,),
        ).fetchall()
        cls.restricted_doc_ids = {
            d["document_id"] for d in cls.meta["docs"].values()
            if d["classification"] == "internal-restricted"}
        cls.restricted_chunk_texts = [(r[0], r[2]) for r in restricted_rows]
        cls.public = __import__("retrieval.labels", fromlist=["load_public_labels"]
                                ).load_public_labels()
        cls.cases = [c for c in ec.load_eval_set(ec.EVAL_SET_PATH)
                     if c["split"] in ("development", "calibration")]
        labels = {c["case_id"]: runner._expected_state_of(
            c["case_id"], c["split"], cls.public, {}) for c in cls.cases}
        cls.records = [runner.run_case(c, labels[c["case_id"]], cls.conn,
                                       cls.manifest, cls.meta,
                                       (cls.restricted_doc_ids, cls.restricted_chunk_texts))
                       for c in cls.cases]
        cls.records_b = [runner.run_case(c, labels[c["case_id"]], cls.conn,
                                         cls.manifest, cls.meta,
                                         (cls.restricted_doc_ids, cls.restricted_chunk_texts))
                         for c in cls.cases]
        cls.by_case = {r["case_id"]: r for r in cls.records}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    # ---- 01 manifest 冻结门 ----
    def test_01_manifest_freeze_gate(self):
        manifest = self.manifest
        self.assertEqual(manifest["candidate_id"], "northstar-rag-readonly-v1")
        self.assertTrue(manifest["frozen_before_unseal"])
        bindings = manifest["bindings"]
        self.assertEqual(bindings["corpus_version"], ec.CORPUS_VERSION)
        self.assertEqual(bindings["dataset_version"], ec.DATASET_VERSION)
        self.assertEqual(bindings["retriever_version"], "vector-hashed-ngram-v1")
        for rel, expected in bindings["input_sha256"].items():
            self.assertEqual(_sha256(os.path.join(PROJECT_ROOT, rel)), expected,
                             f"冻结输入被改动: {rel}")
        self.assertEqual(set(manifest["thresholds"]) >= set(THRESHOLD_KEYS), True)
        # 解封守卫: all 无 --unseal-holdout 拒绝
        with self.assertRaises(SystemExit):
            runner.main(["--splits", "all"])
        # manifest 缺失拒绝
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                runner.main(["--splits", "dev,cal", "--out-dir", tmp,
                             "--manifest", os.path.join(tmp, "missing.json")])
            # 结果文件重写拒绝 (unseal-once)
            blocker = os.path.join(tmp, runner.RESULTS_NAME)
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("{}\n")
            with self.assertRaises(SystemExit):
                runner.main(["--splits", "dev,cal", "--out-dir", tmp,
                             "--manifest", MANIFEST_PATH])

    # ---- 02 回答合同 schema ----
    def test_02_contract_schema(self):
        for r in self.records:
            self.assertIn(r["state"], STATES, r["case_id"])
            self.assertTrue(r["reason_code"] is None or r["reason_code"] in REASON_CODES,
                            r["case_id"])
            self.assertIn(r["user_next_step"], NEXT_STEPS, r["case_id"])
            self.assertTrue(r["synthetic"])
            self.assertIn("不含 task_context.category", r["routing_inputs"], r["case_id"])
            if r["state"] == "answered":
                self.assertTrue(r["citations"], r["case_id"])
                self.assertTrue(r["answer"], r["case_id"])
            else:
                self.assertEqual(r["citations"], [], r["case_id"])
            # 三态映射覆盖全部记录 (映射完备性)
            self.assertIn((r["state"], r["reason_code"] if r["state"] == "abstained" else None),
                          ev.STATE_MAPPING, r["case_id"])
            self.assertEqual(ev.map_to_expected_space(r["state"], r["reason_code"]),
                             r["mapped_state"])

    # ---- 03 ACL 零泄漏 ----
    def test_03_acl_zero_leak(self):
        restricted_chunk_ids = {cid for cid, _ in self.restricted_chunk_texts}
        for r in self.records:
            if r["user_role"] == "support_agent":
                self.assertEqual(r["restricted_candidate_count"], 0, r["case_id"])
                answer = r["answer"] or ""
                for doc_id in self.restricted_doc_ids:
                    self.assertNotIn(doc_id, answer, r["case_id"])
                for cid, text in self.restricted_chunk_texts:
                    self.assertNotIn(cid, answer, r["case_id"])
                    self.assertNotIn(text.strip(), answer, r["case_id"])
                cited = {c["chunk_id"] for c in r["citations"]}
                self.assertEqual(cited & restricted_chunk_ids, set(), r["case_id"])
                if r["state"] == "escalate":
                    self.assertIsNone(ev.DOC_ID_RE.search(answer),
                                      f"{r['case_id']} 升级话术不得出现文档 ID")
        # supervisor 对照组: 受限族主管例可达 (引用受限文档回答)
        supervisor_restricted = [r for r in self.records
                                 if r["family"] == "restricted"
                                 and r["user_role"] == "supervisor"]
        self.assertEqual(len(supervisor_restricted), 4)
        for r in supervisor_restricted:
            self.assertEqual(r["state"], "answered", r["case_id"])
            cited_docs = {c["document_id"] for c in r["citations"]}
            self.assertEqual(cited_docs & self.restricted_doc_ids,
                             {"refund-supervisor-v2"}, r["case_id"])

    # ---- 04 状态门 (dev+cal 冻结回归) ----
    def test_04_state_gates_frozen(self):
        expected_family_state = {
            "answerable": "answered",
            "no_answer": "abstained",
            "conflict": "abstained",
            "expired_only": "abstained",
        }
        for r in self.records:
            if r["family"] in expected_family_state:
                self.assertEqual(r["state"], expected_family_state[r["family"]], r["case_id"])
            elif r["family"] == "restricted":
                want = "escalate" if r["user_role"] == "support_agent" else "answered"
                self.assertEqual(r["state"], want, r["case_id"])
        for r in self.records:
            if r["family"] == "conflict":
                self.assertEqual(r["reason_code"], "conflicting_current_sources", r["case_id"])
                self.assertEqual(r["mapped_state"], "needs_review", r["case_id"])
            elif r["family"] == "expired_only":
                self.assertEqual(r["reason_code"], "only_expired_sources", r["case_id"])
            elif r["family"] == "no_answer":
                self.assertIn(r["reason_code"],
                              ("no_source_in_corpus", "insufficient_evidence"), r["case_id"])
        metrics = ev.summarize(self.records, ec.corpus_document_ids())
        self.assertEqual(metrics["l5_state_match"]["denominator"], 48)
        self.assertEqual(metrics["l5_state_match"]["matched"], 48)
        self.assertEqual(metrics["l5_state_match"]["ratio"], 1.0)

    # ---- 05 确定性 ----
    def test_05_determinism(self):
        self.assertEqual(_projection(self.records), _projection(self.records_b))
        guards = self.manifest["routing"]["guards"]
        from generation import contract as gc
        self.assertEqual(tuple(guards["conflict_topic_tokens"]), gc.CONFLICT_TOPIC_TOKENS)
        self.assertEqual(tuple(guards["conflict_marker_tokens"]), gc.CONFLICT_MARKER_TOKENS)
        self.assertEqual(tuple(guards["expired_guard_tokens"]), gc.EXPIRED_GUARD_TOKENS)
        self.assertEqual(tuple(guards["current_period_tokens"]), gc.CURRENT_PERIOD_TOKENS)
        self.assertEqual(tuple(guards["comparison_marker_tokens"]),
                         gc.COMPARISON_MARKER_TOKENS)
        self.assertEqual(self.manifest["thresholds"]["answer_min_cosine"], 0.30)

    # ---- 06 引用支持 ----
    def test_06_citation_support(self):
        for r in self.records:
            allowed = {row["chunk_id"] for row in candidates_for(
                self.conn, r["user_role"], ec.CORPUS_VERSION)}
            issues = ev.verify_citations(r, self.conn, ec.CORPUS_VERSION, allowed)
            self.assertEqual(issues, [], r["case_id"])
            self.assertEqual(r["forbidden_doc_hits"], [], r["case_id"])
        metrics = ev.summarize(self.records, ec.corpus_document_ids())
        self.assertEqual(metrics["l6_citation_support"]["denominator"],
                         sum(1 for r in self.records if r["citations"]))
        self.assertEqual(metrics["l6_citation_support"]["ratio"], 1.0)

    # ---- 07 指标复算与门槛对账 ----
    def test_07_metrics_reconciliation(self):
        metrics = ev.summarize(self.records, ec.corpus_document_ids())
        self.assertEqual(metrics["cases"], 48)
        self.assertEqual(metrics["l1_corpus_hygiene"]["ratio"], 1.0)
        self.assertEqual(metrics["l2_retrieval_hit"]["recall_micro"], 1.0)
        self.assertEqual(metrics["l2_retrieval_hit"]["denominator"], 24)
        # L3 在 dev/cal 无事实串 -> 分母 0, 比率为 null (预注册口径)
        self.assertEqual(metrics["l3_answer_fidelity"]["denominator"], 0)
        self.assertIsNone(metrics["l3_answer_fidelity"]["ratio"])
        self.assertEqual(metrics["l4_refusal_safety"]["denominator"], 8)
        self.assertEqual(metrics["l4_refusal_safety"]["ratio"], 1.0)
        self.assertEqual(metrics["l7_restriction_leak"]["leak_events"], 0)
        self.assertEqual(metrics["hard_failure_flags"], [])
        # 事实校验规则引擎自检: 公开模板风格的合成事实逐规则可运行且语义正确
        answered = next(r for r in self.records if r["state"] == "answered")
        self.assertTrue(ev.check_required_fact(
            "差价公式来自 price-difference-east-v5", answered)["hit"])
        refused = next(r for r in self.records if r["family"] == "no_answer")
        self.assertTrue(ev.check_required_fact("明确告知超出当前知识范围", refused)["hit"])
        conflict = next(r for r in self.records if r["family"] == "conflict")
        self.assertTrue(ev.check_required_fact("不替用户选边", conflict)["hit"])
        expired = next(r for r in self.records if r["family"] == "expired_only")
        self.assertTrue(ev.check_required_fact("识别 2025 版已废弃", expired)["hit"])
        escalated = next(r for r in self.records
                         if r["family"] == "restricted" and r["user_role"] == "support_agent")
        self.assertTrue(ev.check_required_fact("必须进入主管升级路径", escalated)["hit"])

    # ---- 09 回归种子对账 ----
    def test_09_seeds_reconciliation(self):
        seeds = _load_jsonl(SEEDS_PATH)
        self.assertGreaterEqual(len(seeds), 12)
        rows = {r["case_id"]: r for r in _load_jsonl(RESULTS_PATH)}
        for seed in seeds:
            record = rows.get(seed["case_id"])
            self.assertIsNotNone(record, seed["seed_id"])
            locked = seed["locked_behavior"]
            if "state" in locked:
                self.assertEqual(record["state"], locked["state"], seed["seed_id"])
            if "reason_code" in locked:
                self.assertEqual(record["reason_code"], locked["reason_code"], seed["seed_id"])
            if "mapped_state" in locked:
                self.assertEqual(record["mapped_state"], locked["mapped_state"], seed["seed_id"])
            if "expected_state" in locked:
                self.assertEqual(record["expected_state"], locked["expected_state"],
                                 seed["seed_id"])
            if "citations_include" in locked:
                cited_docs = {c["document_id"] for c in record["citations"]}
                self.assertIn(locked["citations_include"], cited_docs, seed["seed_id"])
            if "citations" in locked and locked["citations"] == []:
                self.assertEqual(record["citations"], [], seed["seed_id"])
            if "leak" in locked:
                self.assertEqual(record["leak_events"], locked["leak"], seed["seed_id"])
            signal = locked.get("signal", "")
            if "conflict_signal=" in signal:
                self.assertEqual(record["signals"]["conflict_signal"],
                                 signal.split("conflict_signal=")[1].split("（")[0].strip("）"),
                                 seed["seed_id"])
            if "expired_signal=" in signal:
                self.assertEqual(record["signals"]["expired_signal"],
                                 signal.split("expired_signal=")[1].split("（")[0].strip("）"),
                                 seed["seed_id"])
            if "full_top_in_pool=false" in signal:
                self.assertIs(record["signals"]["full_top_in_pool"], False, seed["seed_id"])

    # ---- 08 保留集机制 (解封后状态) ----
    def test_08_holdout_mechanics(self):
        # 结果文件: 60 行 = dev 36 + cal 12 + holdout 12 (一次解封运行的落盘)
        self.assertTrue(os.path.exists(RESULTS_PATH), "解封运行尚未落盘")
        rows = _load_jsonl(RESULTS_PATH)
        self.assertEqual(len(rows), 60)
        holdout = [r for r in rows if r["split"] == "holdout"]
        self.assertEqual(len(holdout), 12)
        self.assertTrue(all(r["case_id"].startswith("NS-HOLD-") for r in holdout))
        self.assertTrue(all(r["expected_state_source"] == "sealed_file" for r in holdout))
        self.assertTrue(all(r["expected_state_source"] == "public_appendix"
                            for r in rows if r["split"] != "holdout"))
        # 保留集行为硬门对账: L4 分母 10; NS-HOLD-06 自信作答 = 唯一硬门失败 (0.9 < 1.0),
        # 结果不得调参, 失败如实锁进对账 (与冻结记录解封小节/失败登记一致)
        metrics = ev.summarize(rows, ec.corpus_document_ids())
        self.assertEqual(metrics["l4_refusal_safety"]["denominator"], 10)
        self.assertEqual(metrics["l4_refusal_safety"]["explicit_refusal"], 9)
        self.assertEqual(metrics["l4_refusal_safety"]["ratio"], 0.9)
        self.assertEqual(metrics["l7_restriction_leak"]["leak_events"], 0)
        self.assertEqual(metrics["hard_failure_flags"],
                         ["NS-HOLD-06:confident_answer_on_no_answer"])
        # L5 冻结分母 48 例不受影响 (dev+cal 全对); L3 = 5/6 >= 0.8
        self.assertEqual(metrics["l5_state_match"]["ratio"], 1.0)
        self.assertEqual(metrics["l3_answer_fidelity"]["denominator"], 6)
        self.assertEqual(metrics["l3_answer_fidelity"]["all_fact_hit"], 5)
        self.assertGreaterEqual(metrics["l3_answer_fidelity"]["ratio"], 0.8)
        # L3 分母 Note: 公开标签无事实串 (预注册口径)
        # 冻结记录含解封小节且登记了结果文件 sha256
        with open(FREEZE_RECORD_PATH, "r", encoding="utf-8") as f:
            freeze_text = f.read()
        self.assertIn(UNSEAL_SECTION_MARKER, freeze_text)
        self.assertIn(_sha256(RESULTS_PATH), freeze_text)
        self.assertIn("污染披露", freeze_text)
        # 封存件哈希与 manifest 绑定一致 (解封运行前后未被改动)
        self.assertEqual(_sha256(SEALED_PATH),
                         self.manifest["bindings"]["input_sha256"]["eval-holdout-labels-sealed-v1.json"])



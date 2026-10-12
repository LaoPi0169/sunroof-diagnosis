"""
语料治理测试 (Week 09, 回链 specs/mission-week-09.md 验收标准)

覆盖六项:
  01 两次构建确定性 (不同临时库, documents/chunks 全字段逐条一致; 同库重建幂等)
  02 缺要件默认隔离 (quarantined + missing_fields 落库; 非 accepted* 文档 chunk 恒为 0)
  03 状态不被时间戳掩盖 (expired 由 supersedes 谱系决定, 与文件落盘时间无关)
  04 冲突对保留在快照 (conflict_review 双标记, chunk 恒为 0, 供第 10-12 周识别冲突)
  05 模型调用前角色候选零泄漏 (pqe 精确候选集 13 含 s06 排除; quality_supervisor 18; unknown 0)
  06 提交的 corpus-manifest-v1.json 与全新重建逐字段绑定 (清单防漂移)

运行: python -m unittest tests.test_corpus_governance -v
说明: 全程零模型调用; 语料库建在临时目录, 测试结束清理, 不触碰 data/ 演示库。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus, build_tables  # noqa: E402
from corpus.check_role_candidates import check_role  # noqa: E402

SNAPSHOT = "sunroof-corpus-w09-v1"

# pqe 精确候选集合 (13 = service-general-v3 除 s06 质量主管例外段外的 7 段
# + torque-east-v5 全部 6 段) —— 固化 role-candidate-check-v1 的实测结果
EXPECTED_PQE_CANDIDATES = (
    [f"service-general-v3#s{seq:02d}" for seq in (0, 1, 2, 3, 4, 5, 7)]
    + [f"torque-east-v5#s{seq:02d}" for seq in range(6)]
)


def _canon_documents(documents: list) -> list:
    """file_path 分隔符归一化 (平台一致性边界): 冻结清单按 Windows 作者机的
    os.sep 生成 (反斜杠), Linux 重建为正斜杠。分隔符属平台产物, 不参与内容
    绑定语义 —— 内容锚 (document_id/title/来源 sha256/状态族) 仍逐字段精确
    比对; 冻结的 corpus-manifest-v1.json 本身零改动。"""
    return [
        {**doc, "file_path": doc["file_path"].replace("\\", "/")}
        for doc in documents
    ]


class CorpusGovernanceTests(unittest.TestCase):
    """语料快照的确定性 / 默认隔离 / 权限下推三道治理硬门。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="sunroof-ut-corpus-")
        cls.db_a = os.path.join(cls._tmp.name, "a.db")
        cls.db_b = os.path.join(cls._tmp.name, "b.db")
        cls.doc_a, cls.chunk_a = build_tables(cls.db_a)
        cls.doc_b, cls.chunk_b = build_tables(cls.db_b)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _doc(self, document_id):
        return next(d for d in self.doc_a if d["document_id"] == document_id)

    def test_01_two_build_determinism(self):
        """两次独立构建 (不同临时库) 全字段逐条一致; 同库重建幂等。"""
        self.assertEqual(self.doc_a, self.doc_b)
        self.assertEqual(self.chunk_a, self.chunk_b)
        doc_again, chunk_again = build_tables(self.db_a)
        self.assertEqual(doc_again, self.doc_a)
        self.assertEqual(chunk_again, self.chunk_a)

    def test_02_missing_fields_default_quarantine(self):
        """缺所有者/ACL/版本/生效期任一要件 -> 默认隔离, 不按'公开且有效'处理。"""
        for document_id in ("quick-note", "special-claim-copy"):
            doc = self._doc(document_id)
            self.assertEqual(doc["status"], "quarantined", document_id)
            self.assertEqual(
                set(doc["missing_fields"]),
                {"source_owner", "allowed_roles", "source_version", "effective_at"},
                document_id,
            )
            self.assertEqual(doc["chunk_count"], 0, document_id)
        # 非 accepted* 文档 chunk 恒为 0 (expired / conflict_review / quarantined)
        for doc in self.doc_a:
            if doc["status"] not in ("accepted", "accepted_restricted"):
                self.assertEqual(doc["chunk_count"], 0, doc["document_id"])
                self.assertEqual(doc["chunk_ids"], [], doc["document_id"])
        self.assertEqual(sum(d["chunk_count"] for d in self.doc_a), 18)
        self.assertEqual(len(self.chunk_a), 18)

    def test_03_expiry_via_supersedes_not_mtime(self):
        """过期由谱系 (supersedes) 决定, 与文件落盘时间戳无关; 旧版保留但不可候选。"""
        v1 = self._doc("service-general-v1")
        self.assertEqual(v1["status"], "expired")
        v3 = self._doc("service-general-v3")
        self.assertEqual(v3["status"], "accepted")
        self.assertIn("service-general", v3["supersedes"])
        conn = sqlite3.connect(self.db_a)
        try:
            row = conn.execute(
                "SELECT content_sha256, chunk_count FROM corpus_documents "
                "WHERE document_id = 'service-general-v1'"
            ).fetchone()
            self.assertTrue(row, "过期文档必须保留在快照中 (状态治理, 非物理删除)")
            self.assertEqual(row[1], 0)
        finally:
            conn.close()

    def test_04_conflict_pair_preserved(self):
        """冲突对双标记 conflict_review, 保留在快照但 chunk 恒为 0。"""
        statuses = {d["document_id"]: d["status"] for d in self.doc_a}
        self.assertEqual(statuses["retrofit-bulletin-v2"], "conflict_review")
        self.assertEqual(statuses["retrofit-notice-v4"], "conflict_review")
        groups = {d["document_id"]: d["conflict_group"] for d in self.doc_a}
        self.assertEqual(groups["retrofit-bulletin-v2"], "retrofit-oem-a-2026-04")
        self.assertEqual(groups["retrofit-notice-v4"], groups["retrofit-bulletin-v2"])

    def test_05_role_candidate_zero_leakage(self):
        """模型调用前的原始候选: ACL 下推过滤, 受限内容零泄漏 (含精确集合断言)。"""
        conn = sqlite3.connect(self.db_a)
        try:
            pqe = check_role(conn, "pqe", SNAPSHOT)
            supervisor = check_role(conn, "quality_supervisor", SNAPSHOT)
            unknown = check_role(conn, "unknown_role", SNAPSHOT)
        finally:
            conn.close()
        self.assertEqual(pqe["verdict"], "PASS", pqe["leaks"])
        self.assertEqual(pqe["candidate_count"], 13)
        self.assertEqual(pqe["chunk_ids"], EXPECTED_PQE_CANDIDATES,
                         "候选集合必须与 ACL 矩阵精确一致 (含 service-general-v3#s06 排除)")
        self.assertNotIn("service-general-v3#s06", pqe["chunk_ids"])
        self.assertEqual(set(pqe["by_classification"]), {"internal-general"})
        self.assertEqual(supervisor["verdict"], "PASS", supervisor["leaks"])
        self.assertEqual(supervisor["candidate_count"], 18)
        self.assertEqual(supervisor["by_classification"],
                         {"internal-general": 13, "internal-restricted": 5})
        self.assertEqual(unknown["candidate_count"], 0, "未知角色必须零候选 (401 语义一致)")

    def test_06_manifest_binding(self):
        """提交的 corpus-manifest-v1.json 必须与全新重建逐字段一致 (防文档漂移)。"""
        manifest_path = os.path.join(PROJECT_ROOT, "corpus-manifest-v1.json")
        self.assertTrue(os.path.exists(manifest_path), "语料清单交付物缺失")
        with open(manifest_path, "r", encoding="utf-8") as f:
            committed = json.load(f)
        fresh = build_corpus(
            os.path.join(self._tmp.name, "fresh.db"),
            os.path.join(self._tmp.name, "fresh.json"),
        )
        self.assertEqual(committed["corpus_snapshot"], SNAPSHOT)
        self.assertEqual(committed["batch_id"], "corpus-batch-w09-v1")
        self.assertEqual(
            _canon_documents(committed["documents"]),
            _canon_documents(fresh["documents"]),
            "提交清单与全新重建漂移 (documents; file_path 仅分隔符口径差异)",
        )
        self.assertEqual(committed["chunks"], fresh["chunks"],
                         "提交清单与全新重建漂移 (chunks)")
        self.assertEqual(committed["counts"]["documents_total"], 8)
        self.assertEqual(committed["counts"]["chunks_total"], 18)
        self.assertEqual(
            committed["counts"]["by_status"],
            {"accepted": 2, "accepted_restricted": 1, "expired": 1,
             "conflict_review": 2, "quarantined": 2},
        )
        self.assertEqual(committed["counts"]["chunks_by_classification"],
                         {"internal-general": 13, "internal-restricted": 5})


if __name__ == "__main__":
    unittest.main()
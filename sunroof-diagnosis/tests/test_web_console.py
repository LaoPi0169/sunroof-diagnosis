"""
阶段 C 节点 ⑮ 交互控制台 HTTP 面 —— 自动化集成测试

回链 deploy/web-console-usage.md 与 application/main.py 节点 ⑮ 追加端点:
  01 /ui 控制台页面可达 + 根路径重定向 + 页面零令牌字面量
  02 /api/v1/session/whoami 四档身份矩阵 + 未知令牌 401
  03 /api/v1/rag/answer 可答例: answered + 唯一引用 + 零业务效果 (冻结评测集问句)
  04 /api/v1/rag/answer 门控语义: 冲突域 -> needs_review/conflict_gate; 无答案域 -> refused
  05 /api/v1/rag/answer 封闭契约: 缺字段/自报 role (extra=forbid) 422
  06 /api/v1/rag/corpus-meta 快照 ID 与两表行数
  07 控制台数据源端到端: 任务队列 ACL 过滤 + check_spec answered + 越权 denied

运行: python -m unittest tests.test_web_console -v
"""
import os
import sys
import tempfile
import unittest

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.main import app  # noqa: E402
from application.rag import get_corpus_conn  # noqa: E402
from evaluation.eval_contract import EVAL_SET_PATH, load_eval_set  # noqa: E402

# 页面与响应零令牌字面量 (与 test_deployment_release.SECRET_MARKERS 同口径;
# 注意: 请求头字段名 X-Auth-Token 是公开契约, 不属于秘密)
SECRET_TOKEN_VALUES = (
    "token_pqe_oem_a",
    "token_pqe_oem_b",
    "token_qa_supervisor_secret",
)


def _case_query(case_id: str) -> str:
    """从冻结评测集取真实问句 (保证命中 Week-12 冻结门控行为, 不复述文案)。"""
    for case in load_eval_set(EVAL_SET_PATH):
        if case["case_id"] == case_id:
            return case["query"]
    raise AssertionError(f"评测集缺少 {case_id}")


class WebConsoleTests(unittest.TestCase):
    """交互控制台 + 只读 RAG HTTP 面: 每用例独立 FDE_DB_PATH, 结果 100% 确定"""

    @classmethod
    def setUpClass(cls):
        # 语料库懒建自检 (全新 checkout / Linux CI 安全; 不触碰冻结 manifest)
        conn = get_corpus_conn()
        conn.close()
        cls.queries = {
            case_id: _case_query(case_id)
            for case_id in ("SR-DEV-01", "SR-DEV-21", "SR-DEV-23")
        }

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-console-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_console_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每个用例独立初始化，保证确定性
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    # ------------------------------------------------------------------
    # 01 /ui 控制台页面 + 根路径重定向 + 零秘密
    # ------------------------------------------------------------------
    def test_01_ui_page_served_and_no_secrets(self):
        root = self.client.get("/", follow_redirects=False)
        self.assertIn(root.status_code, (301, 302, 307))
        self.assertEqual(root.headers["location"], "/ui")

        page = self.client.get("/ui")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        html = page.text
        self.assertIn("天窗总成只读诊断", html)
        self.assertIn("/api/v1/rag/answer", html)
        self.assertIn("/api/v1/spec-check", html)
        self.assertIn("/api/v1/tasks", html)
        for secret in SECRET_TOKEN_VALUES:
            self.assertNotIn(secret, html, f"控制台页面不得内嵌令牌: {secret}")

    # ------------------------------------------------------------------
    # 02 whoami 四档身份矩阵 + 未知令牌 401
    # ------------------------------------------------------------------
    def test_02_whoami_identity_matrix(self):
        resp = self.client.get("/api/v1/session/whoami")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user_id"], "pqe_trainee_default")
        self.assertEqual(resp.json()["user_role"], "pqe")
        self.assertEqual(resp.json()["allowed_oem_projects"], ["OEM-A"])

        matrix = {
            "token_pqe_oem_a": ("pqe_chen_oema", "pqe", ["OEM-A"]),
            "token_pqe_oem_b": ("pqe_zhao_oemb", "pqe", ["OEM-B"]),
            "token_qa_supervisor_secret": ("supervisor_li", "quality_supervisor", ["*"]),
        }
        for token, (user_id, role, acl) in matrix.items():
            resp = self.client.get(
                "/api/v1/session/whoami", headers={"X-Auth-Token": token}
            )
            self.assertEqual(resp.status_code, 200, token)
            self.assertEqual(resp.json()["user_id"], user_id)
            self.assertEqual(resp.json()["user_role"], role)
            self.assertEqual(resp.json()["allowed_oem_projects"], acl)

        resp = self.client.get(
            "/api/v1/session/whoami", headers={"X-Auth-Token": "token_invalid_x"}
        )
        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 03 RAG 可答例: answered + 唯一引用 + 零业务效果
    # ------------------------------------------------------------------
    def test_03_rag_answered_with_citation(self):
        resp = self.client.post(
            "/api/v1/rag/answer", json={"query": self.queries["SR-DEV-01"]}
        )
        self.assertEqual(resp.status_code, 200)
        record = resp.json()
        self.assertEqual(record["state"], "answered")
        self.assertEqual(record["expected_task_state"], "answered")
        self.assertEqual(record["user_role"], "pqe")
        self.assertEqual(len(record["citations"]), 1)
        self.assertEqual(record["citations"][0]["document_id"], "torque-east-v5")
        self.assertTrue(record["answer_text"])
        self.assertEqual(record["business_effects"], 0)
        self.assertEqual(record["restricted_candidate_count"], 0)
        self.assertEqual(record["generator_mode"], "deterministic-teaching-stand-in")
        self.assertIn("request_id", record)

    # ------------------------------------------------------------------
    # 04 RAG 门控语义: 冲突域 -> needs_review; 无答案域 -> refused
    # ------------------------------------------------------------------
    def test_04_rag_gates_conflict_and_no_answer(self):
        resp = self.client.post(
            "/api/v1/rag/answer", json={"query": self.queries["SR-DEV-23"]}
        )
        self.assertEqual(resp.status_code, 200)
        conflict = resp.json()
        self.assertEqual(conflict["state"], "abstained")
        self.assertEqual(conflict["expected_task_state"], "needs_review")
        self.assertEqual(conflict["reason_code"], "conflict_gate")
        self.assertEqual(conflict["citations"], [])

        resp = self.client.post(
            "/api/v1/rag/answer", json={"query": self.queries["SR-DEV-21"]}
        )
        self.assertEqual(resp.status_code, 200)
        no_answer = resp.json()
        self.assertEqual(no_answer["expected_task_state"], "refused")
        self.assertEqual(no_answer["citations"], [])

    # ------------------------------------------------------------------
    # 05 RAG 封闭契约: 缺字段 / 自报 role (extra=forbid) -> 422
    # ------------------------------------------------------------------
    def test_05_rag_request_validation(self):
        resp = self.client.post("/api/v1/rag/answer", json={})
        self.assertEqual(resp.status_code, 422)

        resp = self.client.post(
            "/api/v1/rag/answer",
            json={"query": "任意问句", "role": "quality_supervisor"},
        )
        self.assertEqual(resp.status_code, 422, "自报 role 必须被封闭契约拒绝")

        resp = self.client.post(
            "/api/v1/rag/answer",
            json={"query": "任意问句"},
            headers={"X-Auth-Token": "token_invalid_x"},
        )
        self.assertEqual(resp.status_code, 401, "未知令牌: 查询不执行")

    # ------------------------------------------------------------------
    # 06 corpus-meta 快照与两表行数
    # ------------------------------------------------------------------
    def test_06_corpus_meta(self):
        resp = self.client.get("/api/v1/rag/corpus-meta")
        self.assertEqual(resp.status_code, 200)
        meta = resp.json()
        self.assertEqual(meta["corpus_snapshot"], "sunroof-corpus-w09-v1")
        self.assertGreaterEqual(meta["documents"], 8)
        self.assertGreater(meta["chunks"], 0)

    # ------------------------------------------------------------------
    # 07 控制台数据源端到端: 任务队列 ACL + check_spec + 越权 denied
    # ------------------------------------------------------------------
    def test_07_console_task_flow_e2e(self):
        # 默认 PQE 培训身份 (OEM-A): 可见 4 条 OEM-A 夹具任务
        resp = self.client.get("/api/v1/tasks")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.json()["tasks"]
        self.assertEqual(len(tasks), 4)
        self.assertTrue(all(t["oem_project"] == "OEM-A" for t in tasks))

        # check_spec: TASK-N04-001 (9.6 N·m, +1.05%) -> answered 命中 SPEC-SR-A-V5
        resp = self.client.post(
            "/api/v1/tasks/action",
            json={
                "task_id": "TASK-N04-001",
                "action": "check_spec",
                "correlation_id": "corr-web-console-e2e-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        action = resp.json()
        self.assertEqual(action["status"], "answered")
        self.assertIsNotNone(action["candidate_spec"])

        # 越权: PQE 直调 supervisor_resolve -> denied (审计留痕, 状态不变)
        resp = self.client.post(
            "/api/v1/tasks/action",
            json={
                "task_id": "TASK-N04-001",
                "action": "supervisor_resolve",
                "correlation_id": "corr-web-console-e2e-002",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "denied")
        self.assertEqual(resp.json()["reason_code"], "ACTION_NOT_ALLOWED")

        # 任务队列反映终态, 越权未改状态
        resp = self.client.get("/api/v1/tasks")
        statuses = {t["task_id"]: t["status"] for t in resp.json()["tasks"]}
        self.assertEqual(statuses["TASK-N04-001"], "answered")

        # OEM-B PQE 跨项目直调 -> 队列 ACL 过滤为空
        resp = self.client.get(
            "/api/v1/tasks", headers={"X-Auth-Token": "token_pqe_oem_b"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["tasks"], [])


if __name__ == "__main__":
    unittest.main()


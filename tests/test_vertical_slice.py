"""
Week 05 确定性垂直薄片 —— 自动化集成测试 (5/5)

测试原则 (回链 specs/business-background.md 第 5 周 "第 4 天" 验收标准):
1. 只断言 HTTP 状态码不算测试: 每个用例同时核对「用户可见响应」与「数据库 task_runs 终态」;
2. 三向对账: API 响应、数据库终态与测试使用同一 request_id;
3. 异常路径必须显式失败 (needs_review / 401 / 422)，不允许假成功、不允许默认值填充;
4. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

运行方式 (项目根目录):
    python -m unittest discover -s tests -v
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
from application.services import AMOUNT_APPROVAL_THRESHOLD  # noqa: E402

AGENT_TOKEN = {"X-Auth-Token": "token_agent_valid"}
INVALID_TOKEN = {"X-Auth-Token": "token_not_exist"}


class VerticalSliceIntegrationTests(unittest.TestCase):
    """集成测试链路: API 网关 -> 确定性规则引擎 -> SQLite -> 三方对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fde-week05-test-")
        self.db_path = os.path.join(self._tmpdir.name, "northstar_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每个用例独立初始化，保证确定性
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    def _count_policies(self, where_sql: str, params: tuple = ()) -> int:
        """直接查询 policies 表，用于核对夹具与过滤行为"""
        conn = database.get_db_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM policies WHERE {where_sql}", params
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 用例 1: 成功路径
    # 合法客服身份 + 正常输入 -> answered + 唯一当前有效政策 POL-2026-BIL-01
    # ------------------------------------------------------------------
    def test_01_success_path_returns_answered_and_records_audit(self):
        payload = {
            "request_id": "req-w05-test-001",
            "task_type": "billing_policy_check",
            "region": "华东",
            "purchased_at": "2026-08-15",
            "amount": 500.0,
        }
        resp = self.client.post("/api/v1/policy-check", json=payload, headers=AGENT_TOKEN)

        # --- 用户可见响应 ---
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-w05-test-001")
        self.assertEqual(body["status"], "answered")
        self.assertTrue(body["eligible"])
        self.assertIsNone(body["reason_code"])
        self.assertIsNone(body["escalation_preview"])
        details = body["policy_details"]
        self.assertEqual(details["policy_id"], "POL-2026-BIL-01")
        self.assertEqual(details["version"], "v3")
        self.assertEqual(details["effective_at"], "2026-01-01")
        self.assertEqual(details["source_id"], "KB-BILLING-2026-08")
        self.assertIn("refund", details["formula_rule"])

        # --- 确定性 SQL 层证据: 普通客服恰好命中一条 (supervisor 专属政策被 ACL 隔离) ---
        visible = database.query_policies(
            region="华东", purchased_at="2026-08-15", user_role="support_agent"
        )
        self.assertEqual([p["policy_id"] for p in visible], ["POL-2026-BIL-01"])

        # --- 数据库 task_runs 终态对账 ---
        run = database.get_task_run_by_request_id("req-w05-test-001")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "answered")
        self.assertEqual(run["user_id"], "agent_small_zhang")
        self.assertEqual(run["user_role"], "support_agent")
        self.assertEqual(run["task_type"], "billing_policy_check")
        self.assertIsNone(run["reason_code"])
        self.assertEqual(run["matched_policy_id"], "POL-2026-BIL-01")

    # ------------------------------------------------------------------
    # 用例 2: 金额超限阻断
    # 金额 2500 元 > 2000 元门槛 -> needs_review / AMOUNT_EXCEEDS_LIMIT + 主管复核预览
    # ------------------------------------------------------------------
    def test_02_amount_over_limit_blocks_with_needs_review(self):
        amount = AMOUNT_APPROVAL_THRESHOLD + 500.0  # 2500.0
        payload = {
            "request_id": "req-w05-test-002",
            "task_type": "billing_policy_check",
            "region": "华东",
            "purchased_at": "2026-08-15",
            "amount": amount,
        }
        # 无 X-Auth-Token -> 服务端默认客服测试身份 (身份绝不来自请求体)
        resp = self.client.post("/api/v1/policy-check", json=payload)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-w05-test-002")
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["eligible"])
        self.assertIsNone(body["policy_details"])  # 超限时不返回任何政策候选
        self.assertEqual(body["reason_code"], "AMOUNT_EXCEEDS_LIMIT")
        preview = body["escalation_preview"]
        self.assertEqual(preview["target_role"], "supervisor")
        self.assertEqual(preview["suggested_action"], "创建主管复核单")
        self.assertIn("2000", preview["reason"])

        # --- 数据库终态对账 (身份来自服务端默认会话，而非请求体) ---
        run = database.get_task_run_by_request_id("req-w05-test-002")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "needs_review")
        self.assertEqual(run["reason_code"], "AMOUNT_EXCEEDS_LIMIT")
        self.assertEqual(run["user_id"], "agent_small_zhang")
        self.assertEqual(run["user_role"], "support_agent")
        self.assertIsNone(run["matched_policy_id"])

    # ------------------------------------------------------------------
    # 用例 3: 过期版本拦截
    # 查询 2025 年购买时间 -> 已废弃版本被确定性 SQL 拦截 -> NO_MATCHING_POLICY
    # ------------------------------------------------------------------
    def test_03_expired_version_is_blocked_no_matching_policy(self):
        # 前置证据: 已废弃版本确实存在于库中 (拦截发生在 SQL 过滤，而非数据缺失)
        self.assertEqual(
            self._count_policies("policy_id = 'POL-2025-BIL-01' AND is_deprecated = 1"), 1
        )

        payload = {
            "request_id": "req-w05-test-003",
            "task_type": "billing_policy_check",
            "region": "华东",
            "purchased_at": "2025-06-15",
            "amount": 300.0,
        }
        resp = self.client.post("/api/v1/policy-check", json=payload, headers=AGENT_TOKEN)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-w05-test-003")
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["eligible"])
        self.assertIsNone(body["policy_details"])  # 不允许返回已废弃政策
        self.assertEqual(body["reason_code"], "NO_MATCHING_POLICY")
        self.assertEqual(body["escalation_preview"]["target_role"], "supervisor")

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-w05-test-003")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "needs_review")
        self.assertEqual(run["reason_code"], "NO_MATCHING_POLICY")
        self.assertIsNone(run["matched_policy_id"])

    # ------------------------------------------------------------------
    # 用例 4: 缺失字段校验
    # 缺少 region -> FastAPI 422 校验错误；数据库不得偷偷保存默认地区
    # ------------------------------------------------------------------
    def test_04_missing_region_rejected_with_422_and_no_db_record(self):
        payload = {
            "request_id": "req-w05-test-004",
            "task_type": "billing_policy_check",
            "purchased_at": "2026-08-15",
        }
        resp = self.client.post("/api/v1/policy-check", json=payload, headers=AGENT_TOKEN)

        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["detail"]
        self.assertTrue(
            any("region" in item.get("loc", []) for item in errors),
            f"422 错误应定位到 region 字段，实际: {errors}",
        )

        # 数据库不得偷偷保存默认地区，也不应落任何任务记录 (无默认值、无假成功)
        self.assertIsNone(database.get_task_run_by_request_id("req-w05-test-004"))

    # ------------------------------------------------------------------
    # 用例 5: 身份鉴权拦截
    # 非法 Token -> 401 默认拒绝；查询不执行、数据库无记录
    # ------------------------------------------------------------------
    def test_05_invalid_token_denied_with_401_and_no_db_record(self):
        payload = {
            "request_id": "req-w05-test-005",
            "task_type": "billing_policy_check",
            "region": "华东",
            "purchased_at": "2026-08-15",
            "amount": 500.0,
        }
        resp = self.client.post("/api/v1/policy-check", json=payload, headers=INVALID_TOKEN)

        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid authentication token", resp.json()["detail"])

        # 默认拒绝: 业务查询不执行，数据库不落任何任务记录
        self.assertIsNone(database.get_task_run_by_request_id("req-w05-test-005"))


if __name__ == "__main__":
    unittest.main()

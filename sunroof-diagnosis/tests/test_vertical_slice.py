"""
天窗总成只读诊断 · 阶段 A 节点 02 确定性垂直薄片 —— 自动化集成测试 (8/8)

测试原则 (回链 specs/discovery-brief-v1.md §6 成功判据 + vertical-slice-contract.md §5):
1. 只断言 HTTP 状态码不算测试: 每个用例同时核对「用户可见响应」与「数据库 task_runs 终态」;
2. 三向对账: API 响应、数据库终态与测试使用同一 request_id;
3. 零泄漏断言: 阻断/拒答路径的响应体不得包含任何未被授权的规格内容或跨项目组标识
   (保护指标 P-1 跨 OEM 越权 0 次 / P-2 过期 SOP 引用 0 次 / P-3 假装知道 0 次);
4. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

运行方式 (本项目根目录):
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
from application.services import HARD_LIMIT_DEVIATION_PCT  # noqa: E402

TOKEN_PQE_A = {"X-Auth-Token": "token_pqe_oem_a"}
TOKEN_PQE_B = {"X-Auth-Token": "token_pqe_oem_b"}
INVALID_TOKEN = {"X-Auth-Token": "token_not_exist"}


class VerticalSliceIntegrationTests(unittest.TestCase):
    """集成测试链路: API 网关 -> 确定性规则引擎 -> SQLite (ACL 下推) -> 三方对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-slice-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每个用例独立初始化，保证确定性
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    def _count_specs(self, where_sql: str, params: tuple = ()) -> int:
        """直接查询 specs 表，用于核对夹具与过滤行为"""
        conn = database.get_db_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM specs WHERE {where_sql}", params
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    @staticmethod
    def _payload(request_id: str, **overrides):
        """构造合法请求体, 允许按用例覆盖字段"""
        payload = {
            "request_id": request_id,
            "task_type": "spec_check",
            "sn": "SR-20260928-8841",
            "part_no": "MOTOR-MOUNT-BOLT-03",
            "process_step": "torque_tightening",
            "measured_at": "2026-09-28",
            "measured_value": 8.2,
        }
        payload.update(overrides)
        return payload

    # ------------------------------------------------------------------
    # 用例 1: 成功路径 (answered != 合格)
    # 合法 PQE + 8.2 N·m (低于下限但偏差 -13.68% 未超 15% 硬限)
    # -> answered + 唯一现行规格 SPEC-SR-A-V5 出处 + 非符合判定作为证据
    # ------------------------------------------------------------------
    def test_01_success_path_returns_answered_with_citation_and_audit(self):
        resp = self.client.post(
            "/api/v1/spec-check", json=self._payload("req-sr-test-001"), headers=TOKEN_PQE_A
        )

        # --- 用户可见响应 ---
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-001")
        self.assertEqual(body["status"], "answered")
        self.assertFalse(body["conformance_passed"])  # answered != 合格
        self.assertIsNone(body["reason_code"])
        self.assertIsNone(body["reviewer_preview"])
        spec = body["spec"]
        self.assertEqual(spec["spec_id"], "SPEC-SR-A-V5")
        self.assertEqual(spec["version"], "v5.0")
        self.assertEqual(spec["effective_at"], "2026-06-01")
        self.assertEqual(spec["source_ref"], "QMS-SOP-SR-04-v5#Section-3.2")
        self.assertEqual(spec["unit"], "N·m")
        evaluation = body["evaluation"]
        self.assertEqual(evaluation["nominal_value"], 9.5)
        self.assertEqual(evaluation["tolerance"], "+0.5/-0.5")
        self.assertEqual(evaluation["measured_value"], 8.2)
        self.assertEqual(evaluation["deviation_pct"], -13.68)
        self.assertEqual(evaluation["conformance"], "nonconforming")
        self.assertIn("lower", evaluation["formula_rule"])

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-sr-test-001")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "answered")
        self.assertIsNone(run["reason_code"])
        self.assertEqual(run["matched_spec_id"], "SPEC-SR-A-V5")
        self.assertEqual(run["user_role"], "pqe")

    # ------------------------------------------------------------------
    # 用例 2: 偏差硬限阻断
    # 8.0 N·m 偏差 -15.79% > 15% 硬限 -> needs_review / SPEC_HARD_LIMIT_EXCEEDED
    # 响应零规格内容: 无 spec_id / 名义值 / 来源, 审计不留隐性依据
    # ------------------------------------------------------------------
    def test_02_hard_limit_exceeded_blocks_with_zero_spec_content(self):
        resp = self.client.post(
            "/api/v1/spec-check",
            json=self._payload("req-sr-test-002", measured_value=8.0),
            headers=TOKEN_PQE_A,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-002")
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["conformance_passed"])
        self.assertEqual(body["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIsNone(body["spec"])        # 不返回规格出处
        self.assertIsNone(body["evaluation"])  # 不返回偏差计算依据
        self.assertEqual(body["reviewer_preview"]["target_role"], "quality_supervisor")

        # --- 零内容断言: 响应体不含规格库中的任何受控字段 ---
        text = resp.text
        self.assertNotIn("SPEC-SR-A-V5", text)
        self.assertNotIn("9.5", text)  # 名义值不得泄露
        self.assertNotIn("QMS-SOP-SR-04", text)

        # --- 数据库终态对账: 阻断路径不留隐性依据 ---
        run = database.get_task_run_by_request_id("req-sr-test-002")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "needs_review")
        self.assertEqual(run["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIsNone(run["matched_spec_id"])

    # ------------------------------------------------------------------
    # 用例 3: 已被替代版本拦截 (保护指标 P-2)
    # 测量日期 2026-05-15 落在 v4.0 历史窗口 -> is_current=0 行被 SQL 排除
    # -> NO_MATCHING_SPEC, 绝不引用旧版
    # ------------------------------------------------------------------
    def test_03_superseded_version_is_blocked_no_matching_spec(self):
        # 前置证据: 已被替代版本确实存在于库中 (拦截发生在 SQL 过滤，而非数据缺失)
        self.assertEqual(
            self._count_specs("spec_id = 'SPEC-SR-A-V4' AND is_current = 0"), 1
        )

        resp = self.client.post(
            "/api/v1/spec-check",
            json=self._payload("req-sr-test-003", measured_at="2026-05-15", measured_value=9.4),
            headers=TOKEN_PQE_A,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-003")
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["conformance_passed"])
        self.assertIsNone(body["spec"])  # 不允许返回已被替代版本
        self.assertEqual(body["reason_code"], "NO_MATCHING_SPEC")
        self.assertEqual(body["reviewer_preview"]["target_role"], "quality_supervisor")

        # --- 零引用断言: 响应体不得出现旧版本任何标识 ---
        text = resp.text
        self.assertNotIn("v4.0", text)
        self.assertNotIn("QMS-SOP-SR-04-v4", text)

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-sr-test-003")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "needs_review")
        self.assertEqual(run["reason_code"], "NO_MATCHING_SPEC")
        self.assertIsNone(run["matched_spec_id"])

    # ------------------------------------------------------------------
    # 用例 4: 缺失字段校验
    # 缺少 part_no -> FastAPI 422 校验错误；数据库不得偷偷保存默认值
    # ------------------------------------------------------------------
    def test_04_missing_part_no_rejected_with_422_and_no_db_record(self):
        payload = self._payload("req-sr-test-004")
        del payload["part_no"]
        resp = self.client.post("/api/v1/spec-check", json=payload, headers=TOKEN_PQE_A)

        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["detail"]
        self.assertTrue(
            any("part_no" in item.get("loc", []) for item in errors),
            f"422 错误应定位到 part_no 字段，实际: {errors}",
        )

        # 数据库不得偷偷保存默认零件，也不应落任何任务记录 (无默认值、无假成功)
        self.assertIsNone(database.get_task_run_by_request_id("req-sr-test-004"))

    # ------------------------------------------------------------------
    # 用例 5: 身份鉴权拦截
    # 非法 Token -> 401 默认拒绝；查询不执行、数据库无记录。
    # 缺失 Token -> 默认 PQE 培训身份 (教学模拟会话, 回链原仓库第 5 周惯例)
    # ------------------------------------------------------------------
    def test_05_invalid_token_denied_with_401_and_no_db_record(self):
        resp = self.client.post(
            "/api/v1/spec-check", json=self._payload("req-sr-test-005"), headers=INVALID_TOKEN
        )

        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid authentication token", resp.json()["detail"])

        # 默认拒绝: 业务查询不执行，数据库不落任何任务记录
        self.assertIsNone(database.get_task_run_by_request_id("req-sr-test-005"))

        # 缺失 Token 走默认培训身份 (OEM-A) -> 正常 answered
        resp_default = self.client.post(
            "/api/v1/spec-check", json=self._payload("req-sr-test-005b")
        )
        self.assertEqual(resp_default.status_code, 200)
        self.assertEqual(resp_default.json()["status"], "answered")

    # ------------------------------------------------------------------
    # 用例 6: 跨 OEM 项目组零泄漏 (保护指标 P-1)
    # SUNSHADE-RAIL-B7 仅存在于 OEM-B -> OEM-A 查询在 SQL 层即被排除
    # -> NO_MATCHING_SPEC 且响应体零 OEM-B 标识
    # ------------------------------------------------------------------
    def test_06_cross_oem_query_is_acl_blocked_with_zero_leakage(self):
        # 前置证据: 该零件的规格确实存在 (拦截发生在 ACL 过滤，而非数据缺失)
        self.assertEqual(
            self._count_specs("spec_id = 'SPEC-UNIQ-B' AND oem_project = 'OEM-B'"), 1
        )

        resp = self.client.post(
            "/api/v1/spec-check",
            json=self._payload(
                "req-sr-test-006",
                part_no="SUNSHADE-RAIL-B7",
                process_step="gap_check",
                measured_value=4.0,
            ),
            headers=TOKEN_PQE_A,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-006")
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "NO_MATCHING_SPEC")
        self.assertIsNone(body["spec"])

        # --- 零泄漏断言: 响应体不得出现 OEM-B 项目组的任何标识 ---
        text = resp.text
        self.assertNotIn("OEM-B", text)
        self.assertNotIn("SPEC-UNIQ-B", text)
        self.assertNotIn("QMS-SOP-SR-B-07", text)

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-sr-test-006")
        self.assertIsNotNone(run)
        self.assertEqual(run["reason_code"], "NO_MATCHING_SPEC")
        self.assertIsNone(run["matched_spec_id"])

    # ------------------------------------------------------------------
    # 用例 7: 同项目组规格冲突
    # HEAT-SHIELD-02 存在 v1.0 / v1.1 两条现行有效规格 -> SPEC_CONFLICT
    # 冲突版本均在请求者授权范围内, 预览可列出版本号; 不返回任何规格正文
    # ------------------------------------------------------------------
    def test_07_conflicting_current_specs_require_review(self):
        # 前置证据: 两条现行规格并存
        self.assertEqual(
            self._count_specs("part_no = 'HEAT-SHIELD-02' AND is_current = 1"), 2
        )

        resp = self.client.post(
            "/api/v1/spec-check",
            json=self._payload(
                "req-sr-test-007",
                part_no="HEAT-SHIELD-02",
                process_step="gap_check",
                measured_value=4.1,
            ),
            headers=TOKEN_PQE_A,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-007")
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "SPEC_CONFLICT")
        self.assertIsNone(body["spec"])
        self.assertIsNone(body["evaluation"])
        self.assertIn("v1.0", body["reviewer_preview"]["reason"])
        self.assertIn("v1.1", body["reviewer_preview"]["reason"])

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-sr-test-007")
        self.assertIsNotNone(run)
        self.assertEqual(run["reason_code"], "SPEC_CONFLICT")
        self.assertIsNone(run["matched_spec_id"])

    # ------------------------------------------------------------------
    # 用例 8: ACL 下推的成功面 (OEM-B 只见自己的规格)
    # 同零件 MOTOR-MOUNT-BOLT-03 在 OEM-B 有独立规格 (10.5 ± 0.5 N·m)
    # -> OEM-B PQE 命中自己的 SPEC-SR-B-V2, 响应体零 OEM-A 标识
    # ------------------------------------------------------------------
    def test_08_oem_b_pqe_sees_only_own_project_spec(self):
        resp = self.client.post(
            "/api/v1/spec-check",
            json=self._payload("req-sr-test-008", measured_value=10.6),
            headers=TOKEN_PQE_B,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["request_id"], "req-sr-test-008")
        self.assertEqual(body["status"], "answered")
        self.assertTrue(body["conformance_passed"])  # 10.6 在 10.0..11.0 内
        self.assertEqual(body["spec"]["spec_id"], "SPEC-SR-B-V2")
        self.assertEqual(body["spec"]["source_ref"], "QMS-SOP-SR-B-02-v2#Section-1.4")
        self.assertEqual(body["evaluation"]["nominal_value"], 10.5)
        self.assertEqual(body["evaluation"]["deviation_pct"], 0.95)
        self.assertEqual(body["evaluation"]["conformance"], "conforming")

        # --- 零泄漏断言: 响应体不得出现 OEM-A 项目组的任何标识 ---
        text = resp.text
        self.assertNotIn("OEM-A", text)
        self.assertNotIn("SPEC-SR-A-V5", text)

        # --- 数据库终态对账 ---
        run = database.get_task_run_by_request_id("req-sr-test-008")
        self.assertIsNotNone(run)
        self.assertEqual(run["matched_spec_id"], "SPEC-SR-B-V2")


if __name__ == "__main__":
    unittest.main()

"""
Week 07 一线角色与权限任务闭环 —— 自动化集成测试 (7/7)

测试原则 (回链 specs/mission-week-07.md 验收标准):
1. 只断言 HTTP 状态码不算测试: 每个用例同时核对「用户可见响应」与「数据库终态」;
2. 三向对账: HTTP 响应 / billing_tasks 终态 / task_action_runs 审计 共用同一 correlation_id;
3. 服务端授权 100% 强制: 越权动作 denied + 零泄漏 (candidate_policy 恒为 None);
4. 请求体自报角色一律 422 (extra="forbid"), 身份只来自服务端 X-Auth-Token;
5. 异常路径显式失败 (needs_review / INVALID_TASK_STATE / 401 / 404)，不允许假成功;
6. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

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

AGENT_TOKEN = {"X-Auth-Token": "token_agent_valid"}
SUPERVISOR_TOKEN = {"X-Auth-Token": "token_supervisor_secret"}
INVALID_TOKEN = {"X-Auth-Token": "token_not_exist"}


class FrontlineTaskClosureTests(unittest.TestCase):
    """集成测试链路: API 网关 -> RBAC 授权 -> 任务状态机 -> SQLite -> 三方对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fde-week07-test-")
        self.db_path = os.path.join(self._tmpdir.name, "northstar_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每个用例独立初始化，保证确定性
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    def _action(self, task_id: str, action: str, correlation_id: str,
                headers: dict = None, **extra):
        """发起任务动作请求 (默认客服身份)"""
        payload = {"task_id": task_id, "action": action, "correlation_id": correlation_id}
        payload.update(extra)
        return self.client.post(
            "/api/v1/tasks/action", json=payload, headers=headers or AGENT_TOKEN
        )

    def _get_task(self, task_id: str):
        """直接查询 billing_tasks 终态，用于核对数据库事实"""
        conn = database.get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM billing_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _action_runs(self, correlation_id: str):
        """按 correlation_id 汇集动作审计流水 (三向对账锚点之一)"""
        return database.get_task_action_runs_by_correlation(correlation_id)

    # ------------------------------------------------------------------
    # 用例 1: 常规工单闭环 (check_policy -> confirm)
    # 验收标准: 客服工单闭环 + 三向一致 (响应 / billing_tasks / task_action_runs)
    # ------------------------------------------------------------------
    def test_01_normal_task_check_confirm_three_way_consistency(self):
        corr = "corr-w07-t001"

        # --- Step 1: check_policy -> answered + 可核验政策依据 ---
        resp = self._action("TASK-W07-001", "check_policy", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["task_id"], "TASK-W07-001")
        self.assertEqual(body["case_id"], "NS-CASE-2026-0801-001")
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["reason_code"], "SUPPORTED_CURRENT_SOURCES")
        self.assertEqual(body["correlation_id"], corr)
        policy = body["candidate_policy"]
        self.assertIsNotNone(policy)
        self.assertEqual(policy["policy_id"], "POL-2026-BIL-01")
        self.assertEqual(policy["version"], "v3")
        self.assertEqual(policy["effective_at"], "2026-01-01")
        self.assertEqual(policy["source_id"], "KB-BILLING-2026-08")
        self.assertIn("refund", policy["formula_rule"])
        self.assertEqual(set(body["allowed_actions"]), {"confirm", "refuse", "escalate"})

        # --- 数据库终态: billing_tasks 回填命中政策 ---
        task = self._get_task("TASK-W07-001")
        self.assertEqual(task["status"], "answered")
        self.assertEqual(task["matched_policy_id"], "POL-2026-BIL-01")

        # --- 复用 Week 05 引擎证据: task_runs 审计同一 correlation_id ---
        week05_run = database.get_task_run_by_request_id(corr)
        self.assertIsNotNone(week05_run)
        self.assertEqual(week05_run["status"], "answered")

        # --- Step 2: confirm -> confirmed (确定性闭环) ---
        resp2 = self._action("TASK-W07-001", "confirm", corr)
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["status"], "confirmed")
        self.assertFalse(body2["is_idempotent"])
        self.assertEqual(body2["allowed_actions"], [])

        task = self._get_task("TASK-W07-001")
        self.assertEqual(task["status"], "confirmed")
        self.assertEqual(task["reason_code"], "SUPPORTED_CURRENT_SOURCES")

        # --- 三向对账: 响应 / billing_tasks / task_action_runs 共用 correlation_id ---
        runs = self._action_runs(corr)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["action"], "check_policy")
        self.assertEqual(runs[0]["status"], "answered")
        self.assertEqual(runs[0]["user_id"], "agent_small_zhang")
        self.assertEqual(runs[1]["action"], "confirm")
        self.assertEqual(runs[1]["status"], "confirmed")
        for run in runs:
            self.assertEqual(run["correlation_id"], corr)

    # ------------------------------------------------------------------
    # 用例 2: 金额超限显式阻断 -> escalate (不猜答案)
    # 验收标准: 异常必须显式 needs_review + 一键升级主管
    # ------------------------------------------------------------------
    def test_02_amount_over_threshold_blocks_then_escalates(self):
        corr = "corr-w07-t002"

        # --- check_policy: 2500 元 -> needs_review / AMOUNT_EXCEEDS_LIMIT ---
        resp = self._action("TASK-W07-002", "check_policy", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "AMOUNT_EXCEEDS_LIMIT")
        self.assertIsNone(body["candidate_policy"])          # 阻断即零泄漏
        self.assertIsNotNone(body["escalation_preview"])
        self.assertEqual(body["escalation_preview"]["target_role"], "supervisor")
        self.assertEqual(body["allowed_actions"], ["escalate"])

        task = self._get_task("TASK-W07-002")
        self.assertEqual(task["status"], "needs_review")
        self.assertEqual(task["reason_code"], "AMOUNT_EXCEEDS_LIMIT")

        # --- escalate: 显式升级 (缺省继承任务原因码) ---
        resp2 = self._action("TASK-W07-002", "escalate", corr, idempotency_key="idem-w07-002")
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["status"], "escalated")
        self.assertEqual(body2["reason_code"], "AMOUNT_EXCEEDS_LIMIT")
        self.assertFalse(body2["is_idempotent"])

        task = self._get_task("TASK-W07-002")
        self.assertEqual(task["status"], "escalated")
        self.assertEqual(task["idempotency_key"], "idem-w07-002")

        runs = self._action_runs(corr)
        self.assertEqual([r["action"] for r in runs], ["check_policy", "escalate"])

    # ------------------------------------------------------------------
    # 用例 3: 升级动作幂等 (同幂等键重复提交)
    # 验收标准: 重复提交 -> already_applied, 库中无重复生效记录
    # ------------------------------------------------------------------
    def test_03_escalate_is_idempotent_on_repeated_submission(self):
        corr = "corr-w07-t003"
        self._action("TASK-W07-002", "check_policy", corr)
        first = self._action(
            "TASK-W07-002", "escalate", corr, idempotency_key="idem-dup-key"
        ).json()
        self.assertEqual(first["status"], "escalated")
        self.assertFalse(first["is_idempotent"])

        # --- 幂等重放: 同幂等键二次提交 ---
        replay = self._action(
            "TASK-W07-002", "escalate", corr, idempotency_key="idem-dup-key"
        ).json()
        self.assertEqual(replay["status"], "escalated")
        self.assertEqual(replay["reason_code"], "ALREADY_APPLIED")
        self.assertTrue(replay["is_idempotent"])

        # --- 库中无重复生效: 状态唯一, 幂等键不变 ---
        task = self._get_task("TASK-W07-002")
        self.assertEqual(task["status"], "escalated")
        self.assertEqual(task["idempotency_key"], "idem-dup-key")

        # --- 审计记录两次提交, 但只有第一次真正生效 ---
        runs = self._action_runs(corr)
        escalate_runs = [r for r in runs if r["action"] == "escalate"]
        self.assertEqual(len(escalate_runs), 2)
        self.assertEqual(escalate_runs[0]["reason_code"], "AMOUNT_EXCEEDS_LIMIT")
        self.assertEqual(escalate_runs[1]["reason_code"], "ALREADY_APPLIED")

    # ------------------------------------------------------------------
    # 用例 4: 客服直调主管结案接口 -> 服务端一票否决 + 零泄漏
    # 验收标准: 服务端 100% 强制授权; 无权限看不到政策内容; 前端不作安全屏障
    # ------------------------------------------------------------------
    def test_04_agent_cannot_resolve_and_no_policy_leak(self):
        corr = "corr-w07-t004"
        self._action("TASK-W07-002", "check_policy", corr)
        self._action("TASK-W07-002", "escalate", corr, idempotency_key="idem-t004")

        # --- 客服身份调用 supervisor_resolve ---
        resp = self._action("TASK-W07-002", "supervisor_resolve", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "denied")
        self.assertEqual(body["reason_code"], "ACTION_NOT_ALLOWED")
        self.assertIsNone(body["candidate_policy"])
        self.assertIsNone(body["escalation_preview"])

        # --- 零泄漏: 响应全文不含主管专属政策内容 (POL-2026-REF-09) ---
        self.assertNotIn("POL-2026-REF-09", resp.text)
        self.assertNotIn("特殊退款", resp.text)

        # --- 工单状态不被越权动作改变 (denied 仅审计) ---
        task = self._get_task("TASK-W07-002")
        self.assertEqual(task["status"], "escalated")

        # --- 安全审计留痕: 越权尝试记录身份与动作 ---
        runs = self._action_runs(corr)
        denied_runs = [r for r in runs if r["status"] == "denied"]
        self.assertEqual(len(denied_runs), 1)
        self.assertEqual(denied_runs[0]["action"], "supervisor_resolve")
        self.assertEqual(denied_runs[0]["user_id"], "agent_small_zhang")

    # ------------------------------------------------------------------
    # 用例 5: 主管结案闭环 (escalated -> action_completed) + 三向一致
    # 验收标准: 响应 / DB 终态 / 审计日志 同一 correlation_id 对上账
    # ------------------------------------------------------------------
    def test_05_supervisor_resolve_closes_task_with_three_way_evidence(self):
        agent_corr = "corr-w07-t005-agent"
        self._action("TASK-W07-002", "check_policy", agent_corr)
        self._action("TASK-W07-002", "escalate", agent_corr, idempotency_key="idem-t005")

        sup_corr = "corr-w07-t005-supervisor"
        resp = self._action(
            "TASK-W07-002", "supervisor_resolve", sup_corr, headers=SUPERVISOR_TOKEN
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "action_completed")
        self.assertEqual(body["reason_code"], "SUPPORTED_CURRENT_SOURCES")
        self.assertIsNone(body["candidate_policy"])
        self.assertEqual(body["correlation_id"], sup_corr)

        # --- DB 终态 ---
        task = self._get_task("TASK-W07-002")
        self.assertEqual(task["status"], "action_completed")
        self.assertEqual(task["reason_code"], "SUPPORTED_CURRENT_SOURCES")

        # --- 审计: 主管身份 + 动作 + 终态 ---
        runs = self._action_runs(sup_corr)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["action"], "supervisor_resolve")
        self.assertEqual(run["status"], "action_completed")
        self.assertEqual(run["user_role"], "supervisor")
        self.assertEqual(run["user_id"], "supervisor_wang")
        self.assertEqual(run["correlation_id"], sup_corr)

        # --- 三向一致: HTTP 响应 / billing_tasks 终态 / task_action_runs 审计 ---
        self.assertEqual(body["status"], task["status"])
        self.assertEqual(body["status"], run["status"])
        self.assertEqual(body["correlation_id"], run["correlation_id"])

    # ------------------------------------------------------------------
    # 用例 6: 请求体自报角色一律 422 (前端绝不是安全屏障)
    # 验收标准: 前端角色输入永远不作为安全屏障, 身份只来自服务端会话
    # ------------------------------------------------------------------
    def test_06_self_reported_role_in_body_is_rejected_with_422(self):
        corr = "corr-w07-t006"
        payload = {
            "task_id": "TASK-W07-001",
            "action": "check_policy",
            "correlation_id": corr,
            "role": "supervisor",  # 客户端伪造: 请求体自报角色
        }
        resp = self.client.post("/api/v1/tasks/action", json=payload, headers=AGENT_TOKEN)

        # --- 封闭契约: 未知字段直接 422 校验失败 ---
        self.assertEqual(resp.status_code, 422)
        self.assertIn("role", resp.text)

        # --- 无状态变更、无审计 (请求在校验层就被拒绝) ---
        task = self._get_task("TASK-W07-001")
        self.assertEqual(task["status"], "initialized")
        self.assertEqual(self._action_runs(corr), [])

    # ------------------------------------------------------------------
    # 用例 7: refuse 显式拒绝留痕 + 非法 Token 401 零副作用
    # 验收标准: 客服可确认/拒绝/升级; 未知身份 401 且不执行、不落库
    # ------------------------------------------------------------------
    def test_07_refuse_action_audited_and_invalid_token_gets_401(self):
        corr = "corr-w07-t007"
        self._action("TASK-W07-001", "check_policy", corr)

        # --- refuse: answered -> refused (显式拒绝, 不会被静默猜测替代) ---
        resp = self._action("TASK-W07-001", "refuse", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "refused")
        self.assertEqual(body["reason_code"], "REFUSED_BY_AGENT")
        self.assertEqual(body["allowed_actions"], [])

        task = self._get_task("TASK-W07-001")
        self.assertEqual(task["status"], "refused")
        self.assertEqual(task["reason_code"], "REFUSED_BY_AGENT")

        # --- refused 终态不可再 confirm: 显式失败, 状态不漂移 ---
        resp2 = self._action("TASK-W07-001", "confirm", corr)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["status"], "refused")
        self.assertEqual(resp2.json()["reason_code"], "INVALID_TASK_STATE")
        self.assertEqual(self._get_task("TASK-W07-001")["status"], "refused")

        # --- 非法 Token: 401 + 零审计、零查询、零状态变更 ---
        audit_before = len(self._action_runs(corr))
        resp3 = self._action(
            "TASK-W07-001", "check_policy", "corr-w07-t007-invalid", headers=INVALID_TOKEN
        )
        self.assertEqual(resp3.status_code, 401)
        self.assertEqual(resp3.json()["detail"], "Invalid authentication token")
        self.assertEqual(self._action_runs("corr-w07-t007-invalid"), [])
        self.assertEqual(len(self._action_runs(corr)), audit_before)
        self.assertEqual(self._get_task("TASK-W07-001")["status"], "refused")


if __name__ == "__main__":
    unittest.main()
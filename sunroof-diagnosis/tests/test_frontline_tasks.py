"""
阶段 A 节点 04 双角色任务闭环 —— 自动化集成测试 (9/9)

测试原则 (回链 task-state-model.md + specs/role-and-permission-matrix.md §2):
1. 只断言 HTTP 状态码不算测试: 每个用例同时核对「用户可见响应」与「数据库终态」;
2. 三向对账: HTTP 响应 / spec_check_tasks 终态 / task_action_runs 审计 共用同一 correlation_id;
3. 服务端授权 100% 强制: 越权动作 denied + 零泄漏 (candidate_spec 恒为 None,
   跨项目拒绝不回显归属项目组的 case_id/SN/零件号 —— P-1 硬门);
4. 请求体自报角色一律 422 (extra="forbid"), 身份只来自服务端 X-Auth-Token;
5. 异常路径显式失败 (needs_review / INVALID_TASK_STATE / 401 / 404)，不允许假成功;
6. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

运行方式 (本目录下):
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

PQE_A_TOKEN = {"X-Auth-Token": "token_pqe_oem_a"}          # OEM-A 一线 PQE
PQE_B_TOKEN = {"X-Auth-Token": "token_pqe_oem_b"}          # OEM-B 一线 PQE
SUPERVISOR_TOKEN = {"X-Auth-Token": "token_qa_supervisor_secret"}  # 质量主管 (跨项目只读)
INVALID_TOKEN = {"X-Auth-Token": "token_not_exist"}


class FrontlineTaskClosureTests(unittest.TestCase):
    """集成测试链路: API 网关 -> RBAC/任务级 ACL -> 任务状态机 -> SQLite -> 三方对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-node04-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_test.db")
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
        """发起任务动作请求 (默认 OEM-A 一线 PQE 身份)"""
        payload = {"task_id": task_id, "action": action, "correlation_id": correlation_id}
        payload.update(extra)
        return self.client.post(
            "/api/v1/tasks/action", json=payload, headers=headers or PQE_A_TOKEN
        )

    def _get_task(self, task_id: str):
        """直接查询 spec_check_tasks 终态，用于核对数据库事实"""
        conn = database.get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM spec_check_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _action_runs(self, correlation_id: str):
        """按 correlation_id 汇集动作审计流水 (三向对账锚点之一)"""
        return database.get_task_action_runs_by_correlation(correlation_id)

    # ------------------------------------------------------------------
    # 用例 1: 常规任务闭环 (check_spec -> confirm, 幂等重放) + 三向一致
    # 验收点: 一线依据核对结果确认执行 + 响应/DB/审计同一 correlation_id 对上账
    # ------------------------------------------------------------------
    def test_01_normal_task_check_confirm_three_way_consistency(self):
        corr = "corr-n04-t001"

        # --- Step 1: check_spec -> answered + 可核验规格出处 ---
        resp = self._action("TASK-N04-001", "check_spec", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["task_id"], "TASK-N04-001")
        self.assertEqual(body["case_id"], "SR-CASE-2026-1001-001")
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["reason_code"], "SUPPORTED_CURRENT_SOURCES")
        self.assertEqual(body["correlation_id"], corr)
        spec = body["candidate_spec"]
        self.assertIsNotNone(spec)
        self.assertEqual(spec["spec_id"], "SPEC-SR-A-V5")
        self.assertEqual(spec["version"], "v5.0")
        self.assertEqual(spec["effective_at"], "2026-06-01")
        self.assertEqual(spec["source_ref"], "QMS-SOP-SR-04-v5#Section-3.2")
        self.assertEqual(spec["unit"], "N·m")
        self.assertEqual(body["evaluation"]["deviation_pct"], 1.05)  # (9.6-9.5)/9.5*100
        self.assertEqual(body["evaluation"]["conformance"], "conforming")
        self.assertEqual(set(body["allowed_actions"]), {"confirm", "refuse", "escalate"})

        # --- 数据库终态: spec_check_tasks 回填命中规格 ---
        task = self._get_task("TASK-N04-001")
        self.assertEqual(task["status"], "answered")
        self.assertEqual(task["matched_spec_id"], "SPEC-SR-A-V5")

        # --- 复用节点 02 引擎证据: task_runs 审计同一 correlation_id (跨节点血缘锚点) ---
        node02_run = database.get_task_run_by_request_id(corr)
        self.assertIsNotNone(node02_run)
        self.assertEqual(node02_run["status"], "answered")

        # --- Step 2: confirm -> confirmed (确定性闭环) ---
        resp2 = self._action("TASK-N04-001", "confirm", corr)
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["status"], "confirmed")
        self.assertFalse(body2["is_idempotent"])
        self.assertEqual(body2["allowed_actions"], [])

        task = self._get_task("TASK-N04-001")
        self.assertEqual(task["status"], "confirmed")
        self.assertEqual(task["reason_code"], "SUPPORTED_CURRENT_SOURCES")

        # --- Step 3: confirm 幂等重放 -> ALREADY_APPLIED, 终态不漂移 ---
        replay = self._action("TASK-N04-001", "confirm", corr).json()
        self.assertEqual(replay["status"], "confirmed")
        self.assertEqual(replay["reason_code"], "ALREADY_APPLIED")
        self.assertTrue(replay["is_idempotent"])
        self.assertEqual(self._get_task("TASK-N04-001")["status"], "confirmed")

        # --- 三向对账: 响应 / spec_check_tasks / task_action_runs 共用 correlation_id ---
        runs = self._action_runs(corr)
        self.assertEqual(len(runs), 3)
        self.assertEqual(runs[0]["action"], "check_spec")
        self.assertEqual(runs[0]["status"], "answered")
        self.assertEqual(runs[0]["user_id"], "pqe_chen_oema")
        self.assertEqual(runs[1]["action"], "confirm")
        self.assertEqual(runs[1]["status"], "confirmed")
        self.assertEqual(runs[2]["reason_code"], "ALREADY_APPLIED")
        for run in runs:
            self.assertEqual(run["correlation_id"], corr)

    # ------------------------------------------------------------------
    # 用例 2: 偏差超硬限显式阻断 -> escalate (不猜答案)
    # 验收点: 异常必须显式 needs_review + 零规格内容 + 一键升级质量主管
    # ------------------------------------------------------------------
    def test_02_hard_limit_blocks_then_escalates(self):
        corr = "corr-n04-t002"

        # --- check_spec: 11.5 N·m (偏差 +21.05% > 15%) -> needs_review ---
        resp = self._action("TASK-N04-002", "check_spec", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIsNone(body["candidate_spec"])             # 阻断即零泄漏
        self.assertIsNone(body["evaluation"])
        self.assertIsNotNone(body["escalation_preview"])
        self.assertEqual(body["escalation_preview"]["target_role"], "quality_supervisor")
        self.assertEqual(body["allowed_actions"], ["escalate"])

        task = self._get_task("TASK-N04-002")
        self.assertEqual(task["status"], "needs_review")
        self.assertEqual(task["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIsNone(task["matched_spec_id"])

        # --- escalate: 显式升级 (缺省继承任务原因码) ---
        resp2 = self._action("TASK-N04-002", "escalate", corr, idempotency_key="idem-n04-002")
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["status"], "escalated")
        self.assertEqual(body2["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertFalse(body2["is_idempotent"])

        task = self._get_task("TASK-N04-002")
        self.assertEqual(task["status"], "escalated")
        self.assertEqual(task["idempotency_key"], "idem-n04-002")

        runs = self._action_runs(corr)
        self.assertEqual([r["action"] for r in runs], ["check_spec", "escalate"])

    # ------------------------------------------------------------------
    # 用例 3: 升级动作幂等 (同幂等键重复提交)
    # 验收点: 重复提交 -> already_applied + is_idempotent, 库中无重复生效记录
    # ------------------------------------------------------------------
    def test_03_escalate_is_idempotent_on_repeated_submission(self):
        corr = "corr-n04-t003"
        self._action("TASK-N04-002", "check_spec", corr)
        first = self._action(
            "TASK-N04-002", "escalate", corr, idempotency_key="idem-dup-key"
        ).json()
        self.assertEqual(first["status"], "escalated")
        self.assertFalse(first["is_idempotent"])

        # --- 幂等重放: 同幂等键二次提交 ---
        replay = self._action(
            "TASK-N04-002", "escalate", corr, idempotency_key="idem-dup-key"
        ).json()
        self.assertEqual(replay["status"], "escalated")
        self.assertEqual(replay["reason_code"], "ALREADY_APPLIED")
        self.assertTrue(replay["is_idempotent"])

        # --- 库中无重复生效: 状态唯一, 幂等键不变 ---
        task = self._get_task("TASK-N04-002")
        self.assertEqual(task["status"], "escalated")
        self.assertEqual(task["idempotency_key"], "idem-dup-key")

        # --- 审计记录两次提交, 但只有第一次真正生效 ---
        runs = self._action_runs(corr)
        escalate_runs = [r for r in runs if r["action"] == "escalate"]
        self.assertEqual(len(escalate_runs), 2)
        self.assertEqual(escalate_runs[0]["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertEqual(escalate_runs[1]["reason_code"], "ALREADY_APPLIED")

    # ------------------------------------------------------------------
    # 用例 4: PQE 直调主管结案接口 -> 服务端一票否决 + 零泄漏
    # 验收点: 服务端 100% 强制授权; 无权限看不到规格内容; 前端不作安全屏障
    # ------------------------------------------------------------------
    def test_04_pqe_cannot_resolve_and_no_spec_leak(self):
        corr = "corr-n04-t004"
        self._action("TASK-N04-002", "check_spec", corr)
        self._action("TASK-N04-002", "escalate", corr, idempotency_key="idem-t004")

        # --- PQE 身份调用 supervisor_resolve (主管专属动作) ---
        resp = self._action("TASK-N04-002", "supervisor_resolve", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "denied")
        self.assertEqual(body["reason_code"], "ACTION_NOT_ALLOWED")
        self.assertIsNone(body["candidate_spec"])
        self.assertIsNone(body["evaluation"])
        self.assertIsNone(body["escalation_preview"])
        self.assertEqual(body["allowed_actions"], [])

        # --- 零泄漏: 响应全文不含命中规格标识与规格标题 ---
        self.assertNotIn("SPEC-SR-A-V5", resp.text)
        self.assertNotIn("检验标准", resp.text)

        # --- 任务状态不被越权动作改变 (denied 仅审计) ---
        task = self._get_task("TASK-N04-002")
        self.assertEqual(task["status"], "escalated")

        # --- 安全审计留痕: 越权尝试记录身份与动作 ---
        runs = self._action_runs(corr)
        denied_runs = [r for r in runs if r["status"] == "denied"]
        self.assertEqual(len(denied_runs), 1)
        self.assertEqual(denied_runs[0]["action"], "supervisor_resolve")
        self.assertEqual(denied_runs[0]["user_id"], "pqe_chen_oema")
        self.assertEqual(denied_runs[0]["user_role"], "pqe")

    # ------------------------------------------------------------------
    # 用例 5: 质量主管结案闭环 (escalated -> action_completed) + 三向一致
    # 验收点: 响应 / DB 终态 / 审计日志 同一 correlation_id 对上账
    # ------------------------------------------------------------------
    def test_05_supervisor_resolve_closes_task_with_three_way_evidence(self):
        agent_corr = "corr-n04-t005-agent"
        self._action("TASK-N04-002", "check_spec", agent_corr)
        self._action("TASK-N04-002", "escalate", agent_corr, idempotency_key="idem-t005")

        sup_corr = "corr-n04-t005-supervisor"
        resp = self._action(
            "TASK-N04-002", "supervisor_resolve", sup_corr, headers=SUPERVISOR_TOKEN
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "action_completed")
        self.assertEqual(body["reason_code"], "SUPPORTED_CURRENT_SOURCES")
        self.assertIsNone(body["candidate_spec"])
        self.assertEqual(body["correlation_id"], sup_corr)

        # --- DB 终态 ---
        task = self._get_task("TASK-N04-002")
        self.assertEqual(task["status"], "action_completed")
        self.assertEqual(task["reason_code"], "SUPPORTED_CURRENT_SOURCES")

        # --- 审计: 主管身份 + 动作 + 终态 ---
        runs = self._action_runs(sup_corr)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["action"], "supervisor_resolve")
        self.assertEqual(run["status"], "action_completed")
        self.assertEqual(run["user_role"], "quality_supervisor")
        self.assertEqual(run["user_id"], "supervisor_li")
        self.assertEqual(run["correlation_id"], sup_corr)

        # --- 三向一致: HTTP 响应 / spec_check_tasks 终态 / task_action_runs 审计 ---
        self.assertEqual(body["status"], task["status"])
        self.assertEqual(body["status"], run["status"])
        self.assertEqual(body["correlation_id"], run["correlation_id"])

    # ------------------------------------------------------------------
    # 用例 6: 请求体自报角色一律 422 (前端绝不是安全屏障)
    # 验收点: 前端角色输入永远不作为安全屏障, 身份只来自服务端会话
    # ------------------------------------------------------------------
    def test_06_self_reported_role_in_body_is_rejected_with_422(self):
        corr = "corr-n04-t006"
        payload = {
            "task_id": "TASK-N04-001",
            "action": "check_spec",
            "correlation_id": corr,
            "role": "quality_supervisor",  # 客户端伪造: 请求体自报角色
        }
        resp = self.client.post("/api/v1/tasks/action", json=payload, headers=PQE_A_TOKEN)

        # --- 封闭契约: 未知字段直接 422 校验失败 ---
        self.assertEqual(resp.status_code, 422)
        self.assertIn("role", resp.text)

        # --- 无状态变更、无审计 (请求在校验层就被拒绝) ---
        task = self._get_task("TASK-N04-001")
        self.assertEqual(task["status"], "initialized")
        self.assertEqual(self._action_runs(corr), [])

    # ------------------------------------------------------------------
    # 用例 7: refuse 显式拒绝留痕 + 非法 Token 401 零副作用
    # 验收点: PQE 可确认/拒绝/升级; 未知身份 401 且不执行、不落库
    # ------------------------------------------------------------------
    def test_07_refuse_action_audited_and_invalid_token_gets_401(self):
        corr = "corr-n04-t007"
        self._action("TASK-N04-001", "check_spec", corr)

        # --- refuse: answered -> refused (显式拒绝, 不会被静默猜测替代) ---
        resp = self._action("TASK-N04-001", "refuse", corr)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "refused")
        self.assertEqual(body["reason_code"], "REFUSED_BY_AGENT")
        self.assertEqual(body["allowed_actions"], [])

        task = self._get_task("TASK-N04-001")
        self.assertEqual(task["status"], "refused")
        self.assertEqual(task["reason_code"], "REFUSED_BY_AGENT")

        # --- refused 终态不可再 confirm: 显式失败, 状态不漂移 ---
        resp2 = self._action("TASK-N04-001", "confirm", corr)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["status"], "refused")
        self.assertEqual(resp2.json()["reason_code"], "INVALID_TASK_STATE")
        self.assertEqual(self._get_task("TASK-N04-001")["status"], "refused")

        # --- 非法 Token: 401 + 零审计、零查询、零状态变更 ---
        audit_before = len(self._action_runs(corr))
        resp3 = self._action(
            "TASK-N04-001", "check_spec", "corr-n04-t007-invalid", headers=INVALID_TOKEN
        )
        self.assertEqual(resp3.status_code, 401)
        self.assertEqual(resp3.json()["detail"], "Invalid authentication token")
        self.assertEqual(self._action_runs("corr-n04-t007-invalid"), [])
        self.assertEqual(len(self._action_runs(corr)), audit_before)
        self.assertEqual(self._get_task("TASK-N04-001")["status"], "refused")

    # ------------------------------------------------------------------
    # 用例 8: 版本冲突/零匹配显式阻断 + 跨项目直调任务 denied 零泄漏 (D6)
    # 验收点: 判定表分支透传进状态机; 跨 OEM PQE 触达他组任务被 100% 拦截且零回显
    # ------------------------------------------------------------------
    def test_08_conflict_no_match_and_cross_project_denial(self):
        # --- SPEC_CONFLICT: HEAT-SHIELD-02 双现行版本 -> needs_review ---
        resp = self._action("TASK-N04-003", "check_spec", "corr-n04-t008-a")
        body = resp.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "SPEC_CONFLICT")
        self.assertIsNone(body["candidate_spec"])
        task3 = self._get_task("TASK-N04-003")
        self.assertEqual(task3["status"], "needs_review")
        self.assertEqual(task3["reason_code"], "SPEC_CONFLICT")

        # --- NO_MATCHING_SPEC: 未知零件 -> needs_review (绝不猜答案) ---
        resp2 = self._action("TASK-N04-004", "check_spec", "corr-n04-t008-b")
        body2 = resp2.json()
        self.assertEqual(body2["status"], "needs_review")
        self.assertEqual(body2["reason_code"], "NO_MATCHING_SPEC")
        self.assertIsNone(body2["candidate_spec"])

        # --- D6 跨项目拒绝: OEM-B PQE 直调 OEM-A 任务 -> denied + 零回显 ---
        corr = "corr-n04-t008-c"
        resp3 = self._action("TASK-N04-001", "check_spec", corr, headers=PQE_B_TOKEN)
        self.assertEqual(resp3.status_code, 200)
        body3 = resp3.json()
        self.assertEqual(body3["status"], "denied")
        self.assertEqual(body3["reason_code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(body3["case_id"], "N/A-ACL-DENIED")  # 不回显他组案例号
        # 零泄漏: 响应不含 OEM-A 任务归属标识/命中规格/零件号
        self.assertNotIn("SR-CASE-2026-1001-001", resp3.text)
        self.assertNotIn("SPEC-SR-A-V5", resp3.text)
        self.assertNotIn("MOTOR-MOUNT-BOLT-03", resp3.text)

        # --- OEM-A 任务状态不变 (denied 仅审计); 引擎查询未执行 (零 task_runs 行) ---
        self.assertEqual(self._get_task("TASK-N04-001")["status"], "initialized")
        self.assertIsNone(database.get_task_run_by_request_id(corr))

        # --- 安全审计留痕: 跨项目越权记录身份与动作 ---
        runs = self._action_runs(corr)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "denied")
        self.assertEqual(runs[0]["user_id"], "pqe_zhao_oemb")
        self.assertEqual(runs[0]["user_role"], "pqe")
        self.assertEqual(runs[0]["correlation_id"], corr)

    # ------------------------------------------------------------------
    # 用例 9: 未知任务 404 零写入 + 任务队列 ACL 过滤 (PQE 只见本项目组任务)
    # 验收点: 404 显式失败不猜答案; 跨项目任务在队列层即被排除 (P-1)
    # ------------------------------------------------------------------
    def test_09_unknown_task_404_and_task_queue_acl_filtering(self):
        corr = "corr-n04-t009"

        # --- 未知 task_id: 端点层 404 拦截, 零写入 ---
        resp = self._action("TASK-NOT-EXIST", "check_spec", corr)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._action_runs(corr), [])

        # --- OEM-A PQE 队列: 仅见本项目组 4 条任务 (夹具全为 OEM-A) ---
        resp_a = self.client.get("/api/v1/tasks", headers=PQE_A_TOKEN)
        self.assertEqual(resp_a.status_code, 200)
        tasks_a = resp_a.json()["tasks"]
        self.assertEqual(len(tasks_a), 4)
        self.assertTrue(all(t["oem_project"] == "OEM-A" for t in tasks_a))
        self.assertNotIn("allowed_oem_projects", resp_a.text)  # ACL 声明不进队列摘要

        # --- OEM-B PQE 队列: 零条任务 (跨项目任务在队列层即被排除, P-1) ---
        resp_b = self.client.get("/api/v1/tasks", headers=PQE_B_TOKEN)
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_b.json()["tasks"], [])

        # --- 质量主管队列: 跨项目只读, 可见全部 4 条 ---
        resp_sup = self.client.get("/api/v1/tasks", headers=SUPERVISOR_TOKEN)
        self.assertEqual(resp_sup.status_code, 200)
        self.assertEqual(len(resp_sup.json()["tasks"]), 4)

        # --- 无 Token: 默认 PQE 培训身份 (OEM-A), 队列可用; 未知 Token 401 ---
        resp_default = self.client.get("/api/v1/tasks")
        self.assertEqual(resp_default.status_code, 200)
        resp_invalid = self.client.get("/api/v1/tasks", headers=INVALID_TOKEN)
        self.assertEqual(resp_invalid.status_code, 401)


if __name__ == "__main__":
    unittest.main()







"""
阶段 D 批次 1 生产加固 —— 自动化集成测试

回链 specs/production-hardening-brief-v1.md D1 + adr-005-production-hardening-adaptations.md
D1-D7 与 decision-memo-02 §3 生产化差距 1/2/3 (并发竞态/模拟鉴权/审计无防篡改):
  01 迁移 005 幂等: 新表 + version/哈希链列存在, 重复 init_db 终态不变
  02 连接硬化: WAL + busy_timeout=5000
  03 乐观锁单元: update_task_state 三态 (fresh 应用 / stale 拒绝 / 缺省等价冻结基线)
  04 E2E 陈旧版本动作: 读到过期快照 -> CONCURRENCY_CONFLICT (终态不变 + 审计入链 + 重读重试成功)
  05 状态写入全部携带版本: answered/needs_review 两分支 version 逐次自增
  06 审计哈希链: 全动作流后三重校验 ok + 链头与行数对账
  07 篡改检测: 直改业务字段 -> 定位首个坏 run_id (entry_hash_mismatch)
  08 删行检测: 删中间审计行 -> 前向链接断裂 (prev_hash_mismatch)
  09 链头破坏检测: 直改链头 entry_count -> head_ok=False
  10 历史行补链: 置空哈希后 init_db 重跑 -> 重新入链且校验 ok
  11 令牌签发 E2E: /api/v1/auth/token -> sr1.* 过 whoami 与任务队列 (ACL 生效)
  12 过期令牌: exp < now -> EXPIRED -> 401
  13 吊销生命周期: 主管吊销 -> 即时 401; 重复吊销幂等; PQE 吊销 403
  14 改签/畸形令牌: 载荷篡改 BAD_SIGNATURE / 缺段 MALFORMED -> 401
  15 签发零存在性泄漏: 未知用户与坏凭证同封套 401
  16 严格模式: SUNROOF_REQUIRE_SIGNED_TOKENS=1 -> 静态令牌退役 401, 签名令牌通行
  17 回归守卫: 无头默认身份不变 + 未知静态令牌仍 401

运行: python -m unittest tests.test_production_hardening -v
"""
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import unittest.mock

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application import token_service  # noqa: E402
from application.main import app  # noqa: E402

PQE_A_TOKEN = {"X-Auth-Token": "token_pqe_oem_a"}
SUPERVISOR_TOKEN = {"X-Auth-Token": "token_qa_supervisor_secret"}


class ProductionHardeningTests(unittest.TestCase):
    """批次 1 三件套: 乐观锁 (D1-a) + 审计哈希链 (D1-b) + 签名短时令牌 (D1-c)"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-phaseD-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_phase_d.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每个用例独立初始化，保证确定性
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)
        os.environ.pop("SUNROOF_REQUIRE_SIGNED_TOKENS", None)

    # ------------------------------------------------------------------
    # 测试辅助
    # ------------------------------------------------------------------
    def _action(self, task_id: str, action: str, correlation_id: str,
                headers: dict = None, **extra):
        payload = {"task_id": task_id, "action": action, "correlation_id": correlation_id}
        payload.update(extra)
        return self.client.post(
            "/api/v1/tasks/action", json=payload, headers=headers or PQE_A_TOKEN
        )

    def _get_task(self, task_id: str):
        conn = database.get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM spec_check_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _sql(self, statement: str, params: tuple = ()) -> None:
        """直连 SQLite 执行运维/模拟语句 (仅测试用: 模拟并发写者与篡改攻击)。"""
        conn = database.get_db_connection()
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def _issue(self, user_id: str, credential: str, ttl: int = None):
        payload = {"user_id": user_id, "credential": credential}
        if ttl is not None:
            payload["ttl_seconds"] = ttl
        return self.client.post("/api/v1/auth/token", json=payload)

    def _signed_headers(self, user_id: str, credential: str) -> dict:
        resp = self._issue(user_id, credential)
        self.assertEqual(resp.status_code, 200)
        return {"X-Auth-Token": resp.json()["access_token"]}

    # ------------------------------------------------------------------
    # 01 迁移 005 幂等 + 02 连接硬化
    # ------------------------------------------------------------------
    def test_01_migration_005_idempotent_schema(self):
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("audit_chain_head", tables)
            self.assertIn("revoked_tokens", tables)
            task_cols = {r[1] for r in conn.execute("PRAGMA table_info(spec_check_tasks)")}
            self.assertIn("version", task_cols)
            run_cols = {r[1] for r in conn.execute("PRAGMA table_info(task_action_runs)")}
            self.assertIn("prev_entry_hash", run_cols)
            self.assertIn("entry_hash", run_cols)
        finally:
            conn.close()
        # 幂等: 重复 init_db 不改变链头与既有行 (空库链头 entry_count=0)
        database.init_db(self.db_path)
        head = database.get_audit_chain_head()
        self.assertEqual(head["entry_count"], 0)
        self.assertEqual(head["last_entry_hash"], "")
        self.assertTrue(database.verify_audit_chain()["ok"])

    def test_02_connection_wal_and_busy_timeout(self):
        conn = database.get_db_connection()
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 03 乐观锁单元 + 04 E2E 陈旧版本 + 05 版本自增
    # ------------------------------------------------------------------
    def test_03_update_task_state_expected_version_three_states(self):
        task = self._get_task("TASK-N04-001")
        self.assertEqual(task["version"], 0)
        # fresh 版本 -> 应用
        self.assertTrue(database.update_task_state(
            "TASK-N04-001", "answered", matched_spec_id="SPEC-SR-A-V5", expected_version=0))
        # stale 版本 -> 拒绝 (任务终态不变)
        self.assertFalse(database.update_task_state(
            "TASK-N04-001", "confirmed", expected_version=0))
        fresh = self._get_task("TASK-N04-001")
        self.assertEqual(fresh["status"], "answered")
        self.assertEqual(fresh["version"], 1)
        # 缺省 (expected_version=None) 与冻结基线等价: 无条件更新 + 版本自增
        self.assertTrue(database.update_task_state("TASK-N04-001", "confirmed"))
        final = self._get_task("TASK-N04-001")
        self.assertEqual(final["status"], "confirmed")
        self.assertEqual(final["version"], 2)

    def test_04_stale_version_action_structured_conflict(self):
        corr, corr_conflict = "corr-d01-t041", "corr-d01-t042"
        resp = self._action("TASK-N04-001", "check_spec", corr)
        self.assertEqual(resp.json()["status"], "answered")
        fresh = self._get_task("TASK-N04-001")

        # 模拟并发竞态: 状态机读到陈旧快照 (真实写者已把版本推进)
        stale_snapshot = dict(fresh)
        stale_snapshot["version"] = fresh["version"] - 1
        real_get = database.get_spec_check_task
        with unittest.mock.patch(
            "application.tasks.get_spec_check_task",
            lambda tid: stale_snapshot if tid == "TASK-N04-001" else real_get(tid),
        ):
            conflict = self._action("TASK-N04-001", "confirm", corr_conflict)

        body = conflict.json()
        self.assertEqual(body["reason_code"], "CONCURRENCY_CONFLICT")
        self.assertEqual(body["status"], "answered")       # 任务终态不变
        self.assertFalse(body["is_idempotent"])
        self.assertIn("重新读取任务", body["message"])
        # DB 事实: 状态与版本均未被冲突写者改变
        after = self._get_task("TASK-N04-001")
        self.assertEqual(after["status"], "answered")
        self.assertEqual(after["version"], fresh["version"])
        # 冲突事件同样审计留痕且入链 (三向对账: 响应 / 终态 / 审计)
        runs = database.get_task_action_runs_by_correlation(corr_conflict)
        self.assertEqual(runs[-1]["reason_code"], "CONCURRENCY_CONFLICT")
        self.assertEqual(runs[-1]["action"], "confirm")
        self.assertTrue(database.verify_audit_chain()["ok"])
        # 重读后重试 -> 成功 (显式失败不猜答案, 客户端负责重读)
        retry = self._action("TASK-N04-001", "confirm", "corr-d01-t043")
        self.assertEqual(retry.json()["reason_code"], "SUPPORTED_CURRENT_SOURCES")
        self.assertEqual(self._get_task("TASK-N04-001")["status"], "confirmed")

    def test_05_every_state_write_bumps_version(self):
        # answered 分支
        resp = self._action("TASK-N04-001", "check_spec", "corr-d01-t051")
        self.assertEqual(resp.json()["status"], "answered")
        self.assertEqual(self._get_task("TASK-N04-001")["version"], 1)
        # needs_review 分支 (硬限超限夹具)
        resp = self._action("TASK-N04-002", "check_spec", "corr-d01-t052")
        self.assertEqual(resp.json()["status"], "needs_review")
        self.assertEqual(resp.json()["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertEqual(self._get_task("TASK-N04-002")["version"], 1)
        # 版本冲突分支夹具 (SPEC_CONFLICT) 同样携带版本
        resp = self._action("TASK-N04-003", "check_spec", "corr-d01-t053")
        self.assertEqual(resp.json()["reason_code"], "SPEC_CONFLICT")
        self.assertEqual(self._get_task("TASK-N04-003")["version"], 1)

    # ------------------------------------------------------------------
    # 06 哈希链完整性 + 07/08/09 篡改/删行/链头攻击检测 + 10 历史行补链
    # ------------------------------------------------------------------
    def _build_action_traffic(self):
        """构造覆盖全分支的审计流量 (denied 越权也入链), 返回 (关联ID列表, 审计行数)。"""
        self._action("TASK-N04-001", "check_spec", "corr-d01-c01")
        self._action("TASK-N04-001", "confirm", "corr-d01-c02")
        self._action("TASK-N04-001", "supervisor_resolve", "corr-d01-c03")   # PQE 越权 -> denied
        self._action("TASK-N04-002", "check_spec", "corr-d01-c04")           # needs_review
        self._action("TASK-N04-002", "escalate", "corr-d01-c05", idempotency_key="idem-d01-01")
        self._action("TASK-N04-002", "escalate", "corr-d01-c06", idempotency_key="idem-d01-01")  # 重放
        conn = database.get_db_connection()
        try:
            rows = conn.execute("SELECT * FROM task_action_runs ORDER BY rowid ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def test_06_audit_chain_integrity_over_full_traffic(self):
        runs = self._build_action_traffic()
        self.assertGreaterEqual(len(runs), 6)
        result = database.verify_audit_chain()
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["first_bad"])
        self.assertTrue(result["head_ok"])
        self.assertEqual(result["entry_count"], len(runs))
        # 链头与末行一致 (三向对账之上的第四重: 链头锚点)
        head = database.get_audit_chain_head()
        self.assertEqual(head["last_run_id"], runs[-1]["run_id"])
        self.assertEqual(head["entry_count"], len(runs))
        self.assertEqual(head["last_entry_hash"], runs[-1]["entry_hash"])
        # 前向链接抽样: 每行 prev = 前行 entry_hash (首行 prev = 空串)
        prev = ""
        for run in runs:
            self.assertEqual(run["prev_entry_hash"], prev)
            prev = run["entry_hash"]

    def test_07_business_field_tamper_detected(self):
        self._build_action_traffic()
        conn = database.get_db_connection()
        try:
            run_ids = [r["run_id"] for r in conn.execute(
                "SELECT run_id FROM task_action_runs ORDER BY rowid ASC").fetchall()]
        finally:
            conn.close()
        victim = run_ids[0]
        # 攻击者直改业务字段 (把 check_spec 伪装成 refused)
        self._sql("UPDATE task_action_runs SET action = 'refused' WHERE run_id = ?", (victim,))
        result = database.verify_audit_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_bad"]["run_id"], victim)
        self.assertEqual(result["first_bad"]["reason"], "entry_hash_mismatch")

    def test_08_row_deletion_detected_by_forward_link(self):
        self._build_action_traffic()
        conn = database.get_db_connection()
        try:
            run_ids = [r["run_id"] for r in conn.execute(
                "SELECT run_id FROM task_action_runs ORDER BY rowid ASC").fetchall()]
        finally:
            conn.close()
        victim, successor = run_ids[1], run_ids[2]
        self._sql("DELETE FROM task_action_runs WHERE run_id = ?", (victim,))
        result = database.verify_audit_chain()
        self.assertFalse(result["ok"])
        self.assertFalse(result["head_ok"])   # 链头 entry_count 与实际行数不再一致
        self.assertEqual(result["first_bad"]["run_id"], successor)
        self.assertEqual(result["first_bad"]["reason"], "prev_hash_mismatch")

    def test_09_chain_head_tamper_detected(self):
        self._build_action_traffic()
        self._sql("UPDATE audit_chain_head SET entry_count = 999 WHERE id = 1")
        result = database.verify_audit_chain()
        self.assertFalse(result["ok"])
        self.assertFalse(result["head_ok"])
        self.assertIsNone(result["first_bad"])

    def test_10_backfill_rechains_legacy_rows_idempotently(self):
        self._build_action_traffic()
        # 模拟"升级前"的历史表: 哈希列置空 + 链头复位
        self._sql("UPDATE task_action_runs SET prev_entry_hash = NULL, entry_hash = NULL")
        self._sql(
            "UPDATE audit_chain_head SET last_entry_hash = '', last_run_id = '', entry_count = 0")
        self.assertFalse(database.verify_audit_chain()["ok"])  # 未入链行被识别
        # init_db 重跑 -> 一次性补链 (业务字段零改动, 只补哈希注记)
        database.init_db(self.db_path)
        result = database.verify_audit_chain()
        self.assertTrue(result["ok"], result)
        conn = database.get_db_connection()
        try:
            before = conn.execute(
                "SELECT COUNT(*) FROM task_action_runs").fetchone()[0]
        finally:
            conn.close()
        head = database.get_audit_chain_head()
        self.assertEqual(head["entry_count"], before)
        # 再次重跑: 幂等 (无空链行, 不重复计数)
        database.init_db(self.db_path)
        self.assertEqual(database.get_audit_chain_head()["entry_count"], before)
        self.assertTrue(database.verify_audit_chain()["ok"])

    # ------------------------------------------------------------------
    # 11-13 签名短时令牌: 签发 E2E / 过期 / 吊销生命周期
    # ------------------------------------------------------------------
    def _expired_token(self) -> str:
        """白盒构造签名合法但已过期的令牌 (exp < now), 保证过期路径确定性。"""
        import hashlib
        import hmac
        import json
        now = int(time.time())
        payload = {"sub": "pqe_chen_oema", "role": "pqe", "projects": ["OEM-A"],
                   "iat": now - 7200, "exp": now - 3600, "jti": "expiredjti00001"}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_b64 = token_service._b64url(canonical.encode("utf-8"))
        signature = token_service._b64url(
            hmac.new(token_service._signing_secret(), payload_b64.encode("ascii"),
                     hashlib.sha256).digest())
        return f"{token_service.TOKEN_PREFIX}{payload_b64}.{signature}"

    def test_11_issue_token_end_to_end_identity_and_acl(self):
        # PQE 签名令牌: whoami 身份正确 + 任务队列按 ACL 过滤
        pqe_headers = self._signed_headers("pqe_chen_oema", "token_pqe_oem_a")
        who = self.client.get("/api/v1/session/whoami", headers=pqe_headers)
        self.assertEqual(who.status_code, 200)
        self.assertEqual(who.json()["user_id"], "pqe_chen_oema")
        self.assertEqual(who.json()["user_role"], "pqe")
        self.assertEqual(who.json()["allowed_oem_projects"], ["OEM-A"])
        queue = self.client.get("/api/v1/tasks", headers=pqe_headers)
        self.assertEqual(queue.status_code, 200)
        for task in queue.json()["tasks"]:
            self.assertEqual(task["oem_project"], "OEM-A")
        # 种子任务全为 OEM-A (迁移 003 夹具语义); 补一条 OEM-B 夹具行以证明跨项目可见性
        self._sql(
            "INSERT OR IGNORE INTO spec_check_tasks"
            " (task_id, case_id, sn, oem_project, part_no, process_step, measured_at,"
            "  measured_value, assigned_pqe_id, correlation_id)"
            " VALUES ('TASK-N04-901', 'SR-CASE-2026-1002-001', 'SR-20261002-0001',"
            " 'OEM-B', 'MOTOR-MOUNT-BOLT-03', 'torque_tightening', '2026-08-15',"
            " 9.6, 'pqe_zhao_oemb', 'corr-hardening-oemb-001')")
        # OEM-B PQE 签名令牌: 只见 OEM-B (ACL 对称性)
        pqe_b_headers = self._signed_headers("pqe_zhao_oemb", "token_pqe_oem_b")
        queue_b = self.client.get("/api/v1/tasks", headers=pqe_b_headers)
        self.assertEqual(queue_b.status_code, 200)
        self.assertEqual({t["oem_project"] for t in queue_b.json()["tasks"]}, {"OEM-B"})
        # 主管签名令牌: 跨项目只读 (["*"] ACL 随签名载荷下发, 非请求体自报)
        sup_headers = self._signed_headers("supervisor_li", "token_qa_supervisor_secret")
        sup_queue = self.client.get("/api/v1/tasks", headers=sup_headers)
        self.assertEqual(sup_queue.status_code, 200)
        projects = {task["oem_project"] for task in sup_queue.json()["tasks"]}
        self.assertEqual(projects, {"OEM-A", "OEM-B"})

    def test_12_expired_token_rejected_with_default_deny(self):
        expired = self._expired_token()
        with self.assertRaises(token_service.TokenError) as ctx:
            token_service.decode_token(expired)
        self.assertEqual(ctx.exception.code, "EXPIRED")
        resp = self.client.get("/api/v1/session/whoami", headers={"X-Auth-Token": expired})
        self.assertEqual(resp.status_code, 401)

    def test_13_revoke_lifecycle_supervisor_only_idempotent(self):
        issued = self._issue("pqe_chen_oema", "token_pqe_oem_a")
        jti = issued.json()["jti"]
        headers = {"X-Auth-Token": issued.json()["access_token"]}
        self.assertEqual(
            self.client.get("/api/v1/session/whoami", headers=headers).status_code, 200)
        # PQE 无权吊销 -> 403
        forbidden = self.client.post(
            "/api/v1/auth/revoke", json={"jti": jti}, headers=PQE_A_TOKEN)
        self.assertEqual(forbidden.status_code, 403)
        # 主管吊销 -> 即时失效 (Default-Deny)
        revoked = self.client.post(
            "/api/v1/auth/revoke", json={"jti": jti, "reason": "轮换测试"},
            headers=SUPERVISOR_TOKEN)
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["revoked"])
        self.assertEqual(revoked.json()["revoked_by"], "supervisor_li")
        denied = self.client.get("/api/v1/session/whoami", headers=headers)
        self.assertEqual(denied.status_code, 401)
        # 重复吊销幂等: revoked=False 仍 200
        again = self.client.post(
            "/api/v1/auth/revoke", json={"jti": jti}, headers=SUPERVISOR_TOKEN)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["revoked"])

    # ------------------------------------------------------------------
    # 14-17 改签/畸形 / 零存在性泄漏 / 严格模式 / 回归守卫
    # ------------------------------------------------------------------
    def test_14_tampered_and_malformed_tokens_rejected(self):
        issued = self._issue("pqe_chen_oema", "token_pqe_oem_a").json()["access_token"]
        payload_b64, signature = issued[len(token_service.TOKEN_PREFIX):].split(".")
        # 改签: 载荷未重签但签名被篡改 -> BAD_SIGNATURE
        tampered_sig = signature[:-2] + ("BB" if not signature.endswith("BB") else "CC")
        tampered_token = f"{token_service.TOKEN_PREFIX}{payload_b64}.{tampered_sig}"
        with self.assertRaises(token_service.TokenError) as ctx:
            token_service.decode_token(tampered_token)
        self.assertEqual(ctx.exception.code, "BAD_SIGNATURE")
        self.assertEqual(self.client.get(
            "/api/v1/session/whoami", headers={"X-Auth-Token": tampered_token},
        ).status_code, 401)
        # 畸形: 缺签名段 -> MALFORMED
        malformed = f"{token_service.TOKEN_PREFIX}{payload_b64}"
        with self.assertRaises(token_service.TokenError) as ctx2:
            token_service.decode_token(malformed)
        self.assertEqual(ctx2.exception.code, "MALFORMED")
        self.assertEqual(self.client.get(
            "/api/v1/session/whoami", headers={"X-Auth-Token": malformed},
        ).status_code, 401)

    def test_15_issue_envelope_leaks_no_user_existence(self):
        unknown = self._issue("no_such_user", "whatever-credential")
        bad_cred = self._issue("pqe_chen_oema", "wrong-credential")
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(bad_cred.status_code, 401)
        # 零存在性泄漏: 未知用户与坏凭证同封套逐字节一致
        self.assertEqual(unknown.json(), bad_cred.json())

    def test_16_strict_mode_disables_static_teaching_tokens(self):
        os.environ["SUNROOF_REQUIRE_SIGNED_TOKENS"] = "1"
        static = self.client.get("/api/v1/session/whoami", headers=PQE_A_TOKEN)
        self.assertEqual(static.status_code, 401)   # 静态令牌退役
        signed = self._signed_headers("pqe_chen_oema", "token_pqe_oem_a")
        ok = self.client.get("/api/v1/session/whoami", headers=signed)
        self.assertEqual(ok.status_code, 200)       # 签名令牌成为唯一凭证面
        self.assertEqual(ok.json()["user_id"], "pqe_chen_oema")

    def test_17_regression_default_identity_and_unknown_static_token(self):
        # 无头默认培训身份不变 (冻结基线行为)
        default_identity = self.client.get("/api/v1/session/whoami")
        self.assertEqual(default_identity.status_code, 200)
        self.assertEqual(default_identity.json()["user_id"], "pqe_trainee_default")
        # 未知静态令牌仍 401 (既有 Default-Deny 语义零改动)
        unknown = self.client.get(
            "/api/v1/session/whoami", headers={"X-Auth-Token": "token_not_exist"})
        self.assertEqual(unknown.status_code, 401)


if __name__ == "__main__":
    unittest.main()





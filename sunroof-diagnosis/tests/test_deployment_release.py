"""
阶段 A 节点 05 可重复部署与回滚 —— 自动化集成测试 (9/9)

测试原则 (回链 ../fde-roadmap-v1.md §2 节点 ⑤ + specs/mission-week-08.md 验收 9 项):
1. 清单绑定: release-manifest-v1.json 三类绑定 (代码基线/配置 sha256/schema 版本) 且零秘密;
2. 收敛器纪律: 幂等 UPSERT + 白名单 Default-Deny + 零业务写副作用 + 零 DDL;
3. 配置真实流入引擎: 阈值配置生效可见; 缺失/非法值防御性回退默认 (不崩溃不误判);
4. 冒烟门有效性: 配置漂移 (answered -> needs_review) 必须被任务级冒烟拦截;
5. 回滚 = 配置收敛式: 重放 known-good 配置恢复行为, 全程零建表零删库;
6. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库, 结果 100% 确定。

运行方式 (本目录下):
    python -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.main import app  # noqa: E402
from application.models import (  # noqa: E402
    ProcessStep,
    SpecCheckRequest,
    TaskType,
    TrustedUser,
)
from application.services import get_hard_limit_deviation_pct, process_spec_check  # noqa: E402
from ci import redaction_scan  # noqa: E402
from deploy import apply_policy_config, smoke_w08  # noqa: E402

PQE_A_HEADERS = {"X-Auth-Token": "token_pqe_oem_a"}  # OEM-A 一线 PQE (教学模拟会话)
GOOD_CONFIG = os.path.join("config", "sunroof-config-w08.json")
BAD_CONFIG = os.path.join("config", "sunroof-config-w08-rc1-bad.json")
MANIFEST_PATH = "release-manifest-v1.json"
BUSINESS_TABLES = ("specs", "task_runs", "spec_check_tasks", "task_action_runs")
SECRET_MARKERS = (
    "token_pqe_oem_a", "token_pqe_oem_b", "token_qa_supervisor_secret", "X-Auth-Token",
)


class DeploymentReleaseTests(unittest.TestCase):
    """集成测试链路: 发布清单 -> 配置收敛器 -> 引擎阈值 -> 冒烟门 -> 配置收敛式回滚"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-node05-test-")
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
    def _apply(self, config_rel: str) -> dict:
        """对临时库执行配置收敛器 (known-good 或演练用错误配置)"""
        return apply_policy_config.apply_config(
            os.path.join(PROJECT_ROOT, config_rel), self.db_path
        )

    def _policy_projection(self) -> dict:
        """policy_config 业务投影 {config_key: config_value} (幂等比对口径)"""
        return {row["config_key"]: row["config_value"] for row in database.list_policy_config()}

    def _snapshot(self, table: str) -> list:
        conn = database.get_db_connection()
        try:
            return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        finally:
            conn.close()

    def _table_names(self) -> set:
        conn = database.get_db_connection()
        try:
            return {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()

    def _action(self, task_id: str, action: str, correlation_id: str, **extra):
        payload = {"task_id": task_id, "action": action, "correlation_id": correlation_id}
        payload.update(extra)
        return self.client.post("/api/v1/tasks/action", json=payload, headers=PQE_A_HEADERS)

    def _engine_request(self, request_id: str) -> SpecCheckRequest:
        """构造直调引擎的规格核对请求 (等价任务夹具 TASK-N04-001 的事实: +1.05% 偏差)"""
        return SpecCheckRequest(
            request_id=request_id,
            task_type=TaskType.SPEC_CHECK,
            sn="SR-20261001-0001",
            part_no="MOTOR-MOUNT-BOLT-03",
            process_step=ProcessStep.TORQUE_TIGHTENING,
            measured_at="2026-08-15",
            measured_value=9.6,
        )

    # ------------------------------------------------------------------
    # 用例 1: 发布清单三类绑定 + 清单零秘密
    # ------------------------------------------------------------------
    def test_01_release_manifest_bindings_and_no_secrets(self):
        with open(os.path.join(PROJECT_ROOT, MANIFEST_PATH), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for key in ("manifest_version", "release_id", "code_revision", "schema_version",
                    "config_version", "config_sha256", "fixtures_version",
                    "previous_known_good_release"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["schema_version"], "004")
        self.assertEqual(manifest["config_version"], "sunroof-config-w08-v1")
        with open(os.path.join(PROJECT_ROOT, GOOD_CONFIG), "rb") as f:
            config_digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(manifest["config_sha256"], config_digest)
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        for secret in SECRET_MARKERS:
            self.assertNotIn(secret, manifest_text)

    # ------------------------------------------------------------------
    # 用例 2: 收敛器幂等 (投影一致) + 白名单 Default-Deny
    # ------------------------------------------------------------------
    def test_02_converger_idempotent_and_default_deny(self):
        report_1 = self._apply(GOOD_CONFIG)
        self.assertFalse(report_1["errors"])
        self.assertTrue(all(item["converged"] for item in report_1["applied"]))
        projection_1 = self._policy_projection()
        self.assertEqual(projection_1["spec_check.hard_limit_deviation_pct"], "15.0")
        self.assertEqual(projection_1["release.config_version"], "sunroof-config-w08-v1")

        self._apply(GOOD_CONFIG)  # 重复执行同一配置
        self.assertEqual(self._policy_projection(), projection_1)  # 幂等: 投影一致

        # 非白名单键 -> 拒绝收敛 (Default-Deny), 数据库零变化
        evil_config = {
            "config_version": "evil-config",
            "release_id": "evil-release",
            "config_baseline": [
                {"config_key": "specs.nominal_value", "config_value": "999.0"}
            ],
            "config_overrides": [],
        }
        evil_path = os.path.join(self._tmpdir.name, "evil-config.json")
        with open(evil_path, "w", encoding="utf-8") as f:
            json.dump(evil_config, f, ensure_ascii=False)
        report_evil = apply_policy_config.apply_config(evil_path, self.db_path)
        self.assertTrue(report_evil["errors"])
        self.assertIn("白名单", "".join(report_evil["errors"]))
        self.assertEqual(self._policy_projection(), projection_1)

    # ------------------------------------------------------------------
    # 用例 3: 收敛器零业务写副作用 + 零 DDL (只触碰 policy_config)
    # ------------------------------------------------------------------
    def test_03_converger_touches_only_policy_config(self):
        tables_before = self._table_names()
        business_before = {t: self._snapshot(t) for t in BUSINESS_TABLES}
        self._apply(GOOD_CONFIG)
        self._apply(BAD_CONFIG)  # 演练错误配置同样不得触碰业务表
        self.assertEqual(self._table_names(), tables_before)  # 零建表零删表
        business_after = {t: self._snapshot(t) for t in BUSINESS_TABLES}
        self.assertEqual(business_after, business_before)

    # ------------------------------------------------------------------
    # 用例 4: 配置真实流入引擎 (阈值 0.5% -> 常规偏差也被阻断, 理由文本可见)
    # ------------------------------------------------------------------
    def test_04_engine_reads_config_threshold(self):
        custom_config = {
            "config_version": "sunroof-config-w08-t04",
            "release_id": "sunroof-app-w08-t04",
            "config_baseline": [
                {"config_key": "spec_check.hard_limit_deviation_pct", "config_value": "15.0"},
                {"config_key": "release.config_version", "config_value": "sunroof-config-w08-t04"},
            ],
            "config_overrides": [
                {"config_key": "spec_check.hard_limit_deviation_pct", "config_value": "0.5"}
            ],
        }
        custom_path = os.path.join(self._tmpdir.name, "custom-config.json")
        with open(custom_path, "w", encoding="utf-8") as f:
            json.dump(custom_config, f, ensure_ascii=False)
        report = apply_policy_config.apply_config(custom_path, self.db_path)
        self.assertFalse(report["errors"])

        response = self._action("TASK-N04-001", "check_spec", "corr-t04")
        body = response.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIsNone(body["candidate_spec"])  # 阻断路径零规格内容不变
        self.assertIn("0.5%", body["escalation_preview"]["reason"])
        self.assertEqual(self._policy_projection()["spec_check.hard_limit_deviation_pct"], "0.5")

    # ------------------------------------------------------------------
    # 用例 5: 引擎默认回退 (种子 15.0) + 非法值防御 (垃圾值不崩溃不误判)
    # ------------------------------------------------------------------
    def test_05_engine_fallback_default_and_garbage_defense(self):
        # 1) 仅种子默认值 (未跑收敛器): 15.0 生效 -> 21.05% 阻断, 理由文本含 15.0
        response = self._action("TASK-N04-002", "check_spec", "corr-t05a")
        body = response.json()
        self.assertEqual(body["reason_code"], "SPEC_HARD_LIMIT_EXCEEDED")
        self.assertIn("15.0%", body["escalation_preview"]["reason"])

        # 2) 直接写坏值 (绕过收敛器模拟配置损坏): 引擎防御性回退默认, 不崩溃
        conn = database.get_db_connection()
        try:
            conn.execute(
                "UPDATE policy_config SET config_value = 'garbage' "
                "WHERE config_key = 'spec_check.hard_limit_deviation_pct'"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(get_hard_limit_deviation_pct(), 15.0)
        response_2 = self._action("TASK-N04-001", "check_spec", "corr-t05b")
        self.assertEqual(response_2.json()["status"], "answered")  # 1.05% <= 回退后的 15.0

    # ------------------------------------------------------------------
    # 用例 6: 冒烟门有效性 —— 配置漂移 (answered -> needs_review) 必须被拦截
    # ------------------------------------------------------------------
    def test_06_smoke_gate_catches_config_drift(self):
        saved_request, saved_db_query = smoke_w08._request, smoke_w08._db_query

        def _restore():
            smoke_w08._request, smoke_w08._db_query = saved_request, saved_db_query

        self.addCleanup(_restore)

        def _make_fakes(check_body, task_row, engine_row):
            emitted_actions = []

            def fake_request(base_url, path, payload=None, token=None):
                action = payload["action"]
                emitted_actions.append(action)
                if action == "check_spec":
                    return 200, dict(check_body)
                return 200, {"status": "confirmed", "reason_code": None, "is_idempotent": False}

            def fake_db_query(db_path, sql, params=()):
                if "FROM spec_check_tasks" in sql:
                    return [dict(task_row)]
                if "FROM task_runs" in sql:
                    return [dict(engine_row)]
                if "FROM task_action_runs" in sql:
                    return [
                        {"action": a, "status": "answered" if a == "check_spec" else "confirmed",
                         "reason_code": None, "user_role": "pqe"}
                        for a in emitted_actions
                    ]
                return []

            return fake_request, fake_db_query

        # 1) known-good 响应形态: SMOKE-01 应当通过
        good_request, good_db = _make_fakes(
            {"status": "answered", "reason_code": None,
             "candidate_spec": {"spec_id": "SPEC-SR-A-V5"},
             "evaluation": {"deviation_pct": 1.05}},
            {"status": "answered", "matched_spec_id": "SPEC-SR-A-V5", "idempotency_key": None},
            {"status": "answered", "matched_spec_id": "SPEC-SR-A-V5"},
        )
        smoke_w08._request, smoke_w08._db_query = good_request, good_db
        self.assertTrue(smoke_w08.smoke_01("http://smoke-test", "unused.db")["passed"])

        # 2) 配置漂移形态 (阈值过紧 -> needs_review): SMOKE-01 必须拦截
        drift_request, drift_db = _make_fakes(
            {"status": "needs_review", "reason_code": "SPEC_HARD_LIMIT_EXCEEDED",
             "candidate_spec": None, "evaluation": None},
            {"status": "needs_review", "matched_spec_id": None, "idempotency_key": None},
            {"status": "needs_review", "matched_spec_id": None},
        )
        smoke_w08._request, smoke_w08._db_query = drift_request, drift_db
        self.assertFalse(smoke_w08.smoke_01("http://smoke-test", "unused.db")["passed"])

    # ------------------------------------------------------------------
    # 用例 7: 配置收敛式回滚 —— 重放 known-good 恢复行为, 全程零 DDL 零删库
    # ------------------------------------------------------------------
    def test_07_convergent_rollback_restores_behavior_without_ddl(self):
        user = TrustedUser(user_id="pqe_chen_oema", user_role="pqe",
                           allowed_oem_projects=["OEM-A"])
        tables_before = self._table_names()

        # 1) 注入错误配置 -> 引擎漂移 (+1.05% 常规偏差也被 0.01% 硬限阻断)
        self._apply(BAD_CONFIG)
        drifted = process_spec_check(self._engine_request("req-rollback-bad"), user)
        self.assertEqual(drifted.status.value, "needs_review")
        self.assertEqual(drifted.reason_code.value, "SPEC_HARD_LIMIT_EXCEEDED")

        # 2) 回滚 = 重放 known-good 配置 (不删库、不重建、零 DDL)
        self._apply(GOOD_CONFIG)
        self.assertEqual(get_hard_limit_deviation_pct(), 15.0)
        restored = process_spec_check(self._engine_request("req-rollback-good"), user)
        self.assertEqual(restored.status.value, "answered")
        self.assertEqual(restored.spec.spec_id, "SPEC-SR-A-V5")
        self.assertEqual(self._policy_projection()["release.config_version"],
                         "sunroof-config-w08-v1")
        self.assertEqual(self._policy_projection()["spec_check.hard_limit_deviation_pct"], "15.0")
        self.assertEqual(self._table_names(), tables_before)  # 全程零建表零删表

    # ------------------------------------------------------------------
    # 用例 8: Schema 完整性门 —— 迁移 001-004 幂等重放 + 必需表 + 种子默认值
    # ------------------------------------------------------------------
    def test_08_schema_integrity_and_migration_idempotent(self):
        conn = sqlite3.connect(self.db_path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("policy_config", tables)
            self.assertTrue({"specs", "task_runs", "spec_check_tasks",
                             "task_action_runs"}.issubset(tables))
            self.assertTrue(any(t.startswith("ingestion") for t in tables))
            seeds_before = dict(conn.execute(
                "SELECT config_key, config_value FROM policy_config").fetchall())
            specs_before = conn.execute("SELECT COUNT(*) FROM specs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(integrity, "ok")
        self.assertEqual(seeds_before["spec_check.hard_limit_deviation_pct"], "15.0")

        # 幂等重放迁移: 种子行不重复、夹具不重复、完整性保持
        database.init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            seeds_after = dict(conn.execute(
                "SELECT config_key, config_value FROM policy_config").fetchall())
            specs_after = conn.execute("SELECT COUNT(*) FROM specs").fetchone()[0]
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            conn.close()
        self.assertEqual(seeds_after, seeds_before)
        self.assertEqual(specs_after, specs_before)

    # ------------------------------------------------------------------
    # 用例 9: 脱敏扫描 —— 干净范围通过 + 注入违例必被抓 (双向验证门本身有效)
    # ------------------------------------------------------------------
    def test_09_redaction_scan_clean_and_catches_planted_violation(self):
        report = redaction_scan.scan()
        self.assertTrue(report["passed"], report["violations"])
        self.assertEqual(report["scanned_files"], len(redaction_scan.SCOPE_FILES))

        probe_clean = "_probe_clean_ci_scan.py"
        probe_bad = "_probe_bad_ci_scan.py"
        self.addCleanup(lambda: os.path.exists(
            os.path.join(PROJECT_ROOT, probe_clean))
            and os.remove(os.path.join(PROJECT_ROOT, probe_clean)))
        self.addCleanup(lambda: os.path.exists(
            os.path.join(PROJECT_ROOT, probe_bad))
            and os.remove(os.path.join(PROJECT_ROOT, probe_bad)))
        with open(os.path.join(PROJECT_ROOT, probe_clean), "w", encoding="utf-8") as f:
            f.write("value = 'safe telemetry'\nprint('status ok')\n")
        with open(os.path.join(PROJECT_ROOT, probe_bad), "w", encoding="utf-8") as f:
            f.write('print("credential seen:", "token_pqe_oem_a")\n')
            f.write('print("body seen:", "天窗电机固定螺栓拧紧扭矩检验标准")\n')

        clean_report = redaction_scan.scan([probe_clean])
        self.assertTrue(clean_report["passed"])

        bad_report = redaction_scan.scan([probe_bad])
        self.assertFalse(bad_report["passed"])
        rules = {v["rule"] for v in bad_report["violations"]}
        self.assertIn("secret_in_emission", rules)
        self.assertIn("spec_body_in_emission", rules)


if __name__ == "__main__":
    unittest.main()






"""
部署与发布自动化测试 (Week 08, 回链 specs/mission-week-08.md 验收标准)

覆盖九项:
  01 发布清单全绑定 (code_revision=week07-baseline / config sha256 / schema 003 / 无模糊表述)
  02 配置收敛器幂等收敛 + 未知策略 fail-fast
  03 git archive 干净环境真实部署 (独立 uvicorn 进程, 全新 DB, 契约回归)
  04 四条任务级冒烟 + 三向对账 (响应/billing_tasks/task_action_runs)
  05 动态脱敏: 401/越权响应、审计行、服务端日志均无凭据与政策正文
  06 回滚演练自动化: 注入 rc1-bad -> 冒烟拦截 -> 收敛回滚 -> 全新库复验 4/4
  07 deploy-README 与现实一致性 (引用物存在/测试数对齐/全新库前置写明)
  08 CI 流水线定义与证据完整性 (workflow/4 阶段/证据与 manifest 互链)
  09 静态脱敏扫描自身可运行且通过 (CI STAGE-3 的回归保护)

运行: python -m unittest tests.test_deployment_release -v
说明: 03/04/05 共享一个干净实例 (setUpClass); 06 自建两个实例; 全部进程在测试结束被终止。
"""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from application.database import MIGRATION_SCRIPTS, init_db  # noqa: E402
from deploy.apply_policy_config import apply_config  # noqa: E402
from deploy.smoke_w08 import run_all_smokes, smoke_01  # noqa: E402

AGENT_TOKEN = "token_agent_valid"
GOOD_CONFIG = os.path.join(PROJECT_ROOT, "config", "policy-config.json")
BAD_CONFIG = os.path.join(PROJECT_ROOT, "config", "policy-config-rc1-bad.json")
MANIFEST = os.path.join(PROJECT_ROOT, "release-manifest-v1.json")

# 动态断言用的敏感标记 (凭据 + 主管专属政策 + 正文字段名)
LEAK_PATTERNS = (
    "token_agent_valid",
    "token_supervisor_secret",
    "POL-2026-REF-09",
    "特殊退款",
    "special_refund",
    "formula_rule",
)


def _git(args):
    proc = subprocess.run(["git"] + args, cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace").strip()


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http(url, payload=None, token=None, timeout=15):
    """返回 (status_code, body_dict); 401/422/404 也作为可断言结果返回。"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _wait_health(base_url, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _boot_uvicorn(app_dir, db_path, log_path):
    """以全新进程 + 全新 DB 启动独立实例, 返回 (proc, log_file, base_url)。"""
    port = _free_port()
    env = {
        **os.environ,
        "FDE_DB_PATH": db_path,
        "FDE_ENV": "isolated-ut-env",
        "FDE_CONFIG_VERSION": "northstar-config-w08-v1",
        "PYTHONIOENCODING": "utf-8",
    }
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "application.main:app", "--port", str(port)],
        cwd=app_dir, env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_health(base_url):
        proc.terminate()
        log_file.close()
        raise RuntimeError(f"uvicorn failed to boot from clean env ({base_url})")
    return proc, log_file, base_url


def _archive_head(dest_dir):
    """git archive HEAD -> 干净发布包 (只含已提交文件, 与第三方拿到的发布物一致)。"""
    bundle = os.path.join(dest_dir, "bundle.zip")
    code, out = _git(["archive", "--format=zip", f"--output={bundle}", "HEAD"])
    assert code == 0, f"git archive failed: {out}"
    app_dir = os.path.join(dest_dir, "app")
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(app_dir)
    return app_dir


def _db_snapshot(db_path):
    """表清单 + policies 断言 + 行数 (回滚完整性核对的最小集合)。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        policies = [dict(r) for r in conn.execute(
            "SELECT policy_id, is_deprecated, effective_at, expires_at FROM policies "
            "ORDER BY policy_id")]
        return {
            "tables": tables,
            "policies": policies,
            "billing_tasks_rows": conn.execute(
                "SELECT count(*) FROM billing_tasks").fetchone()[0],
        }
    finally:
        conn.close()


class ManifestAndCiTests(unittest.TestCase):
    """发布清单 / 配置收敛器 / 文档与 CI 定义 —— 无需启动服务。"""

    def test_01_release_manifest_bindings(self):
        with open(MANIFEST, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for key in ("release_id", "artifact_id", "built_at_utc", "code_revision",
                    "tooling_revision", "schema_version", "schema_files", "config_version",
                    "config_sha256", "fixtures_version", "smoke_suite",
                    "previous_known_good_release", "rollback_policy"):
            self.assertIn(key, manifest, f"manifest 缺少绑定字段: {key}")
            self.assertTrue(manifest[key], f"manifest 字段为空: {key}")

        # 代码基线绑定: 必须精确等于 week07-baseline 标签的完整提交哈希
        code, rev = _git(["rev-parse", "week07-baseline"])
        self.assertEqual(code, 0)
        self.assertEqual(manifest["code_revision"], rev, "code_revision 未绑定 week07-baseline")
        self.assertEqual(len(manifest["code_revision"]), 40)

        # 工具链绑定: 必须是仓库中真实存在的提交, 且与代码基线不同层
        code, _ = _git(["rev-parse", "--verify", f"{manifest['tooling_revision']}^{{commit}}"])
        self.assertEqual(code, 0, "tooling_revision 不是可解析的提交")
        self.assertNotEqual(manifest["tooling_revision"], manifest["code_revision"])

        # Schema 绑定: 003 且与 database.MIGRATION_SCRIPTS 实际清单一致
        self.assertEqual(manifest["schema_version"], "003")
        self.assertEqual(f"{len(MIGRATION_SCRIPTS):03d}", manifest["schema_version"])
        self.assertEqual(
            {os.path.basename(p) for p in manifest["schema_files"]},
            {os.path.basename(p) for p in MIGRATION_SCRIPTS},
        )

        # 配置绑定: sha256 与发布配置文件逐字节一致
        with open(GOOD_CONFIG, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(manifest["config_sha256"], digest)

        # 回滚目标绑定 + 无模糊表述
        prev = manifest["previous_known_good_release"]
        self.assertEqual(prev["git_tag"], "week07-baseline")
        self.assertEqual(prev["code_revision"], manifest["code_revision"])
        self.assertEqual(len(manifest["smoke_suite"]["cases"]), 4)
        blob = json.dumps(manifest, ensure_ascii=False).lower()
        self.assertNotIn("latest", blob)
        self.assertNotIn("待定", blob)
        self.assertNotIn("todo", blob)

    def test_02_config_applier_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="fde-ut-applier-") as tmp:
            db_path = os.path.join(tmp, "applier.db")
            init_db(db_path)

            report = apply_config(GOOD_CONFIG, db_path)
            self.assertEqual(report["errors"], [])
            self.assertTrue(all(item["converged"] for item in report["applied"]))
            self.assertEqual(len(report["applied"]), 3)
            snap_1 = _db_snapshot(db_path)

            # 幂等: 重复应用后数据库快照逐字段不变
            report = apply_config(GOOD_CONFIG, db_path)
            self.assertTrue(all(item["converged"] for item in report["applied"]))
            self.assertEqual(_db_snapshot(db_path), snap_1)

            # fail fast: 未知策略不得谎报收敛
            bogus = os.path.join(tmp, "bogus.json")
            with open(bogus, "w", encoding="utf-8") as f:
                json.dump({"config_version": "ut-bogus", "release_id": "ut",
                           "policy_baseline": [{"policy_id": "POL-DOES-NOT-EXIST",
                                                "is_deprecated": 0}],
                           "policy_overrides": []}, f)
            report = apply_config(bogus, db_path)
            self.assertTrue(any(not item["converged"] for item in report["applied"]))

    def test_07_deploy_readme_consistency(self):
        readme_path = os.path.join(PROJECT_ROOT, "deploy-README.md")
        self.assertTrue(os.path.exists(readme_path), "deploy-README.md 缺失")
        with open(readme_path, "r", encoding="utf-8") as f:
            readme = f.read()
        for fragment in ("apply_policy_config.py", "smoke_w08.py",
                         "uvicorn application.main:app", "FDE_DB_PATH",
                         "release-manifest-v1.json", "SMOKE-01", "SMOKE-04",
                         "all_passed", "全新"):
            self.assertIn(fragment, readme, f"deploy-README 缺少关键内容: {fragment}")

        # 文档宣称的测试规模必须与真实发现数一致 (防止文档漂移)
        loader = unittest.TestLoader()
        suite = loader.discover(os.path.join(PROJECT_ROOT, "tests"), top_level_dir=PROJECT_ROOT)
        discovered = suite.countTestCases()
        self.assertGreaterEqual(discovered, 29)
        with open(os.path.join(PROJECT_ROOT, "README.md"), "r", encoding="utf-8") as f:
            main_readme = f.read()
        self.assertIn(str(discovered), main_readme,
                      f"README 的全量测试数与 unittest 发现数不一致 (应为 {discovered})")

        with open(os.path.join(PROJECT_ROOT, "ROLLBACK.md"), "r", encoding="utf-8") as f:
            rollback = f.read()
        self.assertIn("apply_policy_config.py", rollback, "ROLLBACK.md 未更新为配置收敛式回滚")

    def test_08_ci_pipeline_definition(self):
        with open(os.path.join(PROJECT_ROOT, ".github", "workflows", "ci.yml"),
                  "r", encoding="utf-8") as f:
            workflow = f.read()
        for fragment in ("ci/run_local_ci.py", "requirements.txt", "python-version",
                         "upload-artifact", "push", "pull_request"):
            self.assertIn(fragment, workflow, f"ci.yml 缺少: {fragment}")

        with open(os.path.join(PROJECT_ROOT, "ci", "run_local_ci.py"), "r", encoding="utf-8") as f:
            runner = f.read()
        for stage in ("STAGE-1-schema", "STAGE-2-unittest", "STAGE-3-redaction",
                      "STAGE-4-clean-env-smoke", "git", "archive"):
            self.assertIn(stage, runner, f"run_local_ci.py 缺少阶段/机制: {stage}")

        with open(MANIFEST, "r", encoding="utf-8") as f:
            artifact_id = json.load(f)["artifact_id"]
        with open(os.path.join(PROJECT_ROOT, "ci-evidence.md"), "r", encoding="utf-8") as f:
            evidence = f.read()
        self.assertIn(artifact_id, evidence, "ci-evidence.md 未与 manifest.artifact_id 互链")
        self.assertIn("STAGE-4-clean-env-smoke", evidence)
        self.assertIn("smokes_passed=4/4", evidence)

        log_path = os.path.join(PROJECT_ROOT, "evidence", "ci-run-w08.log")
        self.assertTrue(os.path.exists(log_path), "CI 原始证据日志缺失")
        with open(log_path, "r", encoding="utf-8") as f:
            self.assertIn("CI RESULT OK", f.read())

    def test_09_static_redaction_scan(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, os.path.join("ci", "redaction_scan.py")],
            cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, f"脱敏扫描未通过:\n{output[-600:]}")
        report = json.loads(output)
        self.assertTrue(report["passed"])
        self.assertEqual(report["violations"], [])
        self.assertGreaterEqual(report["emissions_checked"], 10)


class CleanEnvDeployTests(unittest.TestCase):
    """干净环境真实部署 (git archive HEAD -> 独立 uvicorn 进程) + 冒烟 + 动态脱敏。

    三个用例共享同一实例: test_03 验证可部署性与契约回归, test_04 在其上跑 4 条任务冒烟,
    test_05 复用冒烟产生的审计做动态脱敏断言。执行顺序按方法名序号保证。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="fde-ut-deploy-")
        cls.app_dir = _archive_head(cls._tmp.name)
        # 发布物完整性: 配置层/部署工具/清单必须随发布物分发
        for rel in ("config/policy-config.json", "deploy/apply_policy_config.py",
                    "deploy/smoke_w08.py", "release-manifest-v1.json",
                    "requirements.txt", "application/main.py"):
            assert os.path.exists(os.path.join(cls.app_dir, rel)), f"发布包缺少: {rel}"
        cls.db_path = os.path.join(cls._tmp.name, "clean_env.db")
        init_db(cls.db_path)
        report = apply_config(GOOD_CONFIG, cls.db_path)
        assert all(item["converged"] for item in report["applied"]), report
        cls.uvicorn_log = os.path.join(cls._tmp.name, "uvicorn.log")
        cls.proc, cls._log_file, cls.base_url = _boot_uvicorn(
            cls.app_dir, cls.db_path, cls.uvicorn_log)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls._log_file.close()
        cls._tmp.cleanup()

    def test_03_clean_env_deploy_health_and_tasks(self):
        status, health = _http(self.base_url + "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

        # 契约回归: 任务队列只对持有效凭证的调用方可见
        status, body = _http(self.base_url + "/api/v1/tasks", token=AGENT_TOKEN)
        self.assertEqual(status, 200)
        tasks = body["tasks"]
        self.assertEqual(len(tasks), 4)
        self.assertEqual({t["status"] for t in tasks}, {"initialized"})

        # 冻结契约 (Week 05 语义, Week 07 保持一致): 无效 token -> 401 默认拒绝;
        # 缺失 X-Auth-Token -> 默认通用客服身份 (教学模拟遗留, 已登记 stage-exit 遗留清单)
        status, body = _http(self.base_url + "/api/v1/tasks",
                             token="token_forged_by_attacker")
        self.assertEqual(status, 401)
        self.assertNotIn("token_forged_by_attacker", json.dumps(body))

    def test_04_task_smokes_three_way(self):
        report = run_all_smokes(self.base_url, self.db_path)
        self.assertTrue(report["all_passed"], json.dumps(report, ensure_ascii=False)[:1200])
        self.assertEqual(report["passed_count"], 4)
        for result in report["results"]:
            self.assertTrue(result["three_way_consistent"], result["id"])
            self.assertTrue(all(c["passed"] for c in result["checks"]), result["id"])

        # 三向对账直查: SMOKE-01 的响应/终态/审计经 correlation_id 一致
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            task = dict(conn.execute(
                "SELECT * FROM billing_tasks WHERE task_id='TASK-W07-001'").fetchone())
            self.assertEqual((task["status"], task["matched_policy_id"]),
                             ("confirmed", "POL-2026-BIL-01"))
            runs = [dict(r) for r in conn.execute(
                "SELECT action, status FROM task_action_runs WHERE correlation_id='corr-smoke-01' "
                "ORDER BY rowid")]
            self.assertEqual([(r["action"], r["status"]) for r in runs],
                             [("check_policy", "answered"), ("confirm", "confirmed")])
            engine_run = conn.execute(
                "SELECT status, matched_policy_id FROM task_runs WHERE request_id='corr-smoke-01'"
            ).fetchone()
            self.assertEqual((engine_run["status"], engine_run["matched_policy_id"]),
                             ("answered", "POL-2026-BIL-01"))
        finally:
            conn.close()

    def test_05_dynamic_redaction_and_server_log(self):
        # 1) 服务端进程日志: 不出现凭据与政策正文
        with open(self.uvicorn_log, "rb") as f:
            log_text = f.read().decode("utf-8", errors="replace")
        for pattern in LEAK_PATTERNS:
            self.assertNotIn(pattern, log_text, f"服务端日志泄漏: {pattern}")

        # 2) 无效凭证 401: 不回显所提交的伪造 token
        status, body = _http(self.base_url + "/api/v1/tasks/action",
                             payload={"task_id": "TASK-W07-003", "action": "confirm",
                                      "correlation_id": "corr-ut-badtoken"},
                             token="token_forged_by_attacker")
        self.assertEqual(status, 401)
        body_text = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("token_forged_by_attacker", body_text)
        for pattern in LEAK_PATTERNS:
            self.assertNotIn(pattern, body_text)

        # 3) 越权 denied: candidate_policy 恒空, 审计无正文
        status, body = _http(self.base_url + "/api/v1/tasks/action",
                             payload={"task_id": "TASK-W07-003",
                                      "action": "supervisor_resolve",
                                      "correlation_id": "corr-ut-redact"},
                             token=AGENT_TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual((body["status"], body["candidate_policy"]), ("denied", None))
        body_text = json.dumps(body, ensure_ascii=False)
        for pattern in LEAK_PATTERNS:
            self.assertNotIn(pattern, body_text)

        # 4) 审计行本身只含结构化标识
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM task_action_runs WHERE correlation_id='corr-ut-redact'")]
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["action"], rows[0]["status"], rows[0]["user_role"]),
                         ("supervisor_resolve", "denied", "support_agent"))
        for pattern in LEAK_PATTERNS:
            self.assertNotIn(pattern, json.dumps(rows, ensure_ascii=False))


class RollbackDrillTests(unittest.TestCase):
    """回滚演练自动化 (rollback-drill-v1.md 的可重放版本):
    注入 rc1-bad -> 收敛器照常 converged (配置层不判对错) -> 冒烟拦截 (health 假成功)
    -> 重放 known-good 收敛回滚 -> 全新库完整复验 4/4。全程无删库、无 DDL 变更。
    """

    def test_06_inject_block_rollback_reverify(self):
        with tempfile.TemporaryDirectory(prefix="fde-ut-rollback-") as tmp:
            app_dir = _archive_head(tmp)

            # ---- Round 0 基线: 全新库 + known-good ----
            db_d1 = os.path.join(tmp, "drill1.db")
            init_db(db_d1)
            report = apply_config(GOOD_CONFIG, db_d1)
            self.assertTrue(all(i["converged"] for i in report["applied"]))
            baseline = _db_snapshot(db_d1)

            # ---- Round 1 注入: 坏配置在配置层完全"合法" ----
            report = apply_config(BAD_CONFIG, db_d1)
            self.assertEqual(report["errors"], [])
            self.assertTrue(all(i["converged"] for i in report["applied"]),
                            "收敛器只对齐数据库与配置, 不判定配置正确性")

            proc_a, log_a, url_a = _boot_uvicorn(app_dir, db_d1, os.path.join(tmp, "a.log"))
            try:
                # 假成功: /health 依旧 200
                status, health = _http(url_a + "/health")
                self.assertEqual(status, 200)

                # 任务级冒烟拦截: SMOKE-01 失败, 业务终态 POLICY_CONFLICT
                result = smoke_01(url_a, db_d1)
                self.assertFalse(result["passed"], json.dumps(result, ensure_ascii=False)[:800])
                conn = sqlite3.connect(db_d1)
                try:
                    row = conn.execute(
                        "SELECT status, reason_code FROM billing_tasks "
                        "WHERE task_id='TASK-W07-001'").fetchone()
                    self.assertEqual(row, ("needs_review", "POLICY_CONFLICT"))
                finally:
                    conn.close()
            finally:
                proc_a.terminate()
                try:
                    proc_a.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc_a.kill()
                log_a.close()

            # ---- Round 2 回滚: 重放 known-good (无删库/无 DDL 变更) ----
            report = apply_config(GOOD_CONFIG, db_d1)
            self.assertTrue(all(i["converged"] for i in report["applied"]))
            rolled_back = _db_snapshot(db_d1)
            self.assertEqual(rolled_back["tables"], baseline["tables"], "表结构必须原样保留")
            self.assertEqual(rolled_back["policies"], baseline["policies"], "配置断言必须恢复")
            self.assertEqual(rolled_back["billing_tasks_rows"],
                             baseline["billing_tasks_rows"], "业务数据不得丢失")

            # ---- Round 3 复验: 全新库 + known-good -> 4/4 (冒烟一次性前置) ----
            db_d2 = os.path.join(tmp, "drill2.db")
            init_db(db_d2)
            report = apply_config(GOOD_CONFIG, db_d2)
            self.assertTrue(all(i["converged"] for i in report["applied"]))
            proc_b, log_b, url_b = _boot_uvicorn(app_dir, db_d2, os.path.join(tmp, "b.log"))
            try:
                report = run_all_smokes(url_b, db_d2)
                self.assertTrue(report["all_passed"],
                                json.dumps(report, ensure_ascii=False)[:1200])
                self.assertEqual(report["passed_count"], 4)
                self.assertEqual(_db_snapshot(db_d2)["tables"], baseline["tables"])
            finally:
                proc_b.terminate()
                try:
                    proc_b.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc_b.kill()
                log_b.close()





"""
阶段 D 批次 3 生产发布包 —— 自动化集成测试

回链 specs/production-hardening-brief-v1.md D3 + release-manifest-v2.json
(六类绑定, schema 005) + deploy/backup_snapshot.py + deploy/restore_snapshot.py:
  01 manifest v2 六类绑定齐全且零秘密 (凭据字面量全禁)
  02 config_sha256 与配置文件实算逐字节一致
  03 冻结合同清单 5 份 api-contract sha256 实算一致
  04 schema_version=005 且 D3 交付引用到位
  05 Dockerfile 非 root 化 (uid/gid 10001 + USER 非 root) + 构建期测试门以非 root 执行
  06 compose 生产形态 (SUNROOF_TOKEN_SECRET 注入 + 数据卷 + restart + healthcheck)
  07 备份快照: 在线一致快照 + 哈希链一致性门 + 元数据 sha256
  08 备份被篡改 => --verify-only 校验拒绝 (定位 entry_hash_mismatch)
  09 被篡改备份恢复 fail-fast => 目标库零触碰 (逐字节不变)
  10 正常恢复 => 原子替换逐字节一致 + 恢复后哈希链校验 ok + 安全快照留存
  11 脱敏扫描范围 +2 (备份/恢复工具在运行时代码扫描面内)

运行: python -m unittest tests.test_release_packaging_d3 -v
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.main import app  # noqa: E402
from ci import redaction_scan  # noqa: E402

MANIFEST_V2_PATH = "release-manifest-v2.json"
MANIFEST_V1_PATH = "release-manifest-v1.json"
CONFIG_PATH = os.path.join("config", "sunroof-config-w08.json")
DOCKERFILE_PATH = "Dockerfile"
COMPOSE_PATH = "docker-compose.yml"
PQE_A_TOKEN = {"X-Auth-Token": "token_pqe_oem_a"}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class ManifestV2Tests(unittest.TestCase):
    """D3-b: 发布清单 v2 六类绑定 (清单不含秘密, schema 推进到 005)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(PROJECT_ROOT, MANIFEST_V2_PATH), encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def test_01_six_binding_classes_present_and_no_secrets(self):
        m = self.manifest
        self.assertEqual(m["manifest_version"], "sunroof-release-manifest-v2")
        # 六类绑定: 代码基线 / 配置 sha256 / schema 版本 / 冻结合同清单 / 冒烟门 / CI 入口
        self.assertIn("baseline_commit", m["code_revision"])
        self.assertTrue(m["code_revision"]["baseline_commit"])
        self.assertTrue(m["config_sha256"])
        self.assertEqual(m["schema_version"], "005")
        self.assertEqual(len(m["frozen_contracts"]), 5)
        self.assertTrue(m["smoke_gate"])
        self.assertTrue(m["ci_entrypoint"])
        # 零秘密: 凭据字面量/静态令牌头绝不出现在清单任何字段
        with open(os.path.join(PROJECT_ROOT, MANIFEST_V2_PATH), encoding="utf-8") as f:
            raw = f.read()
        for token in redaction_scan.SECRET_LITERALS + redaction_scan.SECRET_MARKERS_IN_EMISSION:
            self.assertNotIn(token, raw, f"manifest v2 含秘密标记: {token[:6]}***")

    def test_02_config_sha256_matches_file(self):
        actual = sha256_file(os.path.join(PROJECT_ROOT, CONFIG_PATH))
        self.assertEqual(self.manifest["config_sha256"], actual)
        # 与 v1 口径一致: 配置自节点 05 冻结以来零漂移
        with open(os.path.join(PROJECT_ROOT, MANIFEST_V1_PATH), encoding="utf-8") as f:
            v1 = json.load(f)
        self.assertEqual(v1["config_sha256"], actual)

    def test_03_frozen_contracts_sha256_match_files(self):
        for contract in self.manifest["frozen_contracts"]:
            path = os.path.join(PROJECT_ROOT, contract["path"])
            self.assertTrue(os.path.exists(path), contract["path"])
            self.assertEqual(
                sha256_file(path), contract["sha256"],
                f"冻结合同 sha256 漂移: {contract['path']}")

    def test_04_schema_version_005_and_d3_references(self):
        self.assertEqual(self.manifest["schema_version"], "005")
        self.assertIn("audit_chain_head", self.manifest["schema_note"])
        self.assertIn("revoked_tokens", self.manifest["schema_note"])
        # D3 交付引用到位: 备份/恢复工具与 runbook v2
        self.assertEqual(self.manifest["deployment_entrypoint"], "deploy/runbook-v2.md")
        self.assertIn("backup_snapshot.py", self.manifest["backup_tool"])
        self.assertIn("restore_snapshot.py", self.manifest["restore_tool"])
class DockerfileComposeTests(unittest.TestCase):
    """D3-a: 非 root 生产化镜像 + compose 生产形态 (静态产物断言, 本机零 Docker 依赖)"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(PROJECT_ROOT, DOCKERFILE_PATH), encoding="utf-8") as f:
            cls.dockerfile_lines = f.read().splitlines()
        with open(os.path.join(PROJECT_ROOT, COMPOSE_PATH), encoding="utf-8") as f:
            cls.compose_text = f.read()

    def test_05_dockerfile_non_root_with_quality_gate(self):
        lines = self.dockerfile_lines
        self.assertTrue(any(l.startswith("FROM python:3.11-slim") for l in lines))
        # 非 root 化: 专用用户 uid/gid 10001 + USER 切换
        self.assertTrue(any("--uid 10001" in l and "useradd" in l for l in lines),
                        "缺少 useradd uid 10001 (非 root 化缺失)")
        user_idx = [i for i, l in enumerate(lines) if l.startswith("USER ")]
        self.assertTrue(user_idx, "缺少 USER 指令 (仍以 root 运行)")
        last_user = lines[user_idx[-1]].split()[1]
        self.assertNotEqual(last_user.lower(), "root", "最后一个 USER 仍是 root")
        # 构建期全量测试门保留, 且位于 USER 切换之后 (以非 root 身份执行自证权限)
        self.assertTrue(any("HEALTHCHECK" in l for l in lines), "HEALTHCHECK 必须保留")
        gate_idx = next(i for i, l in enumerate(lines) if "unittest discover -s tests" in l)
        self.assertGreater(gate_idx, user_idx[-1],
                           "构建期测试门必须在 USER 切换之后 (非 root 自证)")

    def test_06_compose_production_shape(self):
        text = self.compose_text
        self.assertIn("SUNROOF_TOKEN_SECRET: ${SUNROOF_TOKEN_SECRET:-}", text)
        self.assertIn("SUNROOF_ADMIN_TOKEN: ${SUNROOF_ADMIN_TOKEN:-", text)
        self.assertIn("sunroof-data:/app/data", text)
        self.assertIn("restart: unless-stopped", text)
        self.assertIn("healthcheck:", text)
        self.assertIn("8018:8018", text)


class BackupRestoreD3Tests(unittest.TestCase):
    """D3-c: 备份/恢复 (数据卷 + 审计链快照一致性) —— 子进程走真实 CLI 退出码"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-d3-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_d3.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)
        self.client = TestClient(app)
        self._traffic()  # 造真实审计哈希链流量

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    def _traffic(self):
        """覆盖全分支的审计流量 (denied 越权也入链)。"""
        for task, action, corr, extra in [
            ("TASK-N04-001", "check_spec", "corr-d3-c01", {}),
            ("TASK-N04-001", "confirm", "corr-d3-c02", {}),
            ("TASK-N04-001", "supervisor_resolve", "corr-d3-c03", {}),
            ("TASK-N04-002", "check_spec", "corr-d3-c04", {}),
            ("TASK-N04-002", "escalate", "corr-d3-c05",
             {"idempotency_key": "idem-d3-01"}),
        ]:
            payload = {"task_id": task, "action": action,
                       "correlation_id": corr, **extra}
            self.client.post("/api/v1/tasks/action", json=payload,
                             headers=PQE_A_TOKEN)
        self.assertTrue(database.verify_audit_chain()["ok"])

    def _run_cli(self, *cli_args):
        return subprocess.run(
            [sys.executable, *cli_args], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=120,
        )

    def _backup(self, label="nightly"):
        out_dir = os.path.join(self._tmpdir.name, "backups")
        proc = self._run_cli("deploy/backup_snapshot.py", "--db", self.db_path,
                             "--out-dir", out_dir, "--label", label)
        return proc, out_dir

    def test_07_backup_snapshot_chain_verified_and_metadata(self):
        proc, out_dir = self._backup()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertTrue(report["audit_chain"]["ok"])  # 哈希链快照一致性门
        self.assertEqual(report["integrity_check"], "ok")
        self.assertTrue(report["db_sha256"])
        backup_path = report["backup_file"]
        self.assertTrue(os.path.exists(backup_path))
        self.assertTrue(os.path.exists(backup_path + ".meta.json"))
        self.assertEqual(report["db_sha256"], sha256_file(backup_path))

    def test_08_verify_only_detects_tampered_backup(self):
        proc, out_dir = self._backup("tamper")
        self.assertEqual(proc.returncode, 0)
        backup_path = json.loads(proc.stdout)["backup_file"]
        # 攻击者直改快照里的审计业务字段
        conn = database.get_db_connection()
        conn.close()  # 先关本进程连接, 避免快照被本进程 WAL 侧车干扰
        import sqlite3
        conn = sqlite3.connect(backup_path)
        try:
            victim = conn.execute(
                "SELECT run_id FROM task_action_runs ORDER BY rowid ASC").fetchone()[0]
            conn.execute("UPDATE task_action_runs SET action='refused' WHERE run_id=?",
                         (victim,))
            conn.commit()
        finally:
            conn.close()
        check = self._run_cli("deploy/backup_snapshot.py", "--verify-only",
                              "--backup-file", backup_path)
        self.assertEqual(check.returncode, 1, check.stdout)
        report = json.loads(check.stdout)
        self.assertFalse(report["ok"])
        self.assertEqual(report["audit_chain"]["first_bad"]["reason"],
                         "entry_hash_mismatch")

    def test_09_restore_rejects_tampered_backup_without_touching_target(self):
        proc, _ = self._backup("bad")
        backup_path = json.loads(proc.stdout)["backup_file"]
        import sqlite3
        conn = sqlite3.connect(backup_path)
        try:
            victim = conn.execute(
                "SELECT run_id FROM task_action_runs ORDER BY rowid ASC").fetchone()[0]
            conn.execute("UPDATE task_action_runs SET action='refused' WHERE run_id=?",
                         (victim,))
            conn.commit()
        finally:
            conn.close()
        target = os.path.join(self._tmpdir.name, "target.db")
        database.init_db(target)
        target_sha_before = sha256_file(target)
        restore = self._run_cli("deploy/restore_snapshot.py",
                                "--backup-file", backup_path, "--db", target)
        self.assertEqual(restore.returncode, 1, restore.stdout)
        self.assertIn("restore_rejected_precheck_failed", restore.stdout)
        self.assertEqual(sha256_file(target), target_sha_before)  # 目标库零触碰

    def test_10_restore_success_path_and_chain_ok(self):
        proc, _ = self._backup("good")
        report = json.loads(proc.stdout)
        backup_path = report["backup_file"]
        target = os.path.join(self._tmpdir.name, "target.db")
        database.init_db(target)  # 已存在的目标库 -> 触发安全快照
        restore = self._run_cli("deploy/restore_snapshot.py",
                                "--backup-file", backup_path, "--db", target)
        self.assertEqual(restore.returncode, 0, restore.stdout)
        summary = json.loads(restore.stdout)
        self.assertTrue(summary["ok"])
        self.assertTrue(summary["audit_chain"]["ok"])
        self.assertEqual(summary["integrity_check"], "ok")
        # 原子替换逐字节一致 + 恢复后内容与源库一致
        self.assertEqual(sha256_file(target), report["db_sha256"])
        self.assertTrue(summary["pre_restore_safety_snapshot"])
        self.assertTrue(os.path.exists(summary["pre_restore_safety_snapshot"]))
        import sqlite3
        conn = sqlite3.connect(target)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM task_action_runs").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(rows, 5)


class RedactionScopeD3Tests(unittest.TestCase):
    """批次 3 运行时代码扫描面更新: 备份/恢复工具入 SCOPE_FILES 且当前全干净"""

    def test_11_redaction_scope_includes_d3_tools(self):
        rel_paths = [p.replace("\\", "/") for p in redaction_scan.SCOPE_FILES]
        self.assertIn("deploy/backup_snapshot.py", rel_paths)
        self.assertIn("deploy/restore_snapshot.py", rel_paths)
        report = redaction_scan.scan()
        self.assertTrue(report["passed"], report["violations"])
        self.assertEqual(report["scanned_files"], 52)  # 50 (D2) + 2 (D3-c)


if __name__ == "__main__":
    unittest.main()
"""
回滚演练运行器 (Rollback Drill Runner) —— 节点 05 (回链 specs/mission-week-08.md 第 4 天)

三段闭环 (全部对真实 uvicorn 实例执行, 绝不用 TestClient 替代):
  R0 基线:  冷部署独立实例 (全新库) -> 应用 known-good 配置 -> 冒烟 4/4
  R1 注入:  全新冷部署 + 应用 rc1-bad 错误配置 (偏差硬限 0.01%) -> 冒烟必须拦截
            (归因干净: 唯一失败项为 SMOKE-01 —— answered -> needs_review 漂移;
            退出码 1 = 部署自动阻断, 放量门生效)
  R2 回滚:  全新冷部署 + 重放 known-good 配置 (配置收敛式回滚, 零 DDL 零删库)
            -> 冒烟 4/4 复验

说明: 任务夹具为一次性状态机终态 (确认/升级后不可重放), 因此每阶段都用全新
初始化的数据版本 —— 这正是「回滚 = 退回已知正确版本 (代码 tag + 配置收敛 +
数据版本重置)」的部署语义; 数据库兼容性由三库表集合逐阶段断言 (schema 004)。

证据: evidence/rollback-drill-proof.json (真实运行产物, 每次运行覆盖更新)。
诚实边界: 回滚对象是配置数据而非代码; 本演练在单机沙盒运行, 不宣称生产就绪。
用法 (在本目录下):
    python deploy/run_rollback_drill.py
退出码: 0=三段闭环成立; 1=演练失败 (任一段不符合预期)。
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_CONFIG = os.path.join("config", "sunroof-config-w08.json")
BAD_CONFIG = os.path.join("config", "sunroof-config-w08-rc1-bad.json")
EVIDENCE_PATH = os.path.join(PROJECT_ROOT, "evidence", "rollback-drill-proof.json")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(base_url, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _run(cmd, timeout=120):
    """运行子进程 (统一 UTF-8 解码, Windows 控制台安全), 返回 (exit_code, 输出)。"""
    env = {**os.environ.copy(), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _table_names(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def main():
    phases = []
    servers = []  # (server, log_handle, db_path, base_url, workdir)

    def _cold_deploy(tag):
        """全新冷部署: 新临时目录 + 新库 + 独立 uvicorn 实例, 返回环境句柄。"""
        workdir = tempfile.mkdtemp(prefix=f"sunroof-drill-{tag}-")
        db_path = os.path.join(workdir, "sunroof_drill.db")
        env = {**os.environ.copy(), "PYTHONIOENCODING": "utf-8", "FDE_DB_PATH": db_path}
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        log = open(os.path.join(workdir, "uvicorn.log"), "wb")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "application.main:app", "--port", str(port)],
            cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        servers.append((server, log, db_path, base_url, workdir))
        if not _wait_health(base_url):
            raise RuntimeError(f"uvicorn did not become healthy within 30s ({base_url})")
        return db_path, base_url

    try:
        def _phase(name, tag, config_rel, expect_pass):
            db_path, base_url = _cold_deploy(tag)
            code, out = _run([sys.executable, "deploy/apply_policy_config.py",
                              "--config", config_rel, "--db", db_path])
            smoke_code, smoke_out = _run([sys.executable, "deploy/smoke_w08.py",
                                          "--base-url", base_url, "--db", db_path])
            report = json.loads(smoke_out) if smoke_out.strip().startswith("{") else {}
            entry = {
                "phase": name,
                "config": os.path.basename(config_rel),
                "db_path": db_path,
                "base_url": base_url,
                "converger_exit": code,
                "converged": code == 0,
                "smoke_exit": smoke_code,
                "smokes_passed": report.get("passed_count"),
                "smoke_all_passed": report.get("all_passed"),
                "failed_smokes": [r["id"] for r in report.get("results", []) if not r["passed"]],
                "expectation_met": (smoke_code == 0) == expect_pass,
            }
            phases.append(entry)
            print(f"[{'PASS' if entry['expectation_met'] else 'FAIL'}] {name}: "
                  f"converger={code} smoke_exit={smoke_code} "
                  f"smokes={entry['smokes_passed']}/4 "
                  f"failed={entry['failed_smokes']}", flush=True)
            return entry

        _phase("R0-known-good-baseline", "r0", GOOD_CONFIG, expect_pass=True)
        _phase("R1-bad-config-injected", "r1", BAD_CONFIG, expect_pass=False)
        _phase("R2-convergent-rollback", "r2", GOOD_CONFIG, expect_pass=True)

        # 数据库兼容性: 三阶段独立库的表集合逐阶段一致 (schema 004, 零 DDL 漂移)
        table_sets = [_table_names(item["db_path"]) for item in phases]
        tables_stable = table_sets[0] == table_sets[1] == table_sets[2]
        drill_passed = (
            len(phases) == 3
            and all(item["expectation_met"] and item["converged"] for item in phases)
            and phases[0]["smoke_all_passed"] is True                  # 基线 4/4
            and phases[1]["smoke_exit"] == 1                           # 错误配置被冒烟拦截
            and phases[1]["failed_smokes"] == ["SMOKE-01"]             # 归因: 漂移检测器
            and phases[2]["smoke_all_passed"] is True                  # 回滚后 4/4 复验
            and tables_stable
        )
        evidence = {
            "drill_version": "sunroof-rollback-drill-v1",
            "release_id": "sunroof-app-w08-r0",
            "config_known_good": "sunroof-config-w08-v1",
            "config_injected_bad": "sunroof-config-w08-rc1-bad",
            "tables_stable": tables_stable,
            "table_set": sorted(table_sets[0]) if table_sets else [],
            "phases": phases,
            "drill_passed": drill_passed,
        }
    finally:
        for server, log, *_ in servers:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()

    os.makedirs(os.path.dirname(EVIDENCE_PATH), exist_ok=True)
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"drill_passed": drill_passed, "evidence": EVIDENCE_PATH},
                     ensure_ascii=False))
    sys.exit(0 if drill_passed else 1)


if __name__ == "__main__":
    main()

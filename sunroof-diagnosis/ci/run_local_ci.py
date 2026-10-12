"""
本地 CI 流水线运行器 (Local CI Runner) —— 节点 05 (回链 specs/mission-week-08.md 第 2 天)

与 .github/workflows/ci.yml 共用同一入口, 四阶段:
  STAGE-1 Schema 检查: 临时库幂等迁移 (001-004) + PRAGMA 完整性 + 必需表存在性
  STAGE-2 全量测试:   unittest discover (垂直薄片/摄取幂等/任务闭环/部署发布)
  STAGE-3 脱敏扫描:   ci/redaction_scan.py (凭据/规格正文不入运行时输出)
  STAGE-4 干净环境任务冒烟: git archive HEAD -> 全新目录 -> uvicorn 独立实例
                        -> 应用发布配置 -> deploy/smoke_w08.py 4 条任务冒烟 (三向对账)

用法 (在本目录下):
    python ci/run_local_ci.py [--evidence-file evidence/ci-run-w08.log]
退出码: 0=全部阶段 PASS; 1=任一阶段 FAIL。证据日志默认追加写入 evidence/ 目录。
"""
import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

REQUIRED_TABLES = {
    "specs", "task_runs", "spec_check_tasks", "task_action_runs", "policy_config",
}
SUBPROCESS_ENV = {
    **os.environ.copy(),
    "PYTHONIOENCODING": "utf-8",
    "FDE_ENV": "isolated-ci-env-01",
    "FDE_CONFIG_VERSION": "sunroof-config-w08-v1",
}


def _run(cmd, cwd, timeout=300):
    """运行子进程并收集 (exit_code, 输出文本) —— 统一 UTF-8 解码, Windows 控制台安全。"""
    proc = subprocess.run(
        cmd, cwd=cwd, env=SUBPROCESS_ENV, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# STAGE-1: Schema 检查 (幂等迁移 001-004 + 完整性 + 必需表)
# ---------------------------------------------------------------------------
def stage_1_schema_check():
    from application.database import MIGRATION_SCRIPTS, init_db

    with tempfile.TemporaryDirectory(prefix="sunroof-ci-schema-") as tmp:
        db_path = os.path.join(tmp, "schema_check.db")
        init_db(db_path)  # 幂等迁移: 001 + 002 + 003 + 004
        conn = sqlite3.connect(db_path)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            seeds = conn.execute("SELECT COUNT(*) FROM policy_config").fetchone()[0]
        finally:
            conn.close()
    missing = REQUIRED_TABLES - tables
    ingestion_tables = [name for name in tables if name.startswith("ingestion")]
    passed = integrity == "ok" and not missing and len(ingestion_tables) >= 1 and seeds >= 2
    detail = (
        f"integrity={integrity}; migrations={len(MIGRATION_SCRIPTS)}; "
        f"required_tables_missing={sorted(missing)}; ingestion_tables={len(ingestion_tables)}; "
        f"policy_config_seed_rows={seeds}"
    )
    return passed, detail


# ---------------------------------------------------------------------------
# STAGE-2: 全量测试 (薄片 + 摄取幂等 + 任务闭环 + 部署发布)
# ---------------------------------------------------------------------------
def stage_2_unittest_full():
    code, output = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=PROJECT_ROOT, timeout=600,
    )
    match = re.search(r"Ran (\d+) tests? in ([\d.]+)s", output)
    total = match.group(1) if match else "?"
    duration = match.group(2) if match else "?"
    passed = code == 0 and "OK" in output
    tail = "\n".join(output.strip().splitlines()[-3:])
    return passed, f"Ran {total} tests in {duration}s, exit={code}\n{tail}"


# ---------------------------------------------------------------------------
# STAGE-3: 日志脱敏静态扫描 (ci/redaction_scan.py)
# ---------------------------------------------------------------------------
def stage_3_redaction_scan():
    code, output = _run(
        [sys.executable, os.path.join("ci", "redaction_scan.py")],
        cwd=PROJECT_ROOT, timeout=120,
    )
    try:
        report = json.loads(output)
        detail = (
            f"scanned_files={report['scanned_files']}; "
            f"emissions_checked={report['emissions_checked']}; "
            f"violations={len(report['violations'])}"
        )
        if report.get("violations"):
            detail += "\n" + json.dumps(report["violations"], ensure_ascii=False)[:400]
        return code == 0 and report.get("passed", False), detail
    except json.JSONDecodeError:
        return False, f"redaction scan produced non-JSON output (exit={code})\n{output[-300:]}"

# ---------------------------------------------------------------------------
# STAGE-4: 干净环境冷部署 + 任务级冒烟 (git archive -> 独立实例 -> 配置收敛 -> 4/4)
# ---------------------------------------------------------------------------
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


def _git(args, cwd, timeout=30):
    """运行 git 子进程并返回 (exit_code, 去空白输出文本)。"""
    proc = subprocess.run(
        ["git"] + args, cwd=cwd, env=SUBPROCESS_ENV, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def _pick_archive_source():
    """布局自适配: 选一个 HEAD 中真实跟踪 PROJECT_ROOT 内容的 git 仓库。

    背景: 本地工作区可能存在「外层仓库包含 sunroof-diagnosis/ 子目录」(GitHub
    checkout 同构) 或「PROJECT_ROOT 自身是仓库根」(独立 clone) 两种布局; 个别
    机器上还会遇到指向历史遗迹的嵌套 .git (其 HEAD 根本不含项目文件)。以
    ci/run_local_ci.py 在 HEAD 中是否存在为探针, 逐候选验证, 拒绝猜。
    返回 (repo_root, pathspec, prefix); prefix 为 "" 表示归档无顶层前缀。
    """
    project_dir = os.path.basename(os.path.normpath(PROJECT_ROOT))
    candidates = []  # (repo_root, pathspec)
    rc, out = _git(["rev-parse", "--show-toplevel"], PROJECT_ROOT)
    if rc == 0 and out:
        candidates.append((out, "."))
    rc, out = _git(
        ["rev-parse", "--show-toplevel"], os.path.dirname(os.path.normpath(PROJECT_ROOT))
    )
    if rc == 0 and out:
        candidates.append((out, project_dir))
    for repo_root, pathspec in candidates:
        probe = "ci/run_local_ci.py" if pathspec == "." else f"{pathspec}/ci/run_local_ci.py"
        rc2, out2 = _git(["ls-tree", "HEAD", "--", probe], repo_root)
        if rc2 == 0 and out2:
            return repo_root, pathspec, ("" if pathspec == "." else pathspec)
    return None, None, None


def stage_4_clean_env_smoke():
    workdir = tempfile.mkdtemp(prefix="sunroof-ci-cold-")
    bundle = os.path.join(workdir, "head.zip")

    # 1) 从 git HEAD 打包代码基线 (不含未跟踪文件/本地数据, 等价干净检出);
    #    布局自适配见 _pick_archive_source (嵌套 relic .git / 独立 clone / 子目录)
    repo_root, pathspec, prefix = _pick_archive_source()
    if repo_root is None:
        return False, (
            "git archive failed: 未找到 HEAD 中跟踪本项目内容的 git 仓库 "
            "(候选: PROJECT_ROOT 自身仓库根 / 外层仓库子目录)"
        )
    proc = subprocess.run(
        ["git", "archive", "--format=zip", "HEAD", pathspec],
        cwd=repo_root, env=SUBPROCESS_ENV, timeout=60,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if proc.returncode != 0:
        return False, f"git archive failed: {proc.stdout.decode('utf-8', 'replace')[-200:]}"
    with open(bundle, "wb") as f:
        f.write(proc.stdout)

    app_dir = os.path.join(workdir, "app")
    os.makedirs(app_dir, exist_ok=True)
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(app_dir)
    if prefix:
        app_dir = os.path.join(app_dir, prefix)

    # 2) 全新数据库 + 显式环境变量隔离 (绝不继承作者机上的任何业务环境)
    db_path = os.path.join(workdir, "sunroof_clean_env.db")
    env = {**SUBPROCESS_ENV, "FDE_DB_PATH": db_path}
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    uvicorn_log = open(os.path.join(workdir, "uvicorn.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "application.main:app", "--port", str(port)],
        cwd=app_dir, env=env, stdout=uvicorn_log, stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health(base_url):
            return False, f"uvicorn did not become healthy within 30s ({base_url})"

        # 3) 应用发布配置 (幂等收敛器) —— /health 200 不代表业务正确, 配置必须先收敛
        code, out = _run([sys.executable, "deploy/apply_policy_config.py",
                          "--config", "config/sunroof-config-w08.json", "--db", db_path],
                         cwd=app_dir, timeout=60)
        if code != 0:
            return False, f"apply_policy_config exit={code}\n{out[-300:]}"

        # 4) 任务级冒烟 4 条 (三向对账), 冒烟失败 -> 阶段失败 (部署自动阻断)
        code, out = _run([sys.executable, "deploy/smoke_w08.py",
                          "--base-url", base_url, "--db", db_path],
                         cwd=app_dir, timeout=120)
        if code != 0:
            return False, f"smoke exit={code}\n{out[-800:]}"
        report = json.loads(out)
        detail = (
            f"clean_dir={os.path.basename(workdir)}; port={port}; "
            f"smokes_passed={report['passed_count']}/4"
        )
        return report.get("all_passed", False), detail
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        uvicorn_log.close()


STAGES = [
    ("STAGE-1-schema", stage_1_schema_check),
    ("STAGE-2-unittest", stage_2_unittest_full),
    ("STAGE-3-redaction", stage_3_redaction_scan),
    ("STAGE-4-clean-env-smoke", stage_4_clean_env_smoke),
]


def main():
    parser = argparse.ArgumentParser(description="天窗诊断 节点 05 本地 CI 流水线 (4 阶段)")
    parser.add_argument("--evidence-file",
                        default=os.path.join("evidence", "ci-run-w08.log"))
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    lines = [
        f"CI RUN {started.isoformat(timespec='seconds')} (UTC) | host=local | "
        f"python={sys.version.split()[0]} | cwd={PROJECT_ROOT}",
    ]
    all_passed = True
    for name, fn in STAGES:
        try:
            passed, detail = fn()
        except Exception as exc:  # 阶段异常视为失败, 不中断后续阶段
            passed, detail = False, f"stage raised: {type(exc).__name__}: {exc}"
        all_passed = all_passed and passed
        line = f"[{'PASS' if passed else 'FAIL'}] {name} :: {detail}"
        lines.append(line)
        print(line, flush=True)

    finished = datetime.now(timezone.utc)
    lines.append(
        f"CI RESULT {'OK' if all_passed else 'FAILED'} | "
        f"duration={(finished - started).total_seconds():.1f}s | {finished.isoformat(timespec='seconds')} (UTC)"
    )
    lines.append("")
    print(lines[-1], flush=True)

    evidence_path = os.path.join(PROJECT_ROOT, args.evidence_file)
    os.makedirs(os.path.dirname(evidence_path), exist_ok=True)
    with open(evidence_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()


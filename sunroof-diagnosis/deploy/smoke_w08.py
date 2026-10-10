"""
任务级冒烟测试 (Task-Level Smoke) —— 节点 05 (回链 specs/mission-week-08.md 第 3 天)

设计原则 (对应验收标准「冒烟测试看任务终态」):
1. 绝不只看 /health 或 HTTP 200: 每条冒烟同时断言
   [一线用户可见响应] + [spec_check_tasks 数据库终态] + [task_action_runs 审计]
   (+ check_spec 动作另有节点 02 task_runs 审计);
2. 三向对账: 全部断言经同一 correlation_id (corr-smoke-01..04) 关联;
3. SMOKE-03/04 复验节点 04 能力不退化: PQE 越权 100% 阻断零泄漏, 同幂等键
   重放 ALREADY_APPLIED 不产生重复生效;
4. 前置条件: 目标实例必须是全新初始化的数据库 (任务夹具为 initialized 初始态)
   且已应用 known-good 发布配置 (部署后运行一次; 放量以冒烟 4/4 为准);
5. 纯标准库实现 (urllib + sqlite3), 第三方仅凭 Python 3.10+ 即可执行。

用法 (在本目录下):
    python deploy/smoke_w08.py --base-url http://127.0.0.1:8020 --db <db_path>
退出码: 0=4/4 PASS; 1=存在失败 (冒烟自动阻断); 2=连接失败。
"""
import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request

PQE_TOKEN = "token_pqe_oem_a"  # OEM-A 一线 PQE (教学模拟会话)

# 零泄漏断言用的敏感片段 (规格 ID/规格标题正文/凭据) —— denied 响应全文不得出现
LEAK_PATTERNS = ("SPEC-SR-A-V5", "检验标准", "token_qa_supervisor_secret")


def _request(base_url, path, payload=None, token=None):
    """发起 HTTP 请求, 返回 (status_code, body_dict)。HTTPError 也返回结构化结果供断言。"""
    url = base_url.rstrip("/") + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(
        url, data=data, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 401/422/404 等非 2xx 也作为可断言结果
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _db_query(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _spec_task(db_path, task_id):
    rows = _db_query(db_path, "SELECT * FROM spec_check_tasks WHERE task_id = ?", (task_id,))
    return rows[0] if rows else None


def _action_runs(db_path, correlation_id):
    return _db_query(
        db_path,
        "SELECT * FROM task_action_runs WHERE correlation_id = ? ORDER BY rowid ASC",
        (correlation_id,),
    )


def _task_run(db_path, request_id):
    rows = _db_query(db_path, "SELECT * FROM task_runs WHERE request_id = ?", (request_id,))
    return rows[0] if rows else None


def _check(name, passed, detail):
    return {"check": name, "passed": bool(passed), "detail": detail}


def _finish(smoke_id, name, checks):
    passed = all(item["passed"] for item in checks)
    return {
        "id": smoke_id,
        "name": name,
        "passed": passed,
        "three_way_consistent": passed,
        "checks": checks,
    }

# ---------------------------------------------------------------------------
# SMOKE-01: 常规任务 (TASK-N04-001, +1.05% 偏差) 核对并确认成功
# ---------------------------------------------------------------------------
def smoke_01(base_url, db_path):
    corr = "corr-smoke-01"
    checks = []

    status, check_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-N04-001", "action": "check_spec", "correlation_id": corr},
        token=PQE_TOKEN,
    )
    checks.append(_check(
        "check_spec 响应 answered + 命中 SPEC-SR-A-V5 + 偏差 1.05%",
        status == 200 and check_body.get("status") == "answered"
        and (check_body.get("candidate_spec") or {}).get("spec_id") == "SPEC-SR-A-V5"
        and (check_body.get("evaluation") or {}).get("deviation_pct") == 1.05,
        f"HTTP {status}, {check_body.get('status')}/{(check_body.get('candidate_spec') or {}).get('spec_id')}"))

    task = _spec_task(db_path, "TASK-N04-001") or {}
    checks.append(_check(
        "spec_check_tasks 终态 answered + matched_spec_id 回填",
        task.get("status") == "answered" and task.get("matched_spec_id") == "SPEC-SR-A-V5",
        f"db={task.get('status')}/{task.get('matched_spec_id')}"))

    engine_run = _task_run(db_path, corr) or {}
    checks.append(_check(
        "节点 02 task_runs 引擎审计 answered (血缘不断链)",
        engine_run.get("status") == "answered"
        and engine_run.get("matched_spec_id") == "SPEC-SR-A-V5",
        f"task_runs={engine_run.get('status')}/{engine_run.get('matched_spec_id')}"))

    status, confirm_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-N04-001", "action": "confirm", "correlation_id": corr},
        token=PQE_TOKEN,
    )
    runs = _action_runs(db_path, corr)
    checks.append(_check(
        "confirm -> confirmed; 动作审计 2 条 (check_spec/confirm) 同 correlation_id",
        status == 200 and confirm_body.get("status") == "confirmed"
        and [r["action"] for r in runs] == ["check_spec", "confirm"]
        and [r["status"] for r in runs] == ["answered", "confirmed"],
        f"HTTP {status}, {confirm_body.get('status')}, runs={[(r['action'], r['status']) for r in runs]}"))
    return _finish("SMOKE-01", "常规任务核对并确认成功 (三向对账)", checks)


# ---------------------------------------------------------------------------
# SMOKE-02: 超硬限任务 (TASK-N04-002, +21.05%) 阻断 + 升级主管
# ---------------------------------------------------------------------------
def smoke_02(base_url, db_path):
    corr = "corr-smoke-02"
    checks = []

    status, check_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-N04-002", "action": "check_spec", "correlation_id": corr},
        token=PQE_TOKEN,
    )
    checks.append(_check(
        "check_spec 响应 needs_review / SPEC_HARD_LIMIT_EXCEEDED + 候选零内容",
        status == 200 and check_body.get("status") == "needs_review"
        and check_body.get("reason_code") == "SPEC_HARD_LIMIT_EXCEEDED"
        and check_body.get("candidate_spec") is None,
        f"HTTP {status}, {check_body.get('status')}/{check_body.get('reason_code')}"))

    task = _spec_task(db_path, "TASK-N04-002") or {}
    checks.append(_check(
        "spec_check_tasks 终态 needs_review + matched_spec_id 恒 NULL (零隐性依据)",
        task.get("status") == "needs_review" and task.get("matched_spec_id") is None,
        f"db={task.get('status')}/{task.get('matched_spec_id')}"))

    status, esc_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-N04-002", "action": "escalate", "correlation_id": corr,
         "idempotency_key": "idem-smoke-02-escalate"},
        token=PQE_TOKEN,
    )
    runs = _action_runs(db_path, corr)
    checks.append(_check(
        "escalate -> escalated; 动作审计 2 条 (check_spec/escalate)",
        status == 200 and esc_body.get("status") == "escalated"
        and [r["action"] for r in runs] == ["check_spec", "escalate"],
        f"HTTP {status}, {esc_body.get('status')}, runs={[(r['action'], r['status']) for r in runs]}"))
    return _finish("SMOKE-02", "超硬限任务阻断并升级主管 (零规格内容)", checks)

# ---------------------------------------------------------------------------
# SMOKE-03: PQE 直调主管结案 -> 服务端一票否决 denied + 零泄漏 (节点 04 能力不退化)
# ---------------------------------------------------------------------------
def smoke_03(base_url, db_path):
    corr = "corr-smoke-03"
    checks = []

    # 原始响应文本用于零泄漏全文断言 (拒绝也要防信息外泄)
    url = base_url.rstrip("/") + "/api/v1/tasks/action"
    data = json.dumps({
        "task_id": "TASK-N04-002", "action": "supervisor_resolve",
        "correlation_id": corr,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "X-Auth-Token": PQE_TOKEN,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw_text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw_text = exc.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw_text)
    except json.JSONDecodeError:
        body = {}
    checks.append(_check(
        "PQE 直调 supervisor_resolve -> denied / ACTION_NOT_ALLOWED",
        status == 200 and body.get("status") == "denied"
        and body.get("reason_code") == "ACTION_NOT_ALLOWED",
        f"HTTP {status}, {body.get('status')}/{body.get('reason_code')}"))
    checks.append(_check(
        "denied 响应全文零泄漏 (无规格 ID/标题正文/主管凭据)",
        all(pattern not in raw_text for pattern in LEAK_PATTERNS),
        "leak_patterns=" + ",".join(LEAK_PATTERNS)))

    task = _spec_task(db_path, "TASK-N04-002") or {}
    runs = _action_runs(db_path, corr)
    checks.append(_check(
        "越权不改变任务状态 (仍 escalated); 阻断事件已审计 (denied/pqe)",
        task.get("status") == "escalated" and len(runs) == 1
        and runs[0]["status"] == "denied" and runs[0]["user_role"] == "pqe",
        f"db={task.get('status')}, runs={[(r['action'], r['status'], r['user_role']) for r in runs]}"))
    return _finish("SMOKE-03", "PQE 越权调用被服务端强拦截 (零泄漏)", checks)


# ---------------------------------------------------------------------------
# SMOKE-04: 相同幂等键重复提交升级 -> ALREADY_APPLIED, 不产生重复生效 (不退化)
# ---------------------------------------------------------------------------
def smoke_04(base_url, db_path):
    corr = "corr-smoke-04"
    idem_key = "idem-smoke-02-escalate"  # 与 SMOKE-02 相同的幂等键 (重放)
    checks = []

    status, body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-N04-002", "action": "escalate", "correlation_id": corr,
         "idempotency_key": idem_key},
        token=PQE_TOKEN,
    )
    checks.append(_check(
        "重放响应 escalated + is_idempotent + ALREADY_APPLIED",
        status == 200 and body.get("status") == "escalated"
        and body.get("is_idempotent") is True
        and body.get("reason_code") == "ALREADY_APPLIED",
        f"HTTP {status}, {body.get('status')}/{body.get('reason_code')}/idem={body.get('is_idempotent')}"))

    task = _spec_task(db_path, "TASK-N04-002") or {}
    checks.append(_check(
        "spec_check_tasks 保持首条 escalated, 幂等键唯一",
        task.get("status") == "escalated" and task.get("idempotency_key") == idem_key,
        f"db={task.get('status')}/{task.get('idempotency_key')}"))

    first_runs = _action_runs(db_path, "corr-smoke-02")
    replay_runs = _action_runs(db_path, corr)
    checks.append(_check(
        "无重复生效: 首次升级审计仍 2 条, 重放仅新增 ALREADY_APPLIED 审计 1 条",
        len(first_runs) == 2 and len(replay_runs) == 1
        and replay_runs[0]["reason_code"] == "ALREADY_APPLIED",
        f"first={len(first_runs)} replay={[(r['action'], r['status'], r['reason_code']) for r in replay_runs]}"))
    return _finish("SMOKE-04", "相同幂等键重复提交不产生重复生效", checks)


def run_all_smokes(base_url, db_path):
    """按序执行 4 条冒烟 (SMOKE-03/04 依赖 SMOKE-02 的升级终态), 返回汇总报告。"""
    results = [
        smoke_01(base_url, db_path),
        smoke_02(base_url, db_path),
        smoke_03(base_url, db_path),
        smoke_04(base_url, db_path),
    ]
    return {
        "base_url": base_url,
        "db_path": db_path,
        "all_passed": all(item["passed"] for item in results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="天窗诊断 节点 05 任务级冒烟 (4 条, 三向对账)")
    parser.add_argument("--base-url", required=True, help="目标实例地址, 如 http://127.0.0.1:8020")
    parser.add_argument("--db", required=True, help="目标实例的 SQLite 数据库路径 (用于终态与审计对账)")
    args = parser.parse_args()

    try:
        status, _ = _request(args.base_url, "/health")
    except (urllib.error.URLError, OSError) as exc:
        print(json.dumps({"error": f"cannot reach {args.base_url}: {exc}"}))
        sys.exit(2)
    if status != 200:
        print(json.dumps({"error": f"/health returned HTTP {status}"}))
        sys.exit(2)

    report = run_all_smokes(args.base_url, args.db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()



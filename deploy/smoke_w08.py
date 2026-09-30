"""
任务级冒烟测试 (Task-Level Smoke) —— Week 08 (回链 specs/mission-week-08.md 第 3 天)

设计原则 (对应验收标准「冒烟测试看任务终态」):
1. 绝不只看 /health 或 HTTP 200: 每条冒烟同时断言
   [一线用户可见响应] + [billing_tasks 数据库终态] + [task_action_runs 审计] (+ check 动作另有 Week 05 task_runs 审计);
2. 三向对账: 全部断言经同一 correlation_id (corr-smoke-01..04) 关联;
3. SMOKE-03/04 复验第 7 周能力不退化: 越权 100% 阻断零泄漏, 同幂等键重放 already_applied;
4. 前置条件: 目标实例必须是全新初始化的数据库 (夹具为 initialized 初始态), 部署后运行一次;
5. 纯标准库实现 (urllib + sqlite3), 第三方仅凭 Python 3.10+ 即可执行。

用法:
    python deploy/smoke_w08.py --base-url http://127.0.0.1:8000 --db <db_path>
退出码: 0=4/4 PASS; 1=存在失败 (冒烟自动阻断); 2=连接失败。
"""
import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request

AGENT_TOKEN = "token_agent_valid"
SUPERVISOR_POLICY_ID = "POL-2026-REF-09"

# 零泄漏断言用的敏感正文片段 (主管专属政策标题/公式 + 凭据)
LEAK_PATTERNS = (SUPERVISOR_POLICY_ID, "特殊退款", "special_refund", "token_supervisor_secret")


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


def _billing_task(db_path, task_id):
    rows = _db_query(db_path, "SELECT * FROM billing_tasks WHERE task_id = ?", (task_id,))
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
# SMOKE-01: 常规计费工单 (TASK-W07-001 华东/500元/2026-08-15) 核对并确认成功
# ---------------------------------------------------------------------------
def smoke_01(base_url, db_path):
    corr = "corr-smoke-01"
    checks = []

    status, check_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-001", "action": "check_policy", "correlation_id": corr},
        token=AGENT_TOKEN,
    )
    cp = check_body.get("candidate_policy") or {}
    checks.append(_check(
        "check 可见响应 answered", status == 200 and check_body.get("status") == "answered",
        f"HTTP {status}, status={check_body.get('status')}"))
    checks.append(_check(
        "匹配 POL-2026-BIL-01 v3 + SOP 来源",
        cp.get("policy_id") == "POL-2026-BIL-01" and cp.get("version") == "v3"
        and cp.get("source_id") == "KB-BILLING-2026-08",
        f"policy={cp.get('policy_id')} {cp.get('version')} src={cp.get('source_id')}"))

    status, confirm_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-001", "action": "confirm", "correlation_id": corr},
        token=AGENT_TOKEN,
    )
    checks.append(_check(
        "confirm 可见响应 confirmed", status == 200 and confirm_body.get("status") == "confirmed",
        f"HTTP {status}, status={confirm_body.get('status')}"))

    task = _billing_task(db_path, "TASK-W07-001") or {}
    checks.append(_check(
        "billing_tasks 终态 confirmed",
        task.get("status") == "confirmed" and task.get("matched_policy_id") == "POL-2026-BIL-01",
        f"db={task.get('status')}/{task.get('matched_policy_id')}"))

    runs = _action_runs(db_path, corr)
    audit_ok = (
        len(runs) == 2
        and [r["action"] for r in runs] == ["check_policy", "confirm"]
        and [r["status"] for r in runs] == ["answered", "confirmed"]
    )
    checks.append(_check(
        "task_action_runs 审计 2 条且动作有序", audit_ok,
        f"runs={[(r['action'], r['status']) for r in runs]}"))

    week05_run = _task_run(db_path, corr)
    checks.append(_check(
        "Week 05 task_runs 审计衔接 (check 步)",
        week05_run is not None and week05_run.get("status") == "answered"
        and week05_run.get("matched_policy_id") == "POL-2026-BIL-01",
        f"task_runs={week05_run and (week05_run.get('status'), week05_run.get('matched_policy_id'))}"))

    three_way = bool(runs) and confirm_body.get("status") == task.get("status") == runs[-1]["status"]
    checks.append(_check("三向一致 (响应/终态/审计)", three_way, f"corr={corr}"))
    return _finish("SMOKE-01", "常规计费工单核对并确认成功", checks)


# ---------------------------------------------------------------------------
# SMOKE-02: 高金额工单 (TASK-W07-002 华东/2500元) 触发权限门槛并升级
# ---------------------------------------------------------------------------
def smoke_02(base_url, db_path):
    corr = "corr-smoke-02"
    idem_key = "idem-smoke-02-escalate"
    checks = []

    status, check_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-002", "action": "check_policy", "correlation_id": corr},
        token=AGENT_TOKEN,
    )
    checks.append(_check(
        "check 可见响应 needs_review + 金额门槛",
        status == 200 and check_body.get("status") == "needs_review"
        and check_body.get("reason_code") == "AMOUNT_EXCEEDS_LIMIT"
        and check_body.get("candidate_policy") is None,
        f"HTTP {status}, {check_body.get('status')}/{check_body.get('reason_code')}"))

    status, esc_body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-002", "action": "escalate", "correlation_id": corr,
         "idempotency_key": idem_key},
        token=AGENT_TOKEN,
    )
    checks.append(_check(
        "escalate 可见响应 escalated", status == 200 and esc_body.get("status") == "escalated",
        f"HTTP {status}, status={esc_body.get('status')}"))

    task = _billing_task(db_path, "TASK-W07-002") or {}
    checks.append(_check(
        "billing_tasks 终态 escalated + 原因码",
        task.get("status") == "escalated" and task.get("reason_code") == "AMOUNT_EXCEEDS_LIMIT",
        f"db={task.get('status')}/{task.get('reason_code')}"))

    runs = _action_runs(db_path, corr)
    audit_ok = len(runs) == 2 and runs[-1]["action"] == "escalate" and runs[-1]["status"] == "escalated"
    checks.append(_check(
        "task_action_runs 审计 2 条 (check+escalate)", audit_ok,
        f"runs={[(r['action'], r['status']) for r in runs]}"))

    three_way = bool(runs) and esc_body.get("status") == task.get("status") == runs[-1]["status"]
    checks.append(_check("三向一致 (响应/终态/审计)", three_way, f"corr={corr}"))
    return _finish("SMOKE-02", "高金额工单触发权限门槛升级", checks)


# ---------------------------------------------------------------------------
# SMOKE-03: 普通客服越权直调主管结案接口 -> 服务端 100% 拦截 + 零泄漏
# ---------------------------------------------------------------------------
def smoke_03(base_url, db_path):
    corr = "corr-smoke-03"
    checks = []

    status, body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-002", "action": "supervisor_resolve", "correlation_id": corr},
        token=AGENT_TOKEN,
    )
    body_text = json.dumps(body, ensure_ascii=False)
    checks.append(_check(
        "越权响应 denied + ACTION_NOT_ALLOWED",
        status == 200 and body.get("status") == "denied"
        and body.get("reason_code") == "ACTION_NOT_ALLOWED",
        f"HTTP {status}, {body.get('status')}/{body.get('reason_code')}"))
    checks.append(_check(
        "零泄漏: candidate_policy 为空且无主管政策/凭据正文",
        body.get("candidate_policy") is None
        and not any(pattern in body_text for pattern in LEAK_PATTERNS),
        "响应全文未出现 POL-2026-REF-09/政策正文/token"))

    task = _billing_task(db_path, "TASK-W07-002") or {}
    checks.append(_check(
        "billing_tasks 状态未被篡改 (仍 escalated)",
        task.get("status") == "escalated",
        f"db={task.get('status')}"))

    runs = _action_runs(db_path, corr)
    audit_ok = (
        len(runs) == 1 and runs[0]["action"] == "supervisor_resolve"
        and runs[0]["status"] == "denied" and runs[0]["user_role"] == "support_agent"
    )
    checks.append(_check(
        "阻断事件已审计 (supervisor_resolve/denied)", audit_ok,
        f"runs={[(r['action'], r['status'], r['user_role']) for r in runs]}"))
    return _finish("SMOKE-03", "普通客服越权调用被服务端强拦截", checks)


# ---------------------------------------------------------------------------
# SMOKE-04: 相同幂等键重复提交升级 -> already_applied, 不产生重复生效
# ---------------------------------------------------------------------------
def smoke_04(base_url, db_path):
    corr = "corr-smoke-04"
    idem_key = "idem-smoke-02-escalate"  # 与 SMOKE-02 相同的幂等键 (重放)
    checks = []

    status, body = _request(
        base_url, "/api/v1/tasks/action",
        {"task_id": "TASK-W07-002", "action": "escalate", "correlation_id": corr,
         "idempotency_key": idem_key},
        token=AGENT_TOKEN,
    )
    checks.append(_check(
        "重放响应 escalated + is_idempotent + ALREADY_APPLIED",
        status == 200 and body.get("status") == "escalated"
        and body.get("is_idempotent") is True
        and body.get("reason_code") == "ALREADY_APPLIED",
        f"HTTP {status}, {body.get('status')}/{body.get('reason_code')}/idem={body.get('is_idempotent')}"))

    task = _billing_task(db_path, "TASK-W07-002") or {}
    checks.append(_check(
        "billing_tasks 保持首条 escalated, 幂等键唯一",
        task.get("status") == "escalated" and task.get("idempotency_key") == idem_key,
        f"db={task.get('status')}/{task.get('idempotency_key')}"))

    first_runs = _action_runs(db_path, "corr-smoke-02")
    replay_runs = _action_runs(db_path, corr)
    checks.append(_check(
        "无重复生效: 首次升级审计仍 2 条, 重放仅新增 already_applied 审计",
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
    parser = argparse.ArgumentParser(description="北辰 Week 08 任务级冒烟 (4 条, 三向对账)")
    parser.add_argument("--base-url", required=True, help="目标实例地址, 如 http://127.0.0.1:8000")
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


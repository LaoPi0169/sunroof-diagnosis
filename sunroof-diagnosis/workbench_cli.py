"""
节点 04 双角色任务工作台 CLI (阶段 A · 回链 task-state-model.md / adr-003 D4)

纯标准库实现 (urllib)，零新增依赖。安全全部留在服务端:
CLI 只是薄壳 —— 身份来自 Token、动作由服务端 RBAC+任务级 ACL 裁决、结果由服务端状态机落库。

用法:
  1. 启动 API:  uvicorn application.main:app --port 8020
  2. 启动工作台: python workbench_cli.py
  3. (可选) 指定地址: python workbench_cli.py --base-url http://127.0.0.1:8020

登录提示:
  pqe (OEM-A 一线)   -> token_pqe_oem_a   (直接回车 = 该默认身份)
  pqe (OEM-B 一线)   -> token_pqe_oem_b
  quality_supervisor -> token_qa_supervisor_secret (输入 s)
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

PQE_A_TOKEN = "token_pqe_oem_a"
PQE_B_TOKEN = "token_pqe_oem_b"
SUPERVISOR_TOKEN = "token_qa_supervisor_secret"


def _print(msg: str = "") -> None:
    print(msg)


def http_request(base_url: str, method: str, path: str, token: str, payload: dict | None = None):
    """调用 API; 401/404/422/5xx 均转为 (False, 错误信息) 显式失败，不静默猜测"""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return False, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return False, f"连接失败 ({exc.reason}): 请确认 API 已启动 ({base_url})"


def show_tasks(base_url: str, token: str) -> list:
    ok, body = http_request(base_url, "GET", "/api/v1/tasks", token)
    if not ok:
        _print(f"[!] 任务队列拉取失败: {body}")
        return []
    tasks = body.get("tasks", [])
    _print()
    _print(f"{'task_id':<15} {'case_id':<24} {'零件号':<22} {'工序':<18} {'实测值':>7} {'状态':<18} 原因码")
    _print("-" * 118)
    for t in tasks:
        reason = t.get("reason_code") or "-"
        _print(
            f"{t['task_id']:<15} {t['case_id']:<24} {t['part_no']:<22} "
            f"{t['process_step']:<18} {t['measured_value']:>8.1f} "
            f"{t['status']:<18} {reason}"
        )
    _print("-" * 118)
    return tasks


def show_action_result(result: dict) -> None:
    status = result.get("status")
    reason = result.get("reason_code")
    idem = " [幂等重放]" if result.get("is_idempotent") else ""
    _print()
    _print(f"    动作结果: {status}  原因码: {reason}{idem}")
    if result.get("message"):
        _print(f"    服务端: {result['message']}")
    spec = result.get("candidate_spec")
    if spec:
        # 规格三要素核验: 版本号 / 生效日期 / 来源 —— 决定权在 PQE，服务端只提供依据
        _print("    规格依据 (请人工核对三要素后再决定 confirm/refuse):")
        _print(f"      - spec_id    : {spec['spec_id']}")
        _print(f"      - 标题       : {spec['title']}")
        _print(f"      - 版本号     : {spec['version']}")
        _print(f"      - 生效日期   : {spec['effective_at']}")
        _print(f"      - 来源       : {spec['source_ref']}")
        _print(f"      - 公式规则   : {spec.get('unit')} 评估见 evaluation")
    preview = result.get("escalation_preview")
    if preview:
        _print(
            f"    需质量主管复核 (预置原因: {reason})。已提供升级入口: "
            f"目标角色 {preview['target_role']} / 建议动作 {preview['suggested_action']}"
        )


def act_on_task(base_url: str, token: str, task_id: str) -> None:
    _print(
        "可选动作: check_spec=核对规格  confirm=确认  refuse=拒绝  escalate=升级主管 "
        "supervisor_resolve=主管结案  q=返回"
    )
    action = input("  动作> ").strip()
    if action in ("", "q"):
        return
    correlation_id = f"cli-{uuid.uuid4().hex[:12]}"
    payload = {"task_id": task_id, "action": action, "correlation_id": correlation_id}
    if action == "escalate":
        key = input("  幂等键 (回车自动生成)> ").strip() or f"idem-{uuid.uuid4().hex[:8]}"
        payload["idempotency_key"] = key
    ok, body = http_request(base_url, "POST", "/api/v1/tasks/action", token, payload)
    if not ok:
        _print(f"    [!] 动作被拒绝: {body}")
        return
    show_action_result(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="天窗总成只读诊断 · 双角色任务工作台 CLI (节点 04)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020", help="API 服务地址")
    args = parser.parse_args()

    _print("=" * 60)
    _print("天窗总成只读诊断 · PQE/质量主管 任务工作台 (节点 04)")
    _print("身份只来自服务端 Token 校验，输入框里的角色自报一律无效。")
    _print("=" * 60)
    raw = input("Token (回车=默认 OEM-A PQE; b=OEM-B PQE; s=质量主管)> ").strip()
    if raw.lower() == "s":
        token = SUPERVISOR_TOKEN
    elif raw.lower() == "b":
        token = PQE_B_TOKEN
    elif raw:
        token = raw
    else:
        token = PQE_A_TOKEN

    while True:
        tasks = show_tasks(args.base_url, token)
        _print()
        pick = input("输入 task_id 处理任务 (q=退出; r=刷新)> ").strip()
        if pick.lower() == "q":
            _print("再见。")
            break
        if pick in ("", "r"):
            continue
        if not any(t["task_id"] == pick for t in tasks):
            _print(f"[!] 未知任务 {pick} (服务端 404 或不在你的项目组 ACL 内，不猜答案)")
            continue
        act_on_task(args.base_url, token, pick)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        main()
    except (KeyboardInterrupt, EOFError):
        _print("\n再见。")

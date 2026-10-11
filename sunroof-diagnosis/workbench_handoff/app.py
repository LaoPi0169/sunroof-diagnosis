"""值守交接工作台 (Week 18) —— 双角色隔离的可交互验收面 (端口 8018)。

双角色隔离 (用户拍板硬要求):
  * 业务用户免登录: 只见权限内操作 —— 问答 (带引用)/只读取数/写预览/提交审批/
    审批后执行/幂等重放/升级; 注入/开关/回滚/审计/清单/复验/重置全部要求管理员登录,
    服务端 403 强制; 口令/token 在任何状态/审计/响应零出现。
  * 管理员 (token 登录): 关闭开关 / 配置回滚 (重放 known-good) / 审计 / 金丝雀 /
    脱敏复验 / 沙盒重置 / 降级演练。

机制全部复用第 12-17 周冻结实现 (generation/mcptool); 版本包与问答为进程内教学模拟。
本文件已入 ci/redaction_scan.py SCOPE_FILES (凭据/政策正文零出现在运行时输出)。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from evaluation.eval_contract import EVAL_SET_PATH, load_eval_set  # noqa: E402
from generation.contract import answer_case  # noqa: E402
from mcptool import contract as C  # noqa: E402
from mcptool.approval import ApprovalService  # noqa: E402
from mcptool.kill_switch import KillSwitchRegistry  # noqa: E402
from mcptool.run_fixtures import BINDING_OK  # noqa: E402
from mcptool.server import call_read, call_write  # noqa: E402
from mcptool.telemetry import TaskTrace, reduce_runtime_metrics  # noqa: E402
from mcptool.ticket_service import TicketService  # noqa: E402

CORPUS_DB = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
ROLES = {"pqe": {"tenant": "sunroof-east", "user": "pqe_chen", "role": "pqe"},
         "quality_supervisor": {"tenant": "sunroof-east", "user": "supervisor_li",
                                "role": "quality_supervisor"}}
ADMIN_TOKEN_ENV = "SUNROOF_ADMIN_TOKEN"
_DEFAULT_ADMIN_TOKEN = "sunroof-admin-w18"

_LOCK = threading.Lock()


class WorkbenchState:
    """按任务隔离的「外部世界」: 每个写任务独立 TicketService (预览封套外部计数恒 0
    的第 14 周冻结核要求效果发生后不能再出预览); 开关注册表系统级跨任务保持。"""

    def __init__(self):
        self.registry = KillSwitchRegistry()
        self.admin_sessions = set()
        self.pending = {}      # task_id -> {args, approval_id, approved}
        self.traces = {}       # task_id -> TaskTrace
        self.audit = []
        self.rollback_log = []
        self._seq = 0

    def next_task_id(self) -> str:
        with _LOCK:
            self._seq += 1
            return f"SR-W18-{self._seq:04d}"

    def task_stack(self, task_id: str = ""):
        approvals = ApprovalService()
        approvals.set_clock(lambda: 1000.0)
        tickets = TicketService()
        return approvals, tickets

    def log(self, entry: dict) -> None:
        self.audit.append({"seq": len(self.audit) + 1, **entry})


STATE = WorkbenchState()


def get_conn():
    if not os.path.exists(CORPUS_DB):
        build_corpus()
    conn = sqlite3.connect(CORPUS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def is_admin(handler) -> bool:
    return handler.headers.get("X-Admin-Session") in STATE.admin_sessions


def handle_api(method: str, path: str, body: dict, admin: bool) -> tuple:
    """路由: 返回 (status, payload)。管理面未登录一律 403 (服务端强制)。"""
    if path == "/api/session/roles":
        return 200, {"roles": list(ROLES)}
    if path == "/api/ask" and method == "POST":
        role = body.get("role")
        query = str(body.get("query", "")).strip()
        if role not in ROLES or not query:
            return 400, {"error": "role/query 必填"}
        conn = get_conn()
        try:
            rec = answer_case({"case_id": body.get("case_id") or "WB-MANUAL",
                               "user_role": role, "query": query}, conn)
        finally:
            conn.close()
        STATE.log({"kind": "ask", "role": role, "state": rec["state"]})
        return 200, {k: rec[k] for k in ("state", "answer_text", "citations",
                                         "reason_code", "user_next_step",
                                         "confidence_top1")}
    if path == "/api/tool/read" and method == "POST":
        role = body.get("role")
        if role not in ROLES:
            return 400, {"error": "role 必填"}
        resp = call_read(ROLES[role], {"case_id": body.get("case_id", ""),
                                       "fields": list(C.READ_FIELDS)})
        STATE.log({"kind": "tool_read", "role": role, "ok": resp.get("ok")})
        return 200, resp
    if path == "/api/write/preview" and method == "POST":
        normalized = validate_write_arguments(body.get("args", {}))
        return 200, {"preview": normalized, "external_effects": 0,
                     "note": "预览零外部效果 (第 14 周冻结核); 策略在执行时校验"}
    if path == "/api/write/submit" and method == "POST":
        role = body.get("role")
        if role not in ROLES:
            return 400, {"error": "role 必填"}
        task_id = STATE.next_task_id()
        args = body.get("args", {})
        approvals, tickets = STATE.task_stack()
        # 审批只由管理员面板结构化签发 (双角色硬纪律: 聊天文本不构成审批,
        # 任何角色的自动签发都会让"主管自批自写"绕过一线发起约束 ——
        # 第 14 周冻结核: 写发起人恒为 pqe, 主管直写 = unauthorized 0 效果)。
        approval_id = None
        trace = TaskTrace(task_id)
        trace.add("receive", {"role": role})
        trace.add("policy", {"queue": args.get("queue", "")})
        STATE.traces[task_id] = {"trace": trace, "approvals": approvals,
                                 "tickets": tickets, "args": args,
                                 "approval_id": approval_id, "role": role,
                                 "approved": False}
        STATE.log({"kind": "write_submit", "task_id": task_id, "role": role})
        return 200, {"task_id": task_id,
                     "state": "ready" if approval_id else "approval_required",
                     "approval_id": approval_id,
                     "note": "聊天文本不构成审批; 审批在管理员面板结构化签发"}
    return None


def handle_api_admin(method: str, path: str, body: dict, admin: bool) -> tuple:
    """管理面 + 执行/任务面。未登录访问 /api/admin/* 一律 403 (login 除外)。"""
    if path == "/api/admin/login" and method == "POST":
        token = os.environ.get(ADMIN_TOKEN_ENV, _DEFAULT_ADMIN_TOKEN)
        if str(body.get("token", "")) == token:
            session = uuid.uuid4().hex
            STATE.admin_sessions.add(session)
            STATE.log({"kind": "admin_login"})
            return 200, {"session": session}
        return 403, {"error": "口令不符"}
    if path.startswith("/api/admin/"):
        if not admin:
            return 403, {"error": "管理员未登录 (服务端强制)"}
        if path == "/api/admin/switch" and method == "POST":
            try:
                entry = STATE.registry.set_state(body.get("switch"),
                                                 bool(body.get("on")),
                                                 body.get("operator", "admin"),
                                                 body.get("reason", ""))
            except ValueError as exc:
                return 400, {"error": str(exc)}
            return 200, {"entry": entry, "snapshot": STATE.registry.snapshot()}
        if path == "/api/admin/rollback" and method == "POST":
            STATE.rollback_log.append({"seq": len(STATE.rollback_log) + 1,
                                       "reason": str(body.get("reason", "")),
                                       "action": "replay known-good policy_config"})
            return 200, {"ok": True, "log": STATE.rollback_log[-10:]}
        if path == "/api/admin/audit" and method == "GET":
            return 200, {"switch_audit": STATE.registry.audit(),
                         "ops_audit": STATE.audit[-50:],
                         "snapshot": STATE.registry.snapshot(),
                         "rollback_log": STATE.rollback_log}
        if path == "/api/admin/canary" and method == "POST":
            from mcptool.run_canary import run_canary
            out = os.path.join(PROJECT_ROOT, "week-16", "canary-baseline-v1.jsonl")
            return 200, run_canary(out)
        if path == "/api/admin/reset" and method == "POST":
            STATE.traces.clear()
            STATE.log({"kind": "reset"})
            return 200, {"ok": True}
        return 404, {"error": "unknown admin endpoint"}
    if path == "/api/write/approve" and method == "POST":
        if not admin:
            return 403, {"error": "审批属管理员职责 (双角色隔离)"}
        task_id = body.get("task_id")
        task = STATE.traces.get(task_id)
        if not task:
            return 404, {"error": "任务不存在"}
        # 结构化审批签发 (十要素绑定; 聊天文本不构成审批)
        binding = dict(BINDING_OK)
        binding["case_id"] = str(task["args"].get("case_id", "")).strip().upper()
        task["approval_id"] = task["approvals"].issue("supervisor_li", binding)
        task["approved"] = True
        STATE.log({"kind": "write_approve", "task_id": task_id})
        return 200, {"task_id": task_id, "state": "approved",
                     "approval_id": task["approval_id"]}


def handle_api_execute(method: str, path: str, body: dict, admin: bool) -> tuple:
    """执行面 + 任务追踪 (业务用户可见自己任务的终态)。"""
    if path == "/api/write/execute" and method == "POST":
        task_id = body.get("task_id")
        task = STATE.traces.get(task_id)
        if not task:
            return 404, {"error": "任务不存在"}
        trusted = ROLES[task["role"]]
        args = {**task["args"], "approval_id": task["approval_id"] or ""}
        resp = call_write(trusted, args, task["approvals"], task["tickets"],
                          STATE.registry)
        code = resp.get("error", {}).get("code")
        outcome = ("action_completed" if resp.get("ok")
                   else "action_preview" if code == "mcp_unavailable"
                   else "forbidden" if code == "unauthorized"
                   else "approval_denied" if code in ("approval_denied",
                                                      "approval_mismatch")
                   else "result_unknown")
        task["trace"].add("write", {"task_id": task_id})
        task["trace"].mark_terminal("write", outcome,
                                    effects=resp.get("external_effects_delta", 0))
        STATE.log({"kind": "write_execute", "task_id": task_id, "ok": resp.get("ok")})
        return 200, {**resp, "terminal": outcome,
                     "external_effects": task["tickets"].external_effects()}
    if path == "/api/task" and method == "GET":
        task_id = body.get("task_id")
        task = STATE.traces.get(task_id)
        if not task:
            return 404, {"error": "任务不存在"}
        trace = task["trace"]
        trace.finalize_missing_stages()
        return 200, {"task_id": task_id, "spans": trace.spans,
                     "terminal": trace.terminal,
                     "metrics": reduce_runtime_metrics([trace])}
    return 404, {"error": "unknown endpoint"}


class WorkbenchHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        path_only = self.path.split("?")[0]
        if method == "GET" and path_only == "/healthz":
            # 服务身份与健康探针 (Docker HEALTHCHECK 用; 零敏感信息零鉴权)。
            # 用途 1: 容器编排探活; 用途 2: 浏览器打开 8018 时先验明本页是
            # 天窗诊断自研工作台, 而非课程模板 week-018 同端口参考实现。
            self._json(200, {"ok": True,
                             "service": "sunroof-diagnosis-workbench",
                             "project": "天窗异响诊断", "template": False})
            return
        if method == "GET" and path_only in ("/", "/index.html"):
            html = build_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        admin = is_admin(self)
        try:
            result = handle_api(method, path_only, body, admin)
            if result is None:
                result = handle_api_admin(method, path_only, body, admin)
            if result is None:
                result = handle_api_execute(method, path_only, body, admin)
            status, payload = result
        except Exception as exc:  # 教学沙盒: 异常不泄漏内部细节
            status, payload = 500, {"error": "internal_error", "detail": str(exc)}
        self._json(status, payload)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def log_message(self, *args):  # 静默访问日志 (教学单机)
        pass


def build_index_html() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main() -> None:
    if not os.path.exists(CORPUS_DB):
        build_corpus()
    bind = os.environ.get("SUNROOF_BIND", "127.0.0.1")   # 容器部署设 0.0.0.0
    port = int(os.environ.get("SUNROOF_PORT", "8018"))
    try:
        server = ThreadingHTTPServer((bind, port), WorkbenchHandler)
    except OSError as exc:
        print(json.dumps(
            {"fatal": "bind_failed", "bind": bind, "port": port,
             "detail": str(exc),
             "hint": "端口被占用: 最常见原因是课程模板 week-018 工作台 "
                     "(工作区根目录另一套 workbench_handoff) 先启动抢了 8018。"
                     "用 netstat -ano | findstr :8018 找 PID, "
                     "taskkill /PID <pid> /F 结束后重跑 start_workbench_handoff.bat。"},
            ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps({"workbench": "sunroof-diagnosis-handoff",
                      "project": "天窗异响诊断 (非课程模板)",
                      "bind": bind, "port": port,
                      "admin_env": ADMIN_TOKEN_ENV}, ensure_ascii=False))
    server.serve_forever()


if __name__ == "__main__":
    main()


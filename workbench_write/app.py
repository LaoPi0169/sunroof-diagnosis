"""业务用户交互工作台 (Week 14 增补) —— 浏览器点击式审批写测试入口, 全程无命令行。

交付对象: 业务用户 —— 只操作浏览器页面 (workbench_write/static/index.html),
不接触命令行、不安装环境。交付人双击 start_workbench.bat (或
python -m uvicorn workbench_write.app:app --host 127.0.0.1 --port 8014) 启动一次,
测试者打开 http://127.0.0.1:8014, 按 week-14/business-user-test-script-v1.md 逐步测试。

定位 (回链 week-14/ticket-write-contract-v1.json):
  - 本模块是 MCP Host 之上的薄 HTTP 壳: 规范化/策略/审批/幂等/核验/硬门全部复用
    mcptool 现有组件; 不实现任何新业务规则、不新增任何写入口;
  - 页面流程「预览 -> 拒绝/批准 -> 执行(可改参/重放) -> 核验」与
    mcptool/demo_write_approval.py 四次尝试一一对应 (W-01..W-05 用户可见子集);
  - 会话固定为教学模拟客服周宁 (TrustedSession), 单用户单会话, 不做真实登录;
  - 时钟用真实 UTC: 审批 TTL 900s 真实生效 (批准后等 15 分钟可实测过期路径);
  - 审批记录与审计链属受限审计面 (restricted): 仅在页面面板回显给本人,
    不写日志文件; 本文件已入 ci/redaction_scan.py SCOPE_FILES (凭据/政策正文零出现)。
"""
from __future__ import annotations

import json
import os
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from mcptool import write_contract as W
from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.policy import WritePolicy
from mcptool.ticket_service import TicketService

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX_HTML = os.path.join(_STATIC_DIR, "index.html")

# 教学模拟会话 (与 demo_write_approval.py 同一身份; 生产中来自登录态)
SESSION = TrustedSession("northstar-training", "user-zhou-ning", "support_agent")
RAG_TERMINAL = "escalate"   # 场景: RAG 终态已 escalate (策略组件只认确定性终态)

# 页面表单默认值 (故意用未规范化输入: 小写原因/乱序引用/大小写混合队列, 供预览演示)
FORM_KEYS = ("case_id", "reason_code", "source_refs", "target_queue")
DEFAULT_FORM = {
    "case_id": "NS-1042",
    "reason_code": "policy_conflict",
    "source_refs": "upgrade-notice-v4, upgrade-east-v2",
    "target_queue": "Billing-Supervisor-Review",
}


def utc_now() -> str:
    """真实 UTC 时钟 (与 Host 缺省时钟同格式; 审批 TTL 由此真实计时)。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def raw_arguments_from_form(form: dict) -> dict:
    """页面表单 -> 写工具原始参数 (source_refs 逗号分隔; 空项保留给 schema 拒绝演示)。"""
    refs_raw = str(form.get("source_refs", ""))
    return {
        "case_id": str(form.get("case_id", "")),
        "reason_code": str(form.get("reason_code", "")),
        "source_refs": [chunk.strip() if chunk.strip() else chunk
                        for chunk in refs_raw.split(",")],
        "target_queue": str(form.get("target_queue", "")),
    }


class WorkbenchSession:
    """一个独立的「外部世界」实例 (重置即换新): 工单栈/审批栈/Host/审计全部隔离。"""

    def __init__(self):
        self.run_id = f"run-w14-{time.strftime('%H%M%S')}"
        self.tickets = TicketService()
        self.approvals = ApprovalService()
        self.policy = WritePolicy()
        self.host = McpHost(SESSION, clock=utc_now, ticket_service=self.tickets,
                            approval_service=self.approvals, policy=self.policy)
        self.form = dict(DEFAULT_FORM)
        self.decision_id = None   # 最近一次用户决定记录 ID (审批或拒绝)
        self.decision = None      # "approved" | "denied" | None
        self.sequence = 0         # 本会话内用户决定序号 (进入决定 ID 编号)


_STATE: WorkbenchSession | None = None


def _get_state() -> WorkbenchSession:
    global _STATE
    if _STATE is None:
        _STATE = WorkbenchSession()
    return _STATE


def _merge_form(payload: dict) -> dict:
    form = dict(DEFAULT_FORM)
    for key in FORM_KEYS:
        if payload.get(key) is not None:
            form[key] = str(payload[key])
    return form


def _snapshot(s: WorkbenchSession) -> dict:
    """页面状态快照。审批记录/审计链属受限审计面 (restricted), 仅回显给本人。"""
    record, effective = None, "none"
    if s.decision_id:
        record, effective = s.approvals.status_now(s.decision_id, now=utc_now())
    tickets = json.loads(s.tickets.snapshot())
    return {
        "run_id": s.run_id,
        "session_restricted": SESSION.as_dict(),
        "rag_terminal": RAG_TERMINAL,
        "server_now": utc_now(),
        "form": dict(s.form),
        "decision_id": s.decision_id,
        "decision": s.decision,
        "decision_effective_status": effective,
        "decision_record_restricted": record,
        "idempotency_key": W.idempotency_key(s.run_id),
        "approval_ttl_seconds": s.policy.APPROVAL_TTL_SECONDS,
        "policy_version": s.policy.POLICY_VERSION,
        "ticket_total": s.tickets.ticket_count(),
        "tickets": list(tickets.values()),
        "external_write_effects": s.host.external_write_effects,
        "security_events": list(s.host.security_events),
        "audit_restricted": list(s.host.audit),
    }


app = FastAPI(title="Week 14 审批写工具 · 业务用户工作台")


@app.get("/", include_in_schema=False)
def index():
    """前台页面 (中文, 原生 HTML/JS, 零依赖零构建)。"""
    return FileResponse(_INDEX_HTML)


@app.get("/api/state")
def get_state():
    return _snapshot(_get_state())


@app.post("/api/reset")
def reset(payload: dict = {}):
    """重置: 换全新「外部世界」(工单清零/审计清零/新 run_id)。"""
    global _STATE
    _STATE = WorkbenchSession()
    return _snapshot(_STATE)


@app.post("/api/preview")
def preview(payload: dict):
    """步骤 1: ticket.preview_create —— 无副作用预览 (W-01 用户可见子集)。"""
    s = _get_state()
    s.form = _merge_form(payload)
    envelope = s.host.call_write(W.TOOL_PREVIEW, raw_arguments_from_form(s.form),
                                 run_id=s.run_id, rag_terminal=RAG_TERMINAL)
    return {"envelope": envelope, "state": _snapshot(s)}


@app.post("/api/decide")
def decide(payload: dict):
    """步骤 2: 受控确认动作 (结构化审批; 不读任何聊天文本, 拒绝不自动重问)。"""
    s = _get_state()
    decision = str(payload.get("decision", ""))
    if decision not in ("approve", "deny"):
        return JSONResponse(status_code=400, content={
            "error": "invalid_argument",
            "detail": "decision 必须是 approve 或 deny"})
    canonical, err = W.validate_write_arguments(raw_arguments_from_form(s.form))
    if err:
        return JSONResponse(status_code=400,
                            content={"error": "invalid_argument", "detail": err})
    s.sequence += 1
    decided_at = utc_now()
    if decision == "deny":
        record = s.approvals.record_denial(
            denial_id=f"denial-{s.run_id}-{s.sequence}",
            requester=SESSION.user_id, tenant_id=SESSION.tenant_id,
            run_id=s.run_id, proposed_parameters=canonical, decided_at=decided_at)
        s.decision_id, s.decision = record["approval_id"], "denied"
    else:
        record = s.approvals.issue(
            approval_id=f"approval-{s.run_id}-{s.sequence}",
            requester=SESSION.user_id, tenant_id=SESSION.tenant_id,
            run_id=s.run_id, tool=W.TOOL_CREATE, tool_version=W.TOOL_VERSION,
            approved_parameters=canonical,
            resource_scope=W.resource_scope(canonical["case_id"]),
            policy_version=s.policy.POLICY_VERSION, issued_at=decided_at)
        s.decision_id, s.decision = record["approval_id"], "approved"
    return {"record": record, "state": _snapshot(s)}


@app.post("/api/execute")
def execute(payload: dict):
    """步骤 3: ticket.create —— 唯一产生外部效果的入口 (改参/重放由页面表单表达)。"""
    s = _get_state()
    s.form = _merge_form(payload)
    envelope = s.host.call_write(W.TOOL_CREATE, raw_arguments_from_form(s.form),
                                 run_id=s.run_id, approval_id=s.decision_id,
                                 rag_terminal=RAG_TERMINAL)
    return {"envelope": envelope, "state": _snapshot(s)}


@app.post("/api/verify")
def verify(payload: dict):
    """步骤 4: ticket.get —— 独立只读核验 (结果核验与人工查询共用)。"""
    s = _get_state()
    envelope = s.host.call_write(
        W.TOOL_GET, {"ticket_id": str(payload.get("ticket_id", ""))},
        run_id=s.run_id, rag_terminal=RAG_TERMINAL)
    return {"envelope": envelope, "state": _snapshot(s)}


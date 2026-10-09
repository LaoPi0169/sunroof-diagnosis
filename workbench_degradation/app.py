"""业务用户降级工作台 (Week 17) —— 浏览器点击式「依赖故障 -> 安全降级 -> 恢复复验」。

交付对象: 业务用户 —— 只操作浏览器页面 (workbench_degradation/static/index.html),
不接触命令行。交付人双击 start_workbench_degradation.bat (或
python -m uvicorn workbench_degradation.app:app --host 127.0.0.1 --port 8017)
启动一次, 测试者打开 http://127.0.0.1:8017, 按
week-17/business-user-test-script-v1.md (W-00..W-08) 逐步测试。

定位 (回链 week-17/failure-behavior-contract-v1.md + 手册 3889-4004):
  - 本模块是 mcptool 组件之上的薄 HTTP 壳: 不实现任何新业务规则、不新增任何
    模型可调用写入口 (工具仍 1 读 + 3 写; 降级/开关/注入全部复用 client.py 编排);
  - 页面三条主线: ①注入故障看降级终态 (action_preview + 效果 0) ②关闭开关
    MCP_OFF (运营手段, 审计留痕) ③恢复复验 (同一任务续跑 + 幂等重放 + 受限任务
    仍拒绝) —— 两条故障路 (注入/开关) 落同一个安全终态;
  - 三类固定演练 (模型超时/索引过期/MCP 不可用) 一键运行 run_drills 全剧本;
  - 任务追踪: 任一 task_run_id 还原九阶段链 (含降级段 mcp_unavailable);
  - 脱敏检查: 遥测流机械扫描 (正文/载荷/凭据键零出现)。
教学模拟边界: 单用户单会话; 工单都是隔离模拟; 本文件已入 ci/redaction_scan.py
SCOPE_FILES (凭据/政策正文零出现)。
"""
from __future__ import annotations

import os
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.kill_switch import SWITCH_MCP_OFF, KillSwitchRegistry, WriteChannelProbe
from mcptool.policy import WritePolicy
from mcptool.run_degradation_drill import WRITE_ARGS, run_drills
from mcptool.telemetry import (  # noqa: E402
    STAGE_APPROVAL, STAGE_GENERATION, STAGE_IDENTITY, STAGE_MCP_CALL,
    STAGE_RECONCILIATION, STAGE_REQUEST, STAGE_RESPONSE, STAGE_RETRIEVAL,
    STAGE_VERIFICATION, TaskTrace, TRACE_OUTCOME_PREVIEW_HELD,
    scan_spans_for_violations,
)
from mcptool.ticket_service import TicketService

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX_HTML = os.path.join(_STATIC_DIR, "index.html")

TENANT = "northstar-training"
SESSION = TrustedSession(TENANT, "user-zhou-ning", "support_agent")
RAG_TERMINAL = "escalate"

_STATE = {
    "registry": None, "probe": None, "tickets": None, "approvals": None,
    "policy": None, "host": None, "run_seq": 1041, "run_id": None,
    "preview": None, "approval_id": None, "decision_effective": None,
    "active_trace": None, "traces": {}, "task_order": [], "degraded": None,
    "drills": None, "updated_at": None,
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mark():
    _STATE["updated_at"] = _now()


def _reset_stack():
    registry = KillSwitchRegistry()
    probe = WriteChannelProbe()
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, ticket_service=tickets, approval_service=approvals,
                   policy=policy, kill_switch=registry, write_probe=probe)
    _STATE.update({
        "registry": registry, "probe": probe, "tickets": tickets,
        "approvals": approvals, "policy": policy, "host": host,
        "run_seq": 1041, "run_id": None, "preview": None,
        "approval_id": None, "decision_effective": None,
        "active_trace": None, "traces": {}, "task_order": [],
        "degraded": None, "drills": None,
    })


_reset_stack()
_mark()

app = FastAPI(title="Week 17 安全降级 · 业务用户工作台")


class DecideBody(BaseModel):
    decision: str  # approve | deny


class SwitchBody(BaseModel):
    action: str    # set | clear
    operator: str = "业务用户(演示)"
    reason: str = ""


class FaultBody(BaseModel):
    action: str    # inject | restore


def _degraded_mode() -> dict:
    probe_on = _STATE["probe"].unavailable
    switch_on = _STATE["registry"].is_active(SWITCH_MCP_OFF)
    return {
        "fault_injected": probe_on,
        "mcp_off_active": switch_on,
        "degraded_now": probe_on or switch_on,
        "source": ("fault_injection" if probe_on
                   else ("kill_switch:MCP_OFF" if switch_on else "")),
    }


def _new_task():
    """新任务 = 新的独立「外部世界」(与第 16 周回放 _fresh_write_stack 同构)。

    工单/审批/策略/Host 每任务一份 (第 14 周冻结核: 预览封套外部计数必须为 0,
    即效果已发生的 store 不能再出预览 —— 所以任务之间不共享 store);
    但 关关注册表 与 故障注入缝 是系统级的, 跨任务保持 (开关/注入不随任务重置)。
    """
    registry, probe = _STATE["registry"], _STATE["probe"]
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, ticket_service=tickets, approval_service=approvals,
                   policy=policy, kill_switch=registry, write_probe=probe)
    _STATE["tickets"], _STATE["approvals"] = tickets, approvals
    _STATE["policy"], _STATE["host"] = policy, host
    _STATE["run_seq"] += 1
    run_id = f"northstar-w17-{_STATE['run_seq']}"
    trace = TaskTrace(run_id, kind="write")
    trace.span(STAGE_REQUEST, result="write_request",
               safe_refs={"case_id": WRITE_ARGS["case_id"],
                          "upstream_scenario": "ns-1042-rag-escalate"})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    for stage, note in ((STAGE_RETRIEVAL, "写任务不检索"),
                        (STAGE_GENERATION, "写任务不生成")):
        trace.span(stage, result="not_applicable", not_applicable=True,
                   safe_refs={"note": note})
    _STATE["run_id"] = run_id
    _STATE["active_trace"] = trace
    _STATE["degraded"] = None
    _STATE["preview"] = None            # 新任务必须重新预览 (预览即合同)
    _STATE["approval_id"] = None        # 新任务必须重新受控确认
    _STATE["decision_effective"] = None
    return run_id, trace


def _archive(trace):
    _STATE["traces"][trace.task_run_id] = trace
    if trace.task_run_id not in _STATE["task_order"]:
        _STATE["task_order"].append(trace.task_run_id)
def _snapshot():
    traces = [_STATE["traces"][tid].as_task_row() for tid in _STATE["task_order"]]
    active = _STATE["active_trace"]
    host = _STATE["host"]
    return {
        "run_id": _STATE["run_id"],
        "has_preview": _STATE["preview"] is not None,
        "preview": _STATE["preview"],
        "has_approval": _STATE["approval_id"] is not None,
        "decision_effective": _STATE["decision_effective"],
        "degraded": _degraded_mode(),
        "degraded_task": _STATE["degraded"],
        "switch_status": _STATE["registry"].status(),
        "switch_audit": _STATE["registry"].audit_trail()[-8:],
        "ticket_total": _STATE["tickets"].ticket_count(),
        "external_effects": host.external_write_effects,
        "security_events": host.security_events,
        "active_task": active.as_task_row() if active else None,
        "tasks": traces,
        "drills_summary": (_STATE["drills"]["verdict"]
                           if _STATE["drills"] else None),
        "updated_at": _STATE["updated_at"],
    }


@app.get("/", include_in_schema=False)
def index():
    """前台页面 (中文, 原生 HTML/JS, 零依赖零构建)。"""
    return FileResponse(_INDEX_HTML)


@app.get("/api/state")
def get_state():
    return _snapshot()


@app.post("/api/reset")
def reset():
    """重置: 全新「外部世界」(工单/审批/开关/注入全部归零)。"""
    _reset_stack()
    _mark()
    return _snapshot()


@app.post("/api/preview")
def preview():
    """生成工单预览 (无副作用: 预览不创建任何外部记录)。"""
    if _STATE["degraded"]:
        return JSONResponse(status_code=409, content={
            "error": "degraded_task_pending",
            "detail": ("有一个任务停在 action_preview；先「解除注入/复位开关」"
                       "并「续跑同一任务」，或「重置演示环境」")})
    host = _STATE["host"]
    run_id, trace = _new_task()
    host = _STATE["host"]  # _new_task 重建了独立写栈, 取新 host
    envelope = host.call_write("ticket.preview_create", dict(WRITE_ARGS),
                               run_id=run_id, rag_terminal=RAG_TERMINAL)
    trace.span(STAGE_VERIFICATION,
               result=("policy:allowed_at_preview"
                       if envelope.get("status") == "action_preview"
                       else "policy:denied"),
               versions={"policy_version": _STATE["policy"].POLICY_VERSION},
               safe_refs={"rag_terminal": RAG_TERMINAL})
    _STATE["preview"] = (envelope
                         if envelope.get("status") == "action_preview" else None)
    _mark()
    return {"envelope": envelope, "state": _snapshot()}


@app.post("/api/decide")
def decide(body: DecideBody):
    """受控确认动作: approve=签发结构化审批; deny=记录拒绝 (均留痕)。"""
    if _STATE["run_id"] is None:
        return JSONResponse(status_code=400, content={
            "error": "no_task", "detail": "先点「生成预览」"})
    approvals, policy = _STATE["approvals"], _STATE["policy"]
    trace = _STATE["active_trace"]
    run_id = _STATE["run_id"]
    if body.decision == "approve":
        record = approvals.issue(
            approval_id=f"approval-{run_id}", requester=SESSION.user_id,
            tenant_id=TENANT, run_id=run_id, tool="ticket.create",
            tool_version="1", approved_parameters=dict(WRITE_ARGS),
            resource_scope=f"case:{WRITE_ARGS['case_id']}",
            policy_version=policy.POLICY_VERSION, issued_at=_now())
        _STATE["approval_id"] = record["approval_id"]
        _STATE["decision_effective"] = "approved"
        trace.span(STAGE_APPROVAL, result="approval:approved",
                   versions={"approval_record_version": "approval-record-v1"},
                   safe_refs={"approval_id": record["approval_id"],
                              "decision": "approved"})
    else:
        record = approvals.record_denial(
            denial_id=f"denial-{run_id}", requester=SESSION.user_id,
            tenant_id=TENANT, run_id=run_id,
            proposed_parameters=dict(WRITE_ARGS), decided_at=_now())
        _STATE["approval_id"] = None
        _STATE["decision_effective"] = "denied"
        trace.span(STAGE_APPROVAL, result="approval:denied",
                   safe_refs={"approval_id": record["approval_id"],
                              "decision": "denied"})
    _mark()
    return {"record_restricted": {"approval_id": record["approval_id"],
                                  "decision": record["decision"]},
            "state": _snapshot()}
def _execute_create(trace, run_id, approval_id):
    """执行创建并按结果收口 trace (完成/拒绝 -> 终态; 降级 -> 停驻终态)。"""
    host, tickets = _STATE["host"], _STATE["tickets"]
    before = tickets.ticket_count()
    envelope = host.call_write("ticket.create", dict(WRITE_ARGS),
                               run_id=run_id, approval_id=approval_id,
                               rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    degraded = bool(envelope.get("degraded"))
    refs = {"tool": "ticket.create",
            "idempotency_key": f"{run_id}:ticket.create:1",
            "ticket_id": envelope.get("ticket_id"),
            "verified_by": envelope.get("verified_by"),
            "effects_delta": delta}
    if degraded:
        refs["degraded"] = True
        refs["trigger"] = _degraded_mode()["source"]
        refs["allowed_next_actions"] = envelope.get("allowed_next_actions")
    err = envelope.get("reason_code") or envelope.get("error", {}).get("code")
    trace.span(STAGE_MCP_CALL,
               result=(envelope.get("error", {}).get("code")
                       if envelope.get("status") == "error"
                       else envelope.get("status")),
               versions={"tool_version": "1"}, safe_refs=refs,
               error_class=err)
    trace.span(STAGE_RECONCILIATION, result="not_applicable",
               not_applicable=True,
               safe_refs={"note": "未提交 (0 效果), 无对账段" if degraded
                          else "正常完成, 无对账段"})
    if envelope.get("status") == "action_completed":
        trace.span(STAGE_RESPONSE, result="action_completed")
        trace.finish("action_completed", effects=delta)
        _archive(trace)
        _STATE["active_trace"] = None
        _STATE["degraded"] = None
        _STATE["preview"] = None  # 预览已消耗 (完成), 下一步用新任务
    elif envelope.get("status") == "error":
        trace.span(STAGE_RESPONSE, result="error_presented",
                   error_class=envelope["error"]["code"])
        trace.finish(envelope["error"]["code"], effects=delta)
        _archive(trace)
        _STATE["active_trace"] = None
    else:  # 降级停驻: action_preview (效果 0, 审批未消耗, 可续跑)
        trace.span(STAGE_RESPONSE, result="degraded_preview_presented",
                   error_class="mcp_unavailable")
        trace.finish(TRACE_OUTCOME_PREVIEW_HELD, effects=delta)
        _archive(trace)
        _STATE["active_trace"] = None
        _STATE["degraded"] = {"run_id": run_id,
                              "approval_id": approval_id,
                              "reason_code": "mcp_unavailable"}
    return envelope


@app.post("/api/execute")
def execute():
    """执行创建: 正常 -> action_completed; 依赖故障 -> 停在 action_preview。"""
    approval_bound = (_STATE["approval_id"] is not None
                      and _STATE["approval_id"] == f"approval-{_STATE['run_id']}")
    if (not _STATE["preview"] or not approval_bound
            or _STATE["decision_effective"] != "approved"):
        return JSONResponse(status_code=400, content={
            "error": "not_ready",
            "detail": "需要先「生成预览」并选择「批准」(受控确认动作; 审批必须"
                      "绑定当前任务)"})
    envelope = _execute_create(_STATE["active_trace"], _STATE["run_id"],
                               _STATE["approval_id"])
    _mark()
    return {"envelope": envelope, "state": _snapshot()}
@app.post("/api/retry")
def retry():
    """恢复后同一任务续跑 (retry_later): 同一 run_id + 同一审批 -> 恰 1 效果。"""
    task = _STATE["degraded"]
    if not task:
        return JSONResponse(status_code=400, content={
            "error": "no_degraded_task",
            "detail": "当前没有停在预览的任务; 先注入故障并执行一次"})
    run_id = task["run_id"]
    trace = TaskTrace(run_id + "-retry", kind="write")
    trace.span(STAGE_REQUEST, result="write_request",
               safe_refs={"case_id": WRITE_ARGS["case_id"],
                          "recovery_of": run_id,
                          "recovery": "retry_later_after_restore"})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    for stage, note in ((STAGE_RETRIEVAL, "写任务不检索"),
                        (STAGE_GENERATION, "写任务不生成")):
        trace.span(stage, result="not_applicable", not_applicable=True,
                   safe_refs={"note": note})
    trace.span(STAGE_VERIFICATION, result="policy:allowed_at_preview",
               versions={"policy_version": _STATE["policy"].POLICY_VERSION},
               safe_refs={"rag_terminal": RAG_TERMINAL})
    trace.span(STAGE_APPROVAL,
               result="approval:approved_not_consumed_during_degradation",
               safe_refs={"approval_id": task["approval_id"],
                          "decision": "approved"})
    _STATE["active_trace"] = trace
    _STATE["run_id"] = run_id
    envelope = _execute_create(trace, run_id, task["approval_id"])
    _mark()
    return {"envelope": envelope, "state": _snapshot()}


@app.post("/api/replay")
def replay():
    """幂等重放: 同一幂等键再执行一次 -> 返回原工单, 计数不变 (无重复效果)。"""
    if _STATE["run_id"] is None:
        return JSONResponse(status_code=400, content={
            "error": "no_task", "detail": "还没有任务; 先走一次预览->批准->执行"})
    host, tickets = _STATE["host"], _STATE["tickets"]
    before = tickets.ticket_count()
    envelope = host.call_write("ticket.create", dict(WRITE_ARGS),
                               run_id=_STATE["run_id"],
                               approval_id=_STATE["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    unchanged = tickets.ticket_count() == before
    _mark()
    return {"envelope": envelope, "effect_count_unchanged": unchanged,
            "ticket_total": tickets.ticket_count(), "state": _snapshot()}


@app.post("/api/fault")
def fault(body: FaultBody):
    """注入/解除「MCP 写通道不可达」(演练手段; 与关闭开关两条路同一终态)。"""
    _STATE["probe"].unavailable = (body.action == "inject")
    _mark()
    return {"degraded": _degraded_mode(), "state": _snapshot()}


@app.post("/api/switch")
def switch(body: SwitchBody):
    """关闭开关 MCP_OFF 开/复位 (运营手段; 动作与理由进追加式审计)。"""
    try:
        if body.action == "set":
            event = _STATE["registry"].set(
                SWITCH_MCP_OFF, operator=body.operator or "业务用户(演示)",
                reason=body.reason or "页面演示：隔离 MCP 写通道")
        else:
            event = _STATE["registry"].clear(
                SWITCH_MCP_OFF, operator=body.operator or "业务用户(演示)",
                reason=body.reason or "页面演示：按批准流程复位")
    except ValueError as exc:
        return JSONResponse(status_code=400, content={
            "error": "bad_request", "detail": str(exc)})
    _mark()
    return {"event_restricted": event, "degraded": _degraded_mode(),
            "state": _snapshot()}
@app.post("/api/drills")
def drills():
    """三类固定演练 (模型超时/索引过期/MCP 不可用 + 恢复复验 8 段全剧本)。"""
    _STATE["drills"] = run_drills()
    _mark()
    result = _STATE["drills"]
    return {"verdict": result["verdict"], "drills": result["drills"],
            "metrics": result["metrics"], "redaction": result["redaction"],
            "switch_audit": result["switch_audit"], "state": _snapshot()}


@app.get("/api/trace/{task_run_id}")
def trace(task_run_id: str):
    """任务追踪: 任选 task_run_id 还原九阶段链 (含降级段)。"""
    found = _STATE["traces"].get(task_run_id)
    if found is None:
        return JSONResponse(status_code=404, content={
            "error": "not_found",
            "detail": f"本会话没有 {task_run_id} (重置会清空; 演练请重跑)"})
    return {"task": found.as_task_row(), "spans": found.spans,
            "versions": found.versions, "safe_refs": found.safe_refs,
            "state": _snapshot()}


@app.post("/api/redaction-check")
def redaction_check():
    """遥测脱敏检查: 对本会话全部 span + 演练流机械扫描。"""
    spans = [sp for tid in _STATE["task_order"]
             for sp in _STATE["traces"][tid].spans]
    if _STATE["drills"]:
        spans = spans + _STATE["drills"]["spans"]
    violations = scan_spans_for_violations(spans)
    return {"checked_events": len(spans), "violations": violations,
            "passed": not violations,
            "verdict": ("通过: 遥测流未出现 query 原文/答案正文/审批载荷/"
                        "客户字段/凭据" if not violations
                        else "违规: 遥测流出现受限内容 (见 violations 键名)")}
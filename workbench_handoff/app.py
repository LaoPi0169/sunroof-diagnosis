"""值守交接工作台 (Week 18) —— 冷交接演练的「服务台」, 双角色隔离。

定位 (回链 week-18/ops-handoff-pack-v1.md + week-18/sealed-fault-pack-v1.md +
specs/business-background.md 4006-4148 第 18 周: 值守交接 / 4076 「交接包中每个
动作都有权限角色」):

  - 业务用户 (默认视图, 免登录): 只见其权限内的操作 —— 现行口径问答 / 工单预览
    -> 受控确认 -> 执行 / 稍后重试 / 幂等重放 / 主管升级; 看不到也调不了任何配置
    类接口 (注入/开关/回滚/审计/清单) —— 管理接口服务端强制 403 admin_required,
    不靠前端藏按钮;
  - 管理员 (值守/非作者运营者, 必须登录): 注入封存故障 F-18.1 (MCP 写通道不可达)
    / F-18.2 (配置回归: 废止政策被复活进现行绑定) / 开关 MCP_OFF (operator+reason
    审计) / 完整版本包回滚 (rc-18.1 -> rc-18.0, reason 审计) / 发布清单六类绑定 /
    恢复「四+1 条任务路径」复验跑批 / 重置环境;
  - 登录用 值守代号 + 值守口令 (教学模拟固定值, 见 week-18/handoff-workbench-plain-
    guide-v1.md): 口令只用于比对, 绝不写入遥测/审计/响应/日志 (登录审计只记代号与
    结果); token 仅存内存, 重置即全部失效;
  - 两条故障路 (F-18.1 注入 / MCP_OFF 开关) 落同一个安全终态 (action_preview,
    效果 0, 审批不消耗); F-18.2 的症状在任务面可见: 正常问答引用了废止口径版本;
    处置 = 管理员回滚完整版本包 (不是关开关 —— 关开关修不了配置回归), 恢复 =
    重跑四+1 条任务路径, 只看健康接口不算;
  - 本模块是 mcptool 组件之上的薄 HTTP 壳: 不新增模型可调用写入口, 不发明新机制
    (手册 4015: 不重新发明告警/降级/回滚 —— 降级/开关/幂等全部复用第 14-17 周
    冻结实现, 版本包登记是演练对象的教学模拟, 非生产机制)。
教学模拟边界: 单浏览器单会话; 版本包/问答为进程内登记; 本文件已入
ci/redaction_scan.py SCOPE_FILES (凭据/政策正文零出现在运行时输出)。
"""
from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.kill_switch import SWITCH_MCP_OFF, KillSwitchRegistry, WriteChannelProbe
from mcptool.policy import WritePolicy
from mcptool.run_degradation_drill import WRITE_ARGS
from mcptool.telemetry import (  # noqa: E402
    STAGE_APPROVAL, STAGE_GENERATION, STAGE_IDENTITY, STAGE_MCP_CALL,
    STAGE_RECONCILIATION, STAGE_REQUEST, STAGE_RESPONSE, STAGE_RETRIEVAL,
    STAGE_VERIFICATION, TaskTrace, TRACE_OUTCOME_PREVIEW_HELD,
    scan_spans_for_violations,
)
from mcptool.ticket_service import TicketService
from mcptool import write_contract as W

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX_HTML = os.path.join(_STATIC_DIR, "index.html")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST_PATH = os.path.join(_PROJECT_ROOT, "week-18", "full-release-manifest-v2.json")

TENANT = "northstar-training"
SESSION = TrustedSession(TENANT, "user-zhou-ning", "support_agent")
RAG_TERMINAL = "escalate"

# 教学模拟值守口令 (固定值; 真实系统为个人凭据+轮换, 绝不入库/日志/遥测)。
ADMIN_PASSCODE = "oncall-w18"

# ---- 完整版本包登记 (演练对象的教学模拟; 真实系统 = 制品库 + 部署系统) ----
PKG_KNOWN_GOOD = "rc-18.0"
PKG_CURRENT = "rc-18.1"  # 交接给值守的「现行版本包」: 按封存剧本 F-18.2 已埋配置回归
PACKAGES = {
    PKG_KNOWN_GOOD: {
        "release_id": "northstar-app-w18-rc-18.0",
        "known_good": True,
        "policy_active_binding": "billing-general-v3",
        "policy_active_version": "v3 (2026-01-01 起现行)",
        "note": "已知良好版本包: 现行口径绑定 billing-general-v3",
    },
    PKG_CURRENT: {
        "release_id": "northstar-app-w18-rc-18.1",
        "known_good": False,
        "policy_active_binding": "billing-general-v1",
        "policy_active_version": "v1 (2025 版, 已废止, 不得对客引用为现行)",
        "note": "配置回归 (F-18.2): 废止版本被复活进现行口径绑定 (场景对齐 ROLLBACK.md 场景 0)",
    },
}

# ---- 问答夹具 (任务级恢复检查的对象; 只引用文档 ID 与版本, 不引正文) ----
QA_CASES = {
    "q-billing-east": {
        "question": "2026 年华东套餐差价怎么计费?",
        "kind": "normal",
        "note": "正常查询: 答案必须引用现行口径版本",
    },
    "q-out-of-scope": {
        "question": "把我的号码归属地转到外省, 怎么办理?",
        "kind": "out_of_scope",
        "note": "超范围: 必须拒答并指引人工/专项渠道",
    },
    "q-restricted": {
        "question": "主管专属的退款政策额度是多少?",
        "kind": "restricted",
        "note": "主管专属: 一线不可见, 必须走人工升级",
    },
}

SERVICE_SCOPE = {
    "allowed": [
        "现行口径问答 (回答附依据文档与版本)",
        "生成工单预览 (无副作用)",
        "受控确认后执行创建 (一次确认 = 至多一次外部效果)",
        "依赖故障后「稍后重试」续跑同一任务 (幂等键不变)",
        "同一请求幂等重放 (返回原工单, 不重复创建)",
    ],
    "conditional": [
        "主管专属政策 -> 自动升级人工复核 (受限内容对一线不可见)",
    ],
    "forbidden": [
        "注入故障 / 开关 / 版本包回滚等配置操作 (仅管理员登录后可用)",
        "查看内部审计 / 他人任务 / 完整遥测",
    ],
}

_STATE = {
    "registry": None, "probe": None, "tickets": None, "approvals": None,
    "policy": None, "host": None,
    "run_seq": 1801, "run_id": None, "preview": None, "approval_id": None,
    "decision_effective": None, "active_trace": None, "traces": {},
    "task_order": [], "degraded": None,
    "active_package": PKG_CURRENT,
    "package_audit": [], "fault_audit": [], "login_audit": [],
    "sessions": {}, "recovery": None, "effects_total": 0, "updated_at": None,
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mark():
    _STATE["updated_at"] = _now()


def _reset_stack():
    """全新环境: 写栈/任务/审计/包状态/会话全部归零 (回到交接初始态 rc-18.1)。"""
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
        "run_seq": 1801, "run_id": None, "preview": None,
        "approval_id": None, "decision_effective": None,
        "active_trace": None, "traces": {}, "task_order": [],
        "degraded": None, "active_package": PKG_CURRENT,
        "package_audit": [], "fault_audit": [], "login_audit": [],
        "sessions": {}, "recovery": None, "effects_total": 0,
    })


_reset_stack()
_mark()

def _degraded_mode():
    probe_on = _STATE["probe"].unavailable
    switch_on = _STATE["registry"].is_active(SWITCH_MCP_OFF)
    return {
        "fault_injected": probe_on,
        "mcp_off_active": switch_on,
        "degraded_now": probe_on or switch_on,
        "source": ("fault_injection" if probe_on
                   else ("kill_switch:MCP_OFF" if switch_on else "")),
    }


def _require_admin(x_session_token: str = Header(default=None)):
    """管理接口守卫: 未登录/会话失效一律 403 (服务端强制, 非前端藏按钮)。"""
    info = _STATE["sessions"].get(x_session_token or "")
    if not info:
        raise HTTPException(status_code=403, detail="admin_required")
    return info["operator"]


def _qa_core(question_id: str, active_package: str) -> dict:
    """问答内核 (纯函数): 按现行版本包的口径绑定作答; 只引用文档 ID 与版本。"""
    case = QA_CASES.get(question_id)
    if case is None:
        return {"status": "invalid_question",
                "message": "没有这个问题; 请从页面列出的场景中选择。",
                "citation": None}
    kind = case["kind"]
    if kind == "out_of_scope":
        return {"status": "abstained",
                "message": "该问题超出本服务范围, 我不能作答; 请联系人工/专项渠道处理。",
                "citation": None}
    if kind == "restricted":
        return {"status": "escalated",
                "message": ("该政策属主管专属权限, 一线不可见; 已升级至主管复核, "
                            "请等待人工联系。"),
                "citation": None}
    pkg = PACKAGES[active_package]
    doc = pkg["policy_active_binding"]
    citation = {"document_id": doc,
                "version_label": pkg["policy_active_version"],
                "current": bool(pkg["known_good"])}
    if pkg["known_good"]:
        message = ("已按现行口径回答; 依据文档: %s (%s)。"
                   % (doc, pkg["policy_active_version"]))
    else:
        message = ("已作答, 但依据文档: %s (%s) —— 该口径版本已废止, "
                   "疑似配置回归; 请联系值守处理, 在修复前请勿按此口径对外承诺。"
                   % (doc, pkg["policy_active_version"]))
    return {"status": "answered", "message": message, "citation": citation}


def _archive(trace):
    _STATE["traces"][trace.task_run_id] = trace
    if trace.task_run_id not in _STATE["task_order"]:
        _STATE["task_order"].append(trace.task_run_id)


def _new_task():
    """新任务 = 新的独立「外部世界」(与第 16/17 周同构: 预览封套要求外部计数 0)。"""
    registry, probe = _STATE["registry"], _STATE["probe"]
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, ticket_service=tickets, approval_service=approvals,
                   policy=policy, kill_switch=registry, write_probe=probe)
    _STATE["tickets"], _STATE["approvals"] = tickets, approvals
    _STATE["policy"], _STATE["host"] = policy, host
    _STATE["run_seq"] += 1
    run_id = f"northstar-w18-{_STATE['run_seq']}"
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
    _STATE["preview"] = None
    _STATE["approval_id"] = None
    _STATE["decision_effective"] = None
    return run_id, trace


def _read_trace(question_id: str, core: dict):
    """问答任务的九阶段链 (读路径; 效果恒 0)。"""
    _STATE["run_seq"] += 1
    run_id = f"northstar-w18-{_STATE['run_seq']}-read"
    trace = TaskTrace(run_id, kind="read")
    trace.span(STAGE_REQUEST, result="read_request",
               safe_refs={"question_id": question_id})
    trace.span(STAGE_IDENTITY, result="session_authenticated",
               safe_refs={"role": "support_agent", "tenant_id": TENANT})
    citation = core.get("citation")
    if core["status"] == "answered":
        trace.span(STAGE_RETRIEVAL, result="retrieved",
                   safe_refs={"document_id": citation["document_id"]})
        trace.span(STAGE_GENERATION, result="extractive_with_citation")
        trace.span(STAGE_VERIFICATION, result="citation_supported",
                   versions={"policy_version": _STATE["policy"].POLICY_VERSION},
                   safe_refs={"citation_current": citation["current"]})
    elif core["status"] == "abstained":
        trace.span(STAGE_RETRIEVAL, result="not_applicable",
                   not_applicable=True, safe_refs={"note": "超范围, 不检索作答"})
        trace.span(STAGE_GENERATION, result="abstained_no_content")
        trace.span(STAGE_VERIFICATION, result="scope_check:out_of_scope",
                   versions={"policy_version": _STATE["policy"].POLICY_VERSION})
    else:  # escalated
        trace.span(STAGE_RETRIEVAL, result="not_applicable",
                   not_applicable=True, safe_refs={"note": "受限内容不检索"})
        trace.span(STAGE_GENERATION, result="escalation_notice_only")
        trace.span(STAGE_VERIFICATION, result="acl_restricted_to_supervisor",
                   versions={"policy_version": _STATE["policy"].POLICY_VERSION})
    trace.span(STAGE_MCP_CALL, result="not_applicable", not_applicable=True,
               safe_refs={"note": "只读问答无写通道调用"})
    trace.span(STAGE_RECONCILIATION, result="not_applicable", not_applicable=True,
               safe_refs={"note": "0 效果, 无对账段"})
    trace.span(STAGE_RESPONSE, result=core["status"])
    trace.finish(core["status"], effects=0)
    _archive(trace)
    return trace


def _execute_create(trace, run_id, approval_id):
    """执行创建: 正常 -> action_completed; 依赖故障 -> 停在 action_preview。"""
    host, tickets = _STATE["host"], _STATE["tickets"]
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_CREATE, dict(WRITE_ARGS), run_id=run_id,
                               approval_id=approval_id, rag_terminal=RAG_TERMINAL)
    delta = tickets.ticket_count() - before
    if delta > 0:
        _STATE["effects_total"] += delta
    degraded = envelope.get("degraded") is True
    refs = {"case_id": WRITE_ARGS["case_id"], "approval_id": approval_id,
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
               versions={"tool_version": "1"}, safe_refs=refs, error_class=err)
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
        _STATE["preview"] = None
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
        _STATE["degraded"] = {"run_id": run_id, "approval_id": approval_id,
                              "reason_code": "mcp_unavailable"}
    return envelope


def _snapshot_user():
    """业务用户视角快照: 不含开关/审计/包状态等任何配置类字段。"""
    traces = [_STATE["traces"][tid].as_task_row() for tid in _STATE["task_order"]]
    active = _STATE["active_trace"]
    degraded = _degraded_mode()
    friendly = "服务正常。"
    if degraded["degraded_now"]:
        friendly = ("工单通道暂时繁忙: 您的请求已安全保存为待办预览, 不会丢失;"
                    " 可稍后点「稍后重试」续跑, 或走人工渠道。")
    return {
        "friendly_status": friendly,
        "degraded_now": degraded["degraded_now"],
        "run_id": _STATE["run_id"],
        "preview": _STATE["preview"],
        "has_approval": _STATE["approval_id"] is not None,
        "decision_effective": _STATE["decision_effective"],
        "ticket_total": _STATE["tickets"].ticket_count(),
        "effects_total": _STATE["effects_total"],
        "degraded_task": _STATE["degraded"],
        "active_task": active.as_task_row() if active else None,
        "tasks": traces,
        "service_scope": SERVICE_SCOPE,
        "updated_at": _STATE["updated_at"],
    }


def _load_manifest():
    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"manifest_version": 2,
                "note": "week-18/full-release-manifest-v2.json 未找到"}


def _snapshot_admin(operator):
    """管理员视角快照: 全量配置态 + 审计 (口令/token 永不出现在任何字段)。"""
    traces = [_STATE["traces"][tid].as_task_row() for tid in _STATE["task_order"]]
    active = _STATE["active_trace"]
    pkg = PACKAGES[_STATE["active_package"]]
    return {
        "operator": operator,
        "degraded": _degraded_mode(),
        "qa_binding": {"active_package": _STATE["active_package"],
                       "known_good": pkg["known_good"],
                       "policy_active_binding": pkg["policy_active_binding"],
                       "policy_active_version": pkg["policy_active_version"],
                       "package_note": pkg["note"]},
        "preview": _STATE["preview"],
        "run_id": _STATE["run_id"],
        "has_approval": _STATE["approval_id"] is not None,
        "decision_effective": _STATE["decision_effective"],
        "degraded_task": _STATE["degraded"],
        "ticket_total": _STATE["tickets"].ticket_count(),
        "effects_total": _STATE["effects_total"],
        "active_task": active.as_task_row() if active else None,
        "tasks": traces,
        "switch_status": _STATE["registry"].status(),
        "switch_audit": _STATE["registry"].audit_trail()[-10:],
        "package_audit": _STATE["package_audit"][-10:],
        "fault_audit": _STATE["fault_audit"][-10:],
        "login_audit": _STATE["login_audit"][-10:],
        "session_count": len(_STATE["sessions"]),
        "recovery": _STATE["recovery"],
        "release_manifest": _load_manifest(),
        "security_events": _STATE["host"].security_events,
        "updated_at": _STATE["updated_at"],
    }


app = FastAPI(title="Week 18 值守交接 · 双角色工作台")


class AskBody(BaseModel):
    question_id: str


class DecideBody(BaseModel):
    decision: str  # approve | deny


class LoginBody(BaseModel):
    operator: str
    passcode: str


class FaultBody(BaseModel):
    action: str    # inject | clear (F-18.1: MCP 写通道不可达)


class SwitchBody(BaseModel):
    action: str    # set | clear (MCP_OFF)
    reason: str = ""


class PackageBody(BaseModel):
    action: str    # rollback -> rc-18.0 | activate -> rc-18.1 (F-18.2)
    reason: str = ""


@app.get("/", include_in_schema=False)
def index():

    """前台页面 (中文, 原生 HTML/JS, 零依赖零构建; 业务用户视图为默认)。"""
    return FileResponse(_INDEX_HTML)


@app.get("/api/user/state")
def user_state():
    """业务用户状态 (无任何配置类字段)。"""
    return _snapshot_user()


@app.post("/api/user/ask")
def user_ask(body: AskBody):
    """现行口径问答: 正常 -> answered(引用版本) / 超范围 -> abstained / 受限 -> escalated。"""
    core = _qa_core(body.question_id, _STATE["active_package"])
    trace = _read_trace(body.question_id, core)
    _mark()
    return {"status": core["status"], "message": core["message"],
            "citation": core["citation"], "task_run_id": trace.task_run_id,
            "state": _snapshot_user()}


@app.post("/api/user/preview")
def user_preview():
    """生成工单预览 (无副作用: 预览不创建任何外部记录)。"""
    if _STATE["degraded"]:
        return JSONResponse(status_code=409, content={
            "error": "degraded_task_pending",
            "detail": "有一个任务停在待办预览; 请先「稍后重试」续跑同一任务,"
                      " 或联系值守处理"})
    run_id, trace = _new_task()
    host = _STATE["host"]
    envelope = host.call_write(W.TOOL_PREVIEW, dict(WRITE_ARGS),
                               run_id=run_id, rag_terminal=RAG_TERMINAL)
    trace.span(STAGE_VERIFICATION, result="policy:allowed_at_preview",
               versions={"policy_version": _STATE["policy"].POLICY_VERSION},
               safe_refs={"rag_terminal": RAG_TERMINAL})
    trace.span(STAGE_APPROVAL, result="awaiting_controlled_confirmation")
    trace.span(STAGE_MCP_CALL, result=envelope.get("status"),
               versions={"tool_version": "1"},
               safe_refs={"case_id": WRITE_ARGS["case_id"], "effects_delta": 0})
    trace.span(STAGE_RESPONSE, result="preview_presented")
    if envelope.get("status") != W.STATUS_PREVIEW:
        trace.finish("preview_rejected", effects=0)
        _archive(trace)
        _STATE["active_trace"] = None
    _STATE["preview"] = envelope
    _mark()
    return {"envelope": envelope, "state": _snapshot_user()}


@app.post("/api/user/decide")
def user_decide(body: DecideBody):
    """受控确认: approve 签发审批 / deny 记录拒绝 (正常终态)。"""
    if not _STATE["preview"] or _STATE["active_trace"] is None:
        return JSONResponse(status_code=400, content={
            "error": "no_preview", "detail": "请先「生成工单预览」"})
    run_id = _STATE["run_id"]
    trace = _STATE["active_trace"]
    if body.decision == "approve":
        approval_id = f"approval-{run_id}"
        _STATE["approvals"].issue(
            approval_id=approval_id, requester=SESSION.user_id, tenant_id=TENANT,
            run_id=run_id, tool=W.TOOL_CREATE, tool_version=W.TOOL_VERSION,
            approved_parameters=_STATE["preview"]["canonical_parameters"],
            resource_scope=_STATE["preview"]["resource_scope"],
            policy_version=_STATE["policy"].POLICY_VERSION, issued_at=_now())
        _STATE["approval_id"] = approval_id
        _STATE["decision_effective"] = "approved"
        trace.span(STAGE_APPROVAL, result="approval_issued_controlled_confirmation",
                   safe_refs={"approval_id": approval_id})
    elif body.decision == "deny":
        _STATE["approvals"].record_denial(
            denial_id=f"denial-{run_id}", requester=SESSION.user_id,
            tenant_id=TENANT, run_id=run_id,
            proposed_parameters=_STATE["preview"]["canonical_parameters"],
            decided_at=_now())
        _STATE["decision_effective"] = "denied"
        trace.span(STAGE_APPROVAL, result="user_denied",
                   safe_refs={"denial_id": f"denial-{run_id}"})
        trace.span(STAGE_RESPONSE, result="denied_presented")
        trace.finish("denied", effects=0)
        _archive(trace)
        _STATE["active_trace"] = None
        _STATE["preview"] = None
    else:
        return JSONResponse(status_code=400, content={
            "error": "bad_decision", "detail": "decision 只能是 approve 或 deny"})
    _mark()
    return {"decision_effective": _STATE["decision_effective"],
            "state": _snapshot_user()}

@app.post("/api/user/execute")
def user_execute():
    """执行创建: 正常 -> action_completed; 依赖故障 -> 停在待办预览 (效果 0)。"""
    approval_bound = (_STATE["approval_id"] is not None
                      and _STATE["approval_id"] == f"approval-{_STATE['run_id']}")
    if (not _STATE["preview"] or not approval_bound
            or _STATE["decision_effective"] != "approved"):
        return JSONResponse(status_code=400, content={
            "error": "not_ready",
            "detail": "需要先「生成预览」并选择「批准」(受控确认; 审批必须绑定当前任务)"})
    envelope = _execute_create(_STATE["active_trace"], _STATE["run_id"],
                               _STATE["approval_id"])
    _mark()
    return {"envelope": envelope, "state": _snapshot_user()}


@app.post("/api/user/retry")
def user_retry():
    """恢复后同一任务续跑 (retry_later): 同一 run_id + 同一审批 -> 恰 1 效果。"""
    task = _STATE["degraded"]
    if not task:
        return JSONResponse(status_code=400, content={
            "error": "no_degraded_task",
            "detail": "当前没有停在待办预览的任务; 请先走一次预览->批准->执行"})
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
               safe_refs={"approval_id": task["approval_id"]})
    envelope = _execute_create(trace, run_id, task["approval_id"])
    _mark()
    return {"envelope": envelope, "state": _snapshot_user()}


@app.post("/api/user/replay")
def user_replay():
    """幂等重放: 同一幂等键再执行一次 -> 返回原工单, 计数不变 (无重复效果)。"""
    if _STATE["run_id"] is None:
        return JSONResponse(status_code=400, content={
            "error": "no_task", "detail": "还没有任务; 先走一次预览->批准->执行"})
    host, tickets = _STATE["host"], _STATE["tickets"]
    before = tickets.ticket_count()
    envelope = host.call_write(W.TOOL_CREATE, dict(WRITE_ARGS),
                               run_id=_STATE["run_id"],
                               approval_id=_STATE["approval_id"],
                               rag_terminal=RAG_TERMINAL)
    unchanged = tickets.ticket_count() == before
    _mark()
    return {"envelope": envelope, "effect_count_unchanged": unchanged,
            "ticket_total": tickets.ticket_count(), "state": _snapshot_user()}

@app.post("/api/admin/login")
def admin_login(body: LoginBody):
    """值守登录: 代号 + 口令 -> 内存 token (口令只比对, 不记录不回显)。"""
    _mark()
    operator = (body.operator or "").strip()
    if body.passcode != ADMIN_PASSCODE or not operator:
        _STATE["login_audit"].append(
            {"event": "login_failed", "operator": operator or "(空)", "at": _now()})
        return JSONResponse(status_code=401, content={
            "error": "bad_credentials", "detail": "值守代号或口令不对"})
    token = "sess-" + uuid.uuid4().hex
    _STATE["sessions"][token] = {"operator": operator, "role": "admin",
                                 "at": _now()}
    _STATE["login_audit"].append({"event": "login_ok", "operator": operator,
                                  "at": _now()})
    return {"token": token, "role": "admin", "operator": operator,
            "state": _snapshot_admin(operator)}


@app.post("/api/admin/logout")
def admin_logout(operator: str = Depends(_require_admin),
                 x_session_token: str = Header(default=None)):
    _STATE["sessions"].pop(x_session_token or "", None)
    _mark()
    return {"logged_out": True}


@app.get("/api/admin/state")
def admin_state(operator: str = Depends(_require_admin)):
    """管理员全量状态: 配置态 + 审计 + 清单 (需登录)。"""
    return _snapshot_admin(operator)


@app.post("/api/admin/fault")
def admin_fault(body: FaultBody, operator: str = Depends(_require_admin)):
    """注入/解除 F-18.1 (MCP 写通道不可达) —— 封存故障包动作, 仅管理员。"""
    if body.action not in ("inject", "clear"):
        return JSONResponse(status_code=400, content={
            "error": "bad_action", "detail": "action 只能是 inject 或 clear"})
    _STATE["probe"].unavailable = (body.action == "inject")
    _STATE["fault_audit"].append({
        "event": body.action, "fault_id": "F-18.1",
        "operator": operator, "at": _now()})
    _mark()
    return {"degraded": _degraded_mode(), "state": _snapshot_admin(operator)}


@app.post("/api/admin/switch")
def admin_switch(body: SwitchBody, operator: str = Depends(_require_admin)):
    """开关 MCP_OFF 开/复位 (运营手段; operator+reason 进追加式审计)。"""
    if body.action not in ("set", "clear"):
        return JSONResponse(status_code=400, content={
            "error": "bad_action", "detail": "action 只能是 set 或 clear"})
    if not body.reason.strip():
        return JSONResponse(status_code=400, content={
            "error": "reason_required",
            "detail": "开关动作必须给出理由 (无因操作 = 未批准, 直接拒绝)"})
    try:
        if body.action == "set":
            event = _STATE["registry"].set(
                SWITCH_MCP_OFF, operator=operator, reason=body.reason)
        else:
            event = _STATE["registry"].clear(
                SWITCH_MCP_OFF, operator=operator, reason=body.reason)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={
            "error": "bad_request", "detail": str(exc)})
    _mark()
    return {"event": event, "degraded": _degraded_mode(),
            "state": _snapshot_admin(operator)}

@app.post("/api/admin/package")
def admin_package(body: PackageBody, operator: str = Depends(_require_admin)):
    """F-18.2 处置: rollback -> 已知良好 rc-18.0 (完整版本包回滚);
    activate -> 重新挂上回归包 rc-18.1 (为下一轮演练重埋 F-18.2)。"""
    if body.action not in ("rollback", "activate"):
        return JSONResponse(status_code=400, content={
            "error": "bad_action",
            "detail": "action 只能是 rollback (回滚) 或 activate (重埋回归)"})
    target = PKG_KNOWN_GOOD if body.action == "rollback" else PKG_CURRENT
    if not body.reason.strip():
        return JSONResponse(status_code=400, content={
            "error": "reason_required",
            "detail": "版本包动作必须给出理由 (无因操作未批准)"})
    previous = _STATE["active_package"]
    if previous == target:
        event = {"event": "already_active", "package": target,
                 "operator": operator, "reason": body.reason, "at": _now()}
    else:
        _STATE["active_package"] = target
        event = {"event": ("rolled_back" if body.action == "rollback"
                           else "regression_seeded"),
                 "from": previous, "to": target, "operator": operator,
                 "reason": body.reason, "at": _now()}
    _STATE["package_audit"].append(event)
    _mark()
    return {"event": event, "qa_binding": _snapshot_admin(operator)["qa_binding"],
            "state": _snapshot_admin(operator)}


@app.post("/api/admin/recovery-check")
def admin_recovery_check(operator: str = Depends(_require_admin)):
    """恢复「四+1 条任务路径」复验跑批 (对照 live 现状; 写夹具用独立外部世界)。"""
    binding = _STATE["active_package"]
    rows = []

    core1 = _qa_core("q-billing-east", binding)
    cit = core1.get("citation") or {}
    ok1 = core1["status"] == "answered" and cit.get("current") is True
    rows.append({"path": "1 正常查询", "expected": "answered + 引用现行口径版本",
                 "observed": (f"{core1['status']} "
                              f"依据={cit.get('document_id')} "
                              f"现行={cit.get('current')}"),
                 "pass": ok1, "fixture": True})

    core2 = _qa_core("q-out-of-scope", binding)
    ok2 = core2["status"] == "abstained"
    rows.append({"path": "2 正确拒答", "expected": "abstained (超出范围即拒答)",
                 "observed": core2["status"], "pass": ok2, "fixture": True})

    core3 = _qa_core("q-restricted", binding)
    ok3 = core3["status"] == "escalated"
    rows.append({"path": "3 人工升级", "expected": "escalated (主管专属走升级)",
                 "observed": core3["status"], "pass": ok3, "fixture": True})

    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, ticket_service=tickets, approval_service=approvals,
                   policy=policy, kill_switch=_STATE["registry"],
                   write_probe=_STATE["probe"])
    _STATE["run_seq"] += 1
    run_id = f"northstar-w18-rc-{_STATE['run_seq']}"
    prev = host.call_write(W.TOOL_PREVIEW, dict(WRITE_ARGS),
                           run_id=run_id, rag_terminal=RAG_TERMINAL)
    if prev.get("status") != W.STATUS_PREVIEW:
        rows.append({"path": "4 逐次批准写入", "expected": "预览可生成",
                     "observed": prev.get("status"), "pass": False,
                     "fixture": True})
        rows.append({"path": "5 重复请求与效果对账",
                     "expected": "重放不产生第 2 次效果", "observed": "未执行",
                     "pass": False, "fixture": True})
    else:
        approvals.issue(
            approval_id=f"approval-{run_id}", requester=SESSION.user_id,
            tenant_id=TENANT, run_id=run_id, tool=W.TOOL_CREATE,
            tool_version=W.TOOL_VERSION,
            approved_parameters=prev["canonical_parameters"],
            resource_scope=prev["resource_scope"],
            policy_version=policy.POLICY_VERSION, issued_at=_now())
        before = tickets.ticket_count()
        env4 = host.call_write(W.TOOL_CREATE, dict(WRITE_ARGS), run_id=run_id,
                               approval_id=f"approval-{run_id}",
                               rag_terminal=RAG_TERMINAL)
        delta = tickets.ticket_count() - before
        degraded = env4.get("degraded") is True
        ok4 = (env4.get("status") == "action_completed" and delta == 1)
        rows.append({"path": "4 逐次批准写入",
                     "expected": "action_completed 且外部效果恰 +1",
                     "observed": (f"{env4.get('status')} 效果增量={delta}"
                                  + (" (依赖故障仍在: 停在待办预览)" if degraded else "")),
                     "pass": ok4, "fixture": True})
        env5 = host.call_write(W.TOOL_CREATE, dict(WRITE_ARGS), run_id=run_id,
                               approval_id=f"approval-{run_id}",
                               rag_terminal=RAG_TERMINAL)
        ok5 = tickets.ticket_count() == before + 1
        rows.append({"path": "5 重复请求与效果对账",
                     "expected": "重放返回原工单, 计数不变",
                     "observed": (f"重放后计数={tickets.ticket_count()} "
                                  f"(重放前={before + 1})"),
                     "pass": ok5, "fixture": True})

    overall = all(row["pass"] for row in rows)
    _STATE["recovery"] = {"at": _now(), "active_package": binding,
                          "overall": ("pass" if overall else "fail"),
                          "rows": rows}
    _mark()
    return {"recovery": _STATE["recovery"], "state": _snapshot_admin(operator)}


@app.post("/api/admin/redaction-check")
def admin_redaction_check(operator: str = Depends(_require_admin)):
    """遥测脱敏检查: 本会话全部任务 span 机械扫描 (凭据/正文/载荷零出现)。"""
    spans = [sp for tid in _STATE["task_order"]
             for sp in _STATE["traces"][tid].spans]
    violations = scan_spans_for_violations(spans)
    return {"checked_events": len(spans), "violations": violations,
            "passed": not violations}


@app.post("/api/admin/reset")
def admin_reset(operator: str = Depends(_require_admin)):
    """重置: 全新交接初始态 (任务/审计/包状态归零; 全部管理员会话失效需重登)。"""
    _reset_stack()
    _mark()
    return {"reset": True, "note": "环境已重置; 所有管理员会话已失效, 请重新登录"}


@app.get("/api/admin/trace/{task_run_id}")
def admin_trace(task_run_id: str, operator: str = Depends(_require_admin)):
    """任务追踪: 任选 task_run_id 还原九阶段链 (含降级段/引用版本段)。"""
    found = _STATE["traces"].get(task_run_id)
    if found is None:
        return JSONResponse(status_code=404, content={
            "error": "not_found",
            "detail": f"本会话没有 {task_run_id} (重置会清空; 演练请重跑)"})
    return {"task": found.as_task_row(), "spans": found.spans,
            "versions": found.versions, "safe_refs": found.safe_refs,
            "state": _snapshot_admin(operator)}










"""写审批演示 (Week 14 第 3 天) —— 政策冲突后的主管复核单, 四次尝试对账。

用法: python -m mcptool.demo_write_approval
场景 (手册 3218-3303 完成品; 值为北辰教学合成材料 + 本仓库冻结语料):
  run_id=run-w14-001, 发起人=客服周宁 (可信会话), 工单=NS-1042,
  RAG 终态=escalate, 原因=两份现行政策冲突 (语料冲突组 upgrade-east-2026-04:
  upgrade-east-v2 与 upgrade-notice-v4), 唯一允许效果=创建 1 张主管复核单。
四次尝试的外部状态 (核心输出):
  只生成预览        -> action_preview   工单总数 0
  完全匹配后执行    -> 创建 SIM-5001    工单总数 1
  改队列复用原审批  -> approval_mismatch 工单总数 1
  重放相同幂等键    -> 返回原 SIM-5001  工单总数 1
确定性: 固定时钟 2026-10-08T00:00:00Z (审批 issued/expires/created_at 可复现);
写效果只发生在隔离模拟服务 TicketService 实例; 只读 case_store 零搅动。
"""
from __future__ import annotations

import json

from . import case_store
from . import write_contract as W
from .approval import ApprovalService
from .client import McpHost, TrustedSession
from .policy import WritePolicy
from .run_fixtures import APPROVAL_ID_W14, DENIAL_ID_W14, FIXED_NOW, RUN_ID_W14
from .ticket_service import TicketService

SESSION = TrustedSession("northstar-training", "user-zhou-ning", "support_agent")
RAW_ARGS = {
    "case_id": "NS-1042",
    "reason_code": "policy_conflict",           # 小写输入: 规范化演示
    "source_refs": ["upgrade-notice-v4", "upgrade-east-v2"],  # 乱序: 规范化排序
    "target_queue": "Billing-Supervisor-Review",  # 大小写: 规范化演示
}


def _emit(label: str, payload) -> None:
    print(f"== {label} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(SESSION, clock=lambda: FIXED_NOW,
                   ticket_service=tickets, approval_service=approvals,
                   policy=policy)
    rag_terminal = "escalate"

    # ---- 尝试 1: 只生成无副作用预览 (无审批) ----
    preview = host.call_write(W.TOOL_PREVIEW, RAW_ARGS,
                              run_id=RUN_ID_W14, rag_terminal=rag_terminal)
    _emit("尝试 1 | ticket.preview_create (规范化后参数, 外部效果 0)", {
        "request_arguments_raw": RAW_ARGS,
        "response": preview,
        "simulated_ticket_total": tickets.ticket_count(),
    })

    # ---- 用户在确认界面看到准确参数后拒绝过一次 (正常安全终态, 不重问) ----
    canonical, _err = W.validate_write_arguments(RAW_ARGS)
    denial = approvals.record_denial(
        denial_id=DENIAL_ID_W14, requester=SESSION.user_id,
        tenant_id=SESSION.tenant_id, run_id=RUN_ID_W14,
        proposed_parameters=canonical, decided_at=FIXED_NOW)
    _emit("用户决定 (受控确认动作, 非聊天文本) | 第一次: 拒绝", {
        "denial_record_restricted": denial,
        "note": "approval_denied 是正常终态; 编排器不得自动再次询问",
    })

    # ---- 用户随后确认 (逐次审批; 拒绝不复用, 新决定产生新审批 ID) ----
    approval = approvals.issue(
        approval_id=APPROVAL_ID_W14, requester=SESSION.user_id,
        tenant_id=SESSION.tenant_id, run_id=RUN_ID_W14,
        tool=W.TOOL_CREATE, tool_version=W.TOOL_VERSION,
        approved_parameters=canonical, resource_scope=W.resource_scope("NS-1042"),
        policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW)
    _emit("用户决定 (第二次) | 确认创建主管复核单 -> 审批记录 (restricted)", {
        "approval_record": approval,
        "binding_note": "审批绑定身份/运行/工具版本/完整参数/范围/策略/次数/有效期",
    })

    # ---- 尝试 2: 完全匹配后执行 (执行前服务端重读审批逐项比对) ----
    created = host.call_write(W.TOOL_CREATE, RAW_ARGS, run_id=RUN_ID_W14,
                              approval_id=APPROVAL_ID_W14,
                              rag_terminal=rag_terminal)
    _emit("尝试 2 | ticket.create (有效逐次审批)", {
        "response": created,
        "simulated_ticket_total": tickets.ticket_count(),
    })

    # ---- 尝试 3: 把目标队列改成 executive-review, 复用原审批 ----
    tampered = dict(RAW_ARGS, target_queue="executive-review")
    mismatch = host.call_write(W.TOOL_CREATE, tampered, run_id=RUN_ID_W14,
                               approval_id=APPROVAL_ID_W14,
                               rag_terminal=rag_terminal)
    _emit("尝试 3 | 改队列后执行 (使用原审批)", {
        "tampered_arguments": tampered,
        "response": mismatch,
        "simulated_ticket_total": tickets.ticket_count(),
    })

    # ---- 尝试 4: 以相同幂等键重放 (审批已用尽; 幂等表返回原工单) ----
    replay = host.call_write(W.TOOL_CREATE, RAW_ARGS, run_id=RUN_ID_W14,
                             approval_id=APPROVAL_ID_W14,
                             rag_terminal=rag_terminal)
    _emit("尝试 4 | 重放相同幂等键", {
        "response": replay,
        "simulated_ticket_total": tickets.ticket_count(),
    })

    # ---- 汇总: 审批与外部状态放同一个可验证结果 ----
    _emit("四次尝试的外部状态对账", {
        "run_id": RUN_ID_W14,
        "trace": [
            {"attempt": "1 只生成预览", "terminal": preview.get("status"),
             "simulated_ticket_total": 0, "note": "预览后 0"},
            {"attempt": "2 完全匹配后执行", "terminal": created.get("status"),
             "ticket_id": created.get("ticket_id"), "note": "创建后 1"},
            {"attempt": "3 改队列复用原审批", "terminal": mismatch.get("error", {}).get("code"),
             "note": "仍 1"},
            {"attempt": "4 重放相同幂等键", "terminal": replay.get("status"),
             "ticket_id": replay.get("ticket_id"), "note": "仍 1"},
        ],
        "external_ticket_count_trace": [0, 1, 1, 1],
        "final_simulated_ticket_total": tickets.ticket_count(),
        "read_case_store_immutable": case_store.check_immutable(),
        "host_audit_restricted": host.audit,
    })


if __name__ == "__main__":
    main()
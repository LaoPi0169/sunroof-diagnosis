"""模型外策略组件 (Week 14) —— ticket.create 的业务授权, 独立于模型输出。

手册 3229: RAG/生成结果不是「是否发单」的唯一依据, 策略组件还要检查四件事:
  1) 当前任务是否允许升级 —— RAG 终态必须已是 escalate (编排层传入, 只认
     确定性终态, 不认模型自述);
  2) 发起人是否有权为该对象发起复核 —— 角色允许池 + 对象同租户且角色可见
     (复用只读 case_store 的鉴权事实, 只读不写);
  3) 目标队列是否在允许范围 —— 策略允许池是 schema 语法枚举的真子集
     (executive-review 语法合法但策略不放行);
  4) 引用是否来自获准语料 —— 逐项核对第 9/12 周冻结 corpus-manifest-v1.json
     的 document_id, 且全部属于同一冲突组 (POLICY_CONFLICT 需要冲突双方)。

判定是确定性纯函数: 相同输入必得相同结论; 模型输出不参与任何一步。
预览与执行两个阶段各判定一次 (执行时角色/政策/版本可能已变)。
"""
from __future__ import annotations

import json
import os

from . import case_store

POLICY_VERSION = "ticket-create-policy-v1"
ALLOWED_QUEUES = ("billing-supervisor-review",)
ALLOWED_ROLES = ("support_agent",)          # 一线客服为本人工单发起主管复核
REQUIRED_RAG_TERMINAL = "escalate"
APPROVAL_TTL_SECONDS = 900                  # 审批有效期 15 分钟
MAX_USES_PER_APPROVAL = 1                   # 逐次审批: 一次批准一次使用
MIN_REFS_FOR_CONFLICT = 2                   # 冲突原因必须给出冲突双方

_MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "corpus-manifest-v1.json")


def _load_conflict_groups(path=None):
    """冻结语料清单 -> {document_id: conflict_group 或 ""} (只读装载一次)。"""
    with open(path or _MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {
        doc["document_id"]: doc.get("conflict_group") or ""
        for doc in manifest["documents"]
    }


class WritePolicy:
    """ticket.create 策略判定 (进程内确定性; 无网络无模型)。"""

    POLICY_VERSION = POLICY_VERSION
    APPROVAL_TTL_SECONDS = APPROVAL_TTL_SECONDS
    MAX_USES_PER_APPROVAL = MAX_USES_PER_APPROVAL
    ALLOWED_QUEUES = ALLOWED_QUEUES
    ALLOWED_ROLES = ALLOWED_ROLES
    REQUIRED_RAG_TERMINAL = REQUIRED_RAG_TERMINAL
    MIN_REFS_FOR_CONFLICT = MIN_REFS_FOR_CONFLICT

    def __init__(self, manifest_path=None):
        self._conflict_groups = _load_conflict_groups(manifest_path)

    def decide(self, *, session, canonical_parameters, rag_terminal=None):
        """返回 (allowed, reason_typed); reason_typed 只进受限审计。"""
        canonical = canonical_parameters

        if rag_terminal != REQUIRED_RAG_TERMINAL:
            return False, f"policy_rag_terminal_not_escalate:{rag_terminal}"
        if session.role not in ALLOWED_ROLES:
            return False, f"policy_role_not_allowed:{session.role}"
        if canonical["reason_code"] != "POLICY_CONFLICT":
            return False, f"policy_reason_not_allowed:{canonical['reason_code']}"
        if canonical["target_queue"] not in ALLOWED_QUEUES:
            return False, f"policy_queue_not_allowed:{canonical['target_queue']}"

        refs = canonical["source_refs"]
        if len(refs) < MIN_REFS_FOR_CONFLICT:
            return False, f"policy_ref_count_insufficient:{len(refs)}"
        groups = set()
        for ref in refs:
            group = self._conflict_groups.get(ref)
            if group is None:
                return False, f"policy_ref_unknown:{ref}"
            groups.add(group)
        if len(groups) != 1 or "" in groups:
            return False, f"policy_ref_conflict_group_mismatch:{sorted(groups)}"

        case = case_store.get_case(canonical["case_id"])
        if case is None:
            return False, "policy_case_not_found"
        if case["tenant_id"] != session.tenant_id:
            return False, "policy_case_other_tenant"
        if session.role not in case["visible_to_roles"]:
            return False, "policy_case_not_visible"
        return True, "policy_allowed"
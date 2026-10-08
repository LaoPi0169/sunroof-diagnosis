"""结构化审批服务 (Week 14) —— 审批绑定动作, 不绑定意图。

规则 (回链 week-14/approval-policy-v1.md 与手册 3258-3281):
  - 只保存确定性结构化记录; 不读取、不解析模型或聊天自然语言作为批准
    (「可以/继续吧」可能指任何事, 聊天文本不是可审计审批);
  - 审批绑定十要素: 请求人/租户/运行/工具/版本/规范化参数/资源范围/策略版本/
    最多次数/有效期 —— 任一实质变化即原审批失效;
  - 有效期 = 签发时刻 + TTL (policy.APPROVAL_TTL_SECONDS; ISO-8601 Z 定长
    格式, 字符串可直接比较);
  - record_denial 只落拒绝决定: approval_denied 是正常、安全的业务终态,
    不可执行、不可复用, 编排器不得因拒绝而自动再次询问;
  - 撤销 (执行前 revoke) 与断连对账留第 15 周; 本周失效面 = 拒绝/过期/用尽。
"""
from __future__ import annotations

import copy

from . import policy as policy_mod
from .write_contract import ISO_FORMAT, iso_plus_seconds


class ApprovalService:
    """审批记录的确定性存储 (进程内; 无网络无真实审批系统)。"""

    def __init__(self):
        self._approvals = {}

    def issue(self, *, approval_id, requester, tenant_id, run_id, tool,
              tool_version, approved_parameters, resource_scope, policy_version,
              issued_at, ttl_seconds=policy_mod.APPROVAL_TTL_SECONDS,
              maximum_uses=policy_mod.MAX_USES_PER_APPROVAL):
        """签发一条已批准的逐次审批 (仅在用户于受控确认动作中选择确认后调用)。"""
        record = {
            "record_version": "approval-record-v1",
            "approval_id": approval_id,
            "decision": "approved",
            "requester": requester,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "tool": tool,
            "tool_version": tool_version,
            "approved_parameters": copy.deepcopy(approved_parameters),
            "resource_scope": resource_scope,
            "policy_version": policy_version,
            "maximum_uses": maximum_uses,
            "uses": 0,
            "issued_at": issued_at,
            "expires_at": iso_plus_seconds(issued_at, ttl_seconds),
        }
        self._approvals[approval_id] = record
        return copy.deepcopy(record)

    def record_denial(self, *, denial_id, requester, tenant_id, run_id,
                      proposed_parameters, decided_at):
        """记录用户拒绝 (正常终态): 无 approval_id、不可执行、不可复用。"""
        record = {
            "record_version": "approval-record-v1",
            "approval_id": denial_id,
            "decision": "denied",
            "requester": requester,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "proposed_parameters": copy.deepcopy(proposed_parameters),
            "decided_at": decided_at,
        }
        self._approvals[denial_id] = record
        return copy.deepcopy(record)

    def get(self, approval_id):
        record = self._approvals.get(approval_id)
        return copy.deepcopy(record) if record is not None else None

    def status_now(self, approval_id, *, now):
        """执行器在执行前重读的「当前有效状态」(不信任预览缓存)。

        返回 (record, effective_status); effective_status ∈
        missing / denied / expired / exhausted / approved。
        """
        record = self._approvals.get(approval_id)
        if record is None:
            return None, "missing"
        if record["decision"] != "approved":
            return copy.deepcopy(record), record["decision"]  # denied
        if now > record["expires_at"]:
            return copy.deepcopy(record), "expired"
        if record["uses"] >= record["maximum_uses"]:
            return copy.deepcopy(record), "exhausted"
        return copy.deepcopy(record), "approved"

    def consume(self, approval_id) -> bool:
        """消耗一次使用额度; 超过 maximum_uses 拒绝 (不产生效果)。"""
        record = self._approvals.get(approval_id)
        if record is None or record["decision"] != "approved":
            return False
        if record["uses"] >= record["maximum_uses"]:
            return False
        record["uses"] += 1
        return True
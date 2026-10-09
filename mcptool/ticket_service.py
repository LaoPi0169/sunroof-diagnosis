"""隔离模拟工单服务 (Week 14 + Week 15 对抗增量) —— 写路径独立 store, 与只读 case_store 物理分离。

不变量 (手册 3360-3364 + state-and-effect-map 硬门):
  - 本模块不 import、不修改 mcptool.case_store (只读库 16 工单快照必须逐字节不变);
  - 每个实例自带独立 store: 演示/夹具/测试各建新实例, 运行之间互不串状态,
    结果可复现;
  - 幂等表 idempotency_key -> ticket_id: 同键重复请求返回原工单, 绝不产生新效果
    (相同幂等键最多产生一个外部效果);
  - 计数器从 SIM-5001 起 (手册完成例 SIM-5007 为北辰示例值, 两套编号互不影响);
  - 本模块是唯一写 store: 「创建复核单」只发生在这里。

Week 15 增量 (A-06/A-07):
  - 断连注入缝: create_review_ticket(inject_response_loss=True) 在提交成功后抛
    ResponseLostError (响应未返回) —— 恢复必须先按幂等键查询, 不是再次创建;
  - 对账查询不可用缝: query_outage=True 时 find_id_by_idempotency_key 抛
    QueryUnavailableError —— 编排必须停在 reconciliation_pending, 禁止伪造终态;
  - 事件链追加式: created / response_lost / reconciled / cancellation_approved /
    cancelled 全部保留, 补偿 (cancel_ticket) 不删除任何历史;
  - cancel_ticket 仅运营角色 ops_admin (OPS_CANCEL_ROLE): 不暴露给模型,
    不进入模型 MCP 工具目录/Host 允许列表 (A-07)。
"""
from __future__ import annotations

import json

_FIRST_TICKET_NUMBER = 5001  # SIM-5001 起
OPS_CANCEL_ROLE = "ops_admin"  # 运营补偿接口的唯一允许角色 (独立于模型/一线角色)


class ResponseLostError(Exception):
    """断连夹具 (A-06): 写已提交成功、响应在返回前丢失。"""

    def __init__(self, ticket_id: str, idempotency_key: str):
        super().__init__(f"response lost after commit: {ticket_id}")
        self.ticket_id = ticket_id
        self.idempotency_key = idempotency_key


class QueryUnavailableError(Exception):
    """对账查询不可用夹具 (A-06 第二段): 连幂等键查询都无法完成。"""


class McpUnavailableError(Exception):
    """MCP 写通道不可用 (Week 17 降级缝): 依赖故障 —— 编排层必须停在预览、
    外部效果恒 0、绝不自动重试创建 (手册 3924/3961-3966)。"""


class TicketService:
    """隔离模拟服务的一个实例 = 一个独立的「外部系统」。"""

    def __init__(self):
        self._tickets = {}        # ticket_id -> record
        self._idempotency = {}    # idempotency_key -> ticket_id
        self._counter = 0
        self.query_outage = False  # 注入缝: True 时对账查询不可用

    def create_review_ticket(self, *, case_id, reason_code, source_refs,
                             target_queue, idempotency_key, requested_by,
                             tenant_id, created_at, inject_response_loss=False):
        """创建一张主管复核单; 幂等键已存在时返回原工单且 created=False。

        返回 (ticket_id, created); 调用方 (执行器) 之后必须用 get_ticket
        独立核验, 不以本返回值作为完成证据。
        inject_response_loss=True (A-06 夹具): 提交成功后抛 ResponseLostError ——
        工单已存在、事件链记 response_lost, 调用方拿不到任何返回值。
        """
        existing = self._idempotency.get(idempotency_key)
        if existing is not None:
            return existing, False
        self._counter += 1
        ticket_id = f"SIM-{_FIRST_TICKET_NUMBER + self._counter - 1}"
        record = {
            "ticket_id": ticket_id,
            "state": "open",
            "case_id": case_id,
            "reason_code": reason_code,
            "source_refs": list(source_refs),
            "target_queue": target_queue,
            "requested_by": requested_by,
            "tenant_id": tenant_id,
            "created_at": created_at,
            "events": [{"event": "created", "at": created_at,
                        "by": requested_by}],
        }
        self._tickets[ticket_id] = record
        self._idempotency[idempotency_key] = ticket_id
        if inject_response_loss:
            record["events"].append({
                "event": "response_lost", "at": created_at,
                "reason": "commit_succeeded_response_not_returned"})
            raise ResponseLostError(ticket_id, idempotency_key)
        return ticket_id, True

    def get_ticket(self, ticket_id):
        """独立核验读取 (返回副本, 外部无法借引用改写 store)。"""
        record = self._tickets.get(ticket_id)
        return dict(record) if record is not None else None

    def find_id_by_idempotency_key(self, idempotency_key):
        """按幂等键查询外部事实 (A-06 恢复第一动作; 查询不可用即抛错)。"""
        if self.query_outage:
            raise QueryUnavailableError("reconciliation query unavailable")
        return self._idempotency.get(idempotency_key)

    def mark_reconciled(self, ticket_id, *, run_id, at):
        """对账完成标记 (追加式事件; 同一 run 幂等, 不重复追加)。"""
        record = self._tickets.get(ticket_id)
        if record is None:
            return None
        for event in record["events"]:
            if event.get("event") == "reconciled" and event.get("run_id") == run_id:
                return dict(record)
        record["events"].append({"event": "reconciled", "at": at, "run_id": run_id})
        return dict(record)

    def cancel_ticket(self, ticket_id, *, operator_role, reason, cancelled_at):
        """运营补偿取消 (A-07): 仅 ops_admin; append-only, 不删除任何历史。

        返回 (record, None) 或 (None, reason_typed); reason_typed ∈
        ticket_not_found / role_not_allowed:<role> / already_cancelled。
        本方法不在任何模型工具目录/Host 允许列表中 —— 补偿不是模型能力。
        """
        record = self._tickets.get(ticket_id)
        if record is None:
            return None, "ticket_not_found"
        if operator_role != OPS_CANCEL_ROLE:
            return None, f"role_not_allowed:{operator_role}"
        if record["state"] == "cancelled":
            return None, "already_cancelled"
        record["state"] = "cancelled"
        record["events"].append({"event": "cancellation_approved",
                                 "at": cancelled_at, "operator_role": operator_role,
                                 "reason": reason})
        record["events"].append({"event": "cancelled", "at": cancelled_at,
                                 "operator_role": operator_role, "reason": reason})
        return dict(record), None

    def ticket_count(self) -> int:
        """外部效果计数: 工单总数 (预览/执行前后的差值即新增效果数)。"""
        return len(self._tickets)

    def snapshot(self) -> str:
        """store 快照 (排序 JSON): 运行前后对比用。"""
        return json.dumps(self._tickets, sort_keys=True, ensure_ascii=False)


class UnavailableWriteTickets(TicketService):
    """深层注入缝 (Week 17): 写通道不可达 —— create 一律抛 McpUnavailableError。

    主缝在编排层前置探测 (client.py 注入的 WriteChannelProbe.check(), 服务端
    不被打扰、审批不消耗); 本子类是纵深防御的第二条缝: 若编排层探测被绕过,
    依赖在服务端深处失败 —— 此时审批额度已在 create 第 5 步消耗 (先消耗后写),
    降级封套照常 0 效果, 恢复后续跑需重新确认审批 (幂等键不变, 绝不产生
    第二个效果)。
    """

    def create_review_ticket(self, **kwargs):
        raise McpUnavailableError("mcp write channel unavailable (deep seam)")
"""隔离写服务 (Week 14) —— TicketService: 唯一写点 + 幂等表 + 效果计数。

本实例是课程的唯一「外部世界」: 外部效果 = 本服务计数; 预览封套外部计数恒 0。
"""
from __future__ import annotations


class McpUnavailableError(RuntimeError):
    """依赖不可用注入缝 (第 17 周降级演练使用)。"""


class TicketService:
    def __init__(self):
        self._tickets = {}
        self._idempotency = {}
        self._external_effects = 0
        self._available = True

    def set_available(self, available: bool) -> None:
        self._available = available

    def ticket_count(self) -> int:
        return len(self._tickets)

    def external_effects(self) -> int:
        return self._external_effects

    def get_ticket(self, ticket_id: str):
        return self._tickets.get(ticket_id)

    def available(self) -> bool:
        return self._available

    def peek_idempotency(self, idempotency_key: str):
        """只读窥探幂等表 (供执行器在消耗额度前判重放)。"""
        return self._idempotency.get(idempotency_key)

    def create_review_ticket(self, case_id: str, queue: str, reason_code: str,
                             source_refs: list, idempotency_key: str) -> dict:
        """幂等写入: 相同幂等键最多一个外部效果; 重放返回原工单不消耗额度。"""
        if not self._available:
            raise McpUnavailableError("ticket service unavailable")
        if idempotency_key in self._idempotency:
            return {"ticket_id": self._idempotency[idempotency_key],
                    "replayed": True, "created": False}
        ticket_id = f"SIM-{5000 + len(self._tickets) + 1}"
        self._tickets[ticket_id] = {
            "ticket_id": ticket_id, "case_id": case_id, "queue": queue,
            "reason_code": reason_code, "source_refs": list(source_refs),
            "status": "open",
        }
        self._idempotency[idempotency_key] = ticket_id
        self._external_effects += 1
        return {"ticket_id": ticket_id, "replayed": False, "created": True}

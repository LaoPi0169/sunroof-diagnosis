"""隔离模拟工单服务 (Week 14) —— 写路径独立 store, 与只读 case_store 物理分离。

不变量 (手册 3360-3364 + state-and-effect-map 硬门):
  - 本模块不 import、不修改 mcptool.case_store (只读库 16 工单快照必须逐字节不变);
  - 每个实例自带独立 store: 演示/夹具/测试各建新实例, 运行之间互不串状态,
    结果可复现;
  - 幂等表 idempotency_key -> ticket_id: 同键重复请求返回原工单, 绝不产生新效果
    (相同幂等键最多产生一个外部效果);
  - 计数器从 SIM-5001 起 (手册完成例 SIM-5007 为北辰示例值, 两套编号互不影响);
  - 本模块是唯一写 store: 「创建复核单」只发生在这里, 可取消 (可逆) 由独立
    运营路径完成 —— 取消不是模型可用能力 (第 15 周)。
"""
from __future__ import annotations

import json

_FIRST_TICKET_NUMBER = 5001  # SIM-5001 起


class TicketService:
    """隔离模拟服务的一个实例 = 一个独立的「外部系统」。"""

    def __init__(self):
        self._tickets = {}        # ticket_id -> record
        self._idempotency = {}    # idempotency_key -> ticket_id
        self._counter = 0

    def create_review_ticket(self, *, case_id, reason_code, source_refs,
                             target_queue, idempotency_key, requested_by,
                             tenant_id, created_at):
        """创建一张主管复核单; 幂等键已存在时返回原工单且 created=False。

        返回 (ticket_id, created); 调用方 (执行器) 之后必须用 get_ticket
        独立核验, 不以本返回值作为完成证据。
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
        }
        self._tickets[ticket_id] = record
        self._idempotency[idempotency_key] = ticket_id
        return ticket_id, True

    def get_ticket(self, ticket_id):
        """独立核验读取 (返回副本, 外部无法借引用改写 store)。"""
        record = self._tickets.get(ticket_id)
        return dict(record) if record is not None else None

    def find_id_by_idempotency_key(self, idempotency_key):
        return self._idempotency.get(idempotency_key)

    def ticket_count(self) -> int:
        """外部效果计数: 工单总数 (预览/执行前后的差值即新增效果数)。"""
        return len(self._tickets)

    def snapshot(self) -> str:
        """store 快照 (排序 JSON): 运行前后对比用。"""
        return json.dumps(self._tickets, sort_keys=True, ensure_ascii=False)
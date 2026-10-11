"""结构化审批服务 (Week 14) —— 只存确定性结构化记录, 不读自然语言。"""
from __future__ import annotations

import time


class ApprovalService:
    def __init__(self, ttl_seconds: int = 900, max_uses: int = 1):
        self._records = {}
        self._denials = []
        self._ttl = ttl_seconds
        self._max_uses = max_uses
        self._now = lambda: time.time()

    def set_clock(self, now_fn) -> None:
        self._now = now_fn

    def issue(self, approver: str, binding: dict) -> str:
        approval_id = f"APR-{len(self._records) + 1:04d}"
        self._records[approval_id] = {
            "approval_id": approval_id, "approver": approver,
            "binding": dict(binding), "issued_at": self._now(),
            "uses_left": self._max_uses,
        }
        return approval_id

    def deny(self, approver: str, reason_code: str) -> dict:
        denial = {"approver": approver, "reason_code": reason_code,
                  "denied_at": self._now()}
        self._denials.append(denial)
        return denial

    def check(self, approval_id: str, binding: dict) -> dict:
        """执行前重读 + 绑定比对 (存在/TTL/十要素); 额度用尽单独在写入前判定
        (重放先于用尽 —— 合法重放不被误拒)。"""
        record = self._records.get(approval_id)
        if record is None:
            return {"ok": False, "reason": "approval_denied"}
        if self._now() - record["issued_at"] > self._ttl:
            return {"ok": False, "reason": "approval_denied"}
        if record["binding"] != binding:
            return {"ok": False, "reason": "approval_mismatch"}
        return {"ok": True, "record": record}

    def has_uses(self, approval_id: str) -> bool:
        return self._records.get(approval_id, {}).get("uses_left", 0) > 0

    def consume(self, approval_id: str) -> None:
        if approval_id in self._records:
            self._records[approval_id]["uses_left"] -= 1

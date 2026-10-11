"""关闭开关登记表 (Week 17) —— 封闭枚举 + 追加式审计 + 快照不可变。

操作纪律: 无因操作未批准 (operator + reason 必填); 判定优先级 = 权限判定优先于降级。
"""
from __future__ import annotations

SWITCHES = ("MCP_OFF", "MODEL_OFF", "INDEX_STALE")


class KillSwitchRegistry:
    def __init__(self):
        self._state = {name: False for name in SWITCHES}
        self._audit = []

    def set_state(self, switch: str, on: bool, operator: str, reason: str) -> dict:
        if switch not in SWITCHES:
            raise ValueError(f"非法开关: {switch}")
        if not operator or not reason:
            raise ValueError("无因操作未批准: operator 与 reason 必填")
        self._state[switch] = bool(on)
        entry = {"switch": switch, "on": bool(on), "operator": operator,
                 "reason": reason, "seq": len(self._audit) + 1}
        self._audit.append(entry)  # 追加式: 历史绝不物理删除
        return dict(entry)

    def is_off(self, switch: str) -> bool:
        return self._state.get(switch, False)

    def audit(self) -> list:
        return [dict(entry) for entry in self._audit]

    def snapshot(self) -> dict:
        return {name: self._state[name] for name in SWITCHES}

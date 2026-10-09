"""关闭开关登记 (Week 17 安全降级) —— 手册 3946「关闭开关」列的最小运营实现。

回链: week-17/failure-behavior-contract-v1.md (三类故障 × 关闭开关列) +
specs/business-background.md 3889-4004 (第 17 周: 让依赖故障进入安全降级路径)。

定位与边界:
  - 开关是**运营手段** (人工批准的依赖隔离), 与**演练注入** (run_degradation_drill /
    工作台故障按钮) 是两条不同的路, 但两条路必须落进**同一个安全终态**
    (写路径 action_preview / reason_code=mcp_unavailable);
  - 本模块只做登记与审计: set/clear 每次动作落追加式审计 (operator/reason/at),
    不提供任何「自动复位」「定时恢复」—— 复位必须按批准流程由人工执行
    (recovery-verification-v1.md 检查 #1);
  - 三个开关本周只把 MCP_OFF 接进写路径编排 (mcptool/client.py); MODEL_OFF /
    INDEX_STALE 完成登记并用于固定演练 (固定演练中的读路径门), 不改生产读路径
    —— 手册 3968: 不要在同一周独立重写三套恢复机制;
  - 注册表实例自带独立审计: 演示/工作台/测试各建新实例, 互不串状态;
  - 本模块不 import 任何 mcptool 组件 (纯数据登记, 零外部效果)。
"""
from __future__ import annotations

# ---- 开关词表 (封闭枚举; 未知开关一律拒绝) ----
SWITCH_MCP_OFF = "MCP_OFF"          # MCP 写通道停用 -> ticket.create 停在 action_preview
SWITCH_MODEL_OFF = "MODEL_OFF"      # 生成段停用 (固定演练; 生产接线不在本周范围)
SWITCH_INDEX_STALE = "INDEX_STALE"  # 索引过期登记 (固定演练; 语料治理归第 10/11 周)
SWITCH_NAMES = (SWITCH_MCP_OFF, SWITCH_MODEL_OFF, SWITCH_INDEX_STALE)

# 每个开关的作用域与降级终态 (工作台页面/合同文档同源展示)
SWITCH_PURPOSES = {
    SWITCH_MCP_OFF: {
        "scope": "ticket.create 写执行 (预览/查询不受阻; 权限判定优先于开关)",
        "safe_terminal": "action_preview",
        "reason_code": "mcp_unavailable",
        "wired": True,
    },
    SWITCH_MODEL_OFF: {
        "scope": "固定演练中的生成段门 (本周不改生产读路径)",
        "safe_terminal": "abstained",
        "reason_code": "model_unavailable",
        "wired": False,
    },
    SWITCH_INDEX_STALE: {
        "scope": "固定演练中的索引新鲜度门 (本周不改生产读路径)",
        "safe_terminal": "abstained",
        "reason_code": "index_stale",
        "wired": False,
    },
}


class KillSwitchRegistry:
    """一张追加式开关登记表: set/clear/is_active + 完整审计轨迹。

    - set/clear 必须给出 operator 与 reason (无因操作 = 未批准, 直接拒绝);
    - 未知开关拒绝 (封闭枚举);
    - 审计事件只追加, 不删除不改写; snapshot 返回副本 (外部无法借引用改写);
    - 同一开关重复 set 幂等 (不重复落「opened」事件, 返回现状)。
    """

    def __init__(self, *, clock=None):
        import time
        self._clock = clock or (lambda: time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._active = set()
        self._audit = []  # 追加式: opened/closed 事件

    # ---- 内部: 校验开关名 ----
    def _check(self, name: str) -> str:
        if name not in SWITCH_NAMES:
            raise ValueError(f"未知关闭开关: {name} (允许: {SWITCH_NAMES})")
        return name

    def set(self, name: str, *, operator: str, reason: str) -> dict:
        """打开开关 (人工批准的依赖隔离); 返回审计事件。"""
        name = self._check(name)
        if not operator or not reason:
            raise ValueError("开关动作必须给出 operator 与 reason (无因操作未批准)")
        if name in self._active:
            return {"event": "already_open", "switch": name,
                    "operator": operator, "at": self._clock()}
        self._active.add(name)
        event = {"event": "opened", "switch": name, "operator": operator,
                 "reason": reason, "at": self._clock()}
        self._audit.append(event)
        return event

    def clear(self, name: str, *, operator: str, reason: str) -> dict:
        """按批准流程复位开关 (恢复复验检查 #1); 返回审计事件。"""
        name = self._check(name)
        if not operator or not reason:
            raise ValueError("复位必须给出 operator 与 reason (批准流程留痕)")
        if name not in self._active:
            return {"event": "already_closed", "switch": name,
                    "operator": operator, "at": self._clock()}
        self._active.discard(name)
        event = {"event": "closed", "switch": name, "operator": operator,
                 "reason": reason, "at": self._clock()}
        self._audit.append(event)
        return event

    def is_active(self, name: str) -> bool:
        name = self._check(name)
        return name in self._active

    @property
    def active_switches(self) -> tuple:
        return tuple(sorted(self._active))

    def audit_trail(self) -> list:
        """审计轨迹 (副本): 细分操作者/原因属运营受限审计, 不进遥测。"""
        return [dict(event) for event in self._audit]

    def status(self) -> dict:
        """工作台/演练展示面: 每个开关的 当前状态 + 作用域 (无敏感字段)。"""
        out = {}
        for name in SWITCH_NAMES:
            out[name] = {
                "active": name in self._active,
                "scope": SWITCH_PURPOSES[name]["scope"],
                "safe_terminal": SWITCH_PURPOSES[name]["safe_terminal"],
                "reason_code": SWITCH_PURPOSES[name]["reason_code"],
                "wired": SWITCH_PURPOSES[name]["wired"],
            }
        return out


class WriteChannelProbe:
    """MCP 写通道不可达注入缝 (演练手段, 与开关 MCP_OFF 平行的另一条路)。

    - check() 在写编排调用服务端之前执行: unavailable=True 时抛
      McpUnavailableError -> 编排层降级到 action_preview (同一安全终态);
    - attempts 计数器用于证明「不调用写入重试」: 一次用户动作 = 恰一次探测,
      编排层绝不循环重试 (测试断言 attempts == 1);
    - 教学模拟: 真实系统此处是网络/进程健康探测; 本类是确定性手动开关。
    """

    def __init__(self):
        self.unavailable = False
        self.attempts = 0

    def check(self) -> None:
        self.attempts += 1
        if self.unavailable:
            from .ticket_service import McpUnavailableError
            raise McpUnavailableError("mcp write channel unreachable (injected)")

# 关闭开关登记 v1（kill-switch-registry-v1，第 17 周）

- 日期: 2026-10-09 · 实现: `mcptool/kill_switch.py`（`KillSwitchRegistry` /
  `WriteChannelProbe`）· 接线: `mcptool/client.py` `_degradation_gate`
- 回链: `week-17/failure-behavior-contract-v1.md`（「关闭开关」列）+
  `specs/business-background.md` 3944-3946（合同表）+ 3973（恢复检查 #1）

## 一、开关词表（封闭枚举，未知开关直接拒绝）

| 开关 | 作用域 | 激活后的安全终态 | 本周是否接线 |
| --- | --- | --- | --- |
| `MCP_OFF` | `ticket.create` 写执行（预览/核验查询不受阻） | `action_preview` / `reason_code=mcp_unavailable` | **是**（写编排前置判定） |
| `MODEL_OFF` | 生成段（读路径） | `abstained` / `reason_code=model_unavailable` | 否——仅登记 + 演练门（本周不重写读路径恢复机制） |
| `INDEX_STALE` | 检索新鲜度门（读路径） | `abstained` / `reason_code=index_stale` | 否——仅登记 + 演练门（语料治理归第 10/11 周） |

## 二、操作纪律（登记表行为，代码强制）

1. **无因操作未批准**：`set` / `clear` 必须给出 `operator` 与 `reason`，
   缺一即 `ValueError`（代码层拒绝「无因开关」）；
2. **追加式审计**：每次动作落 `{event: opened|closed, switch, operator,
   reason, at}`，只追加不删除不改写；`audit_trail()` 返回副本（外部无法借
   引用改写，test_02 锁定）；细分操作者/理由属运营受限审计，**不进遥测**；
3. **重复 set 幂等**：已激活再 `set` 返回 `already_open`，不重复落审计；
   复位未激活开关返回 `already_closed`；
4. **复位必须按批准流程**：`clear` 是恢复复验检查 #1 的动作，理由栏写明
   批准依据（演练记录 `degradation-drill-records-v1.md` D-05b 留有样例）；
5. **实例隔离**：演示/工作台/测试各建新注册表，互不串状态。

## 三、两条降级路，同一安全终态（与演练注入的关系）

| | 关闭开关 `MCP_OFF`（运营手段） | 故障注入 `WriteChannelProbe`（演练手段） |
| --- | --- | --- |
| 谁触发 | 值班/运营人工批准 | 演练者/工作台按钮（教学模拟） |
| 作用点 | 编排层前置判定（服务端不被打扰，`server_calls` 不增） | 编排层前置探测（同左）；深层缝 `UnavailableWriteTickets` 兜底 |
| 用户面结果 | `action_preview` / `mcp_unavailable` | 完全相同 |
| 审批 | 不消耗 | 不消耗（深层缝消耗但如实入账，幂等键兜底） |
| 审计 | 开关注册表 opened/closed | Host 受限审计 `reason_typed=mcp_unavailable:fault_injection:write_probe` |

## 四、审计样例（来自固定演练 D-02/D-04/D-05b，固定时钟 2026-10-09T00:00:00Z）

```json
{"event": "opened", "switch": "INDEX_STALE", "operator": "ops-li-w17",
 "reason": "演练：索引新鲜度存疑，隔离索引依赖（第 17 周 D-02）", "at": "2026-10-09T00:00:00Z"}
{"event": "closed", "switch": "INDEX_STALE", "operator": "ops-li-w17",
 "reason": "按批准流程复位：语料新鲜度复核通过（演练留痕）", "at": "2026-10-09T00:00:00Z"}
{"event": "opened", "switch": "MCP_OFF", "operator": "ops-li-w17",
 "reason": "演练：隔离 MCP 写通道（第 17 周固定演练 D-04）", "at": "2026-10-09T00:00:00Z"}
{"event": "closed", "switch": "MCP_OFF", "operator": "ops-li-w17",
 "reason": "按批准流程复位：演练完成，恢复复验前置于续跑（D-05b）", "at": "2026-10-09T00:00:00Z"}
```

## 五、工作台入口（业务用户可测试）

`workbench_degradation/` 页面「环境面板」展示三开关状态（激活/作用域/安全终态/
是否接线），「故障路 B」按钮开/复位 `MCP_OFF`，「开关审计」表实时显示动作轨迹；
操作步骤白话版见 `week-17/degradation-plain-guide-v1.md`。

## 六、边界

1. 本登记表是**进程内教学实现**：真实系统的开关要有持久化、双人在岗批准、
   复位冷静期与告警联动 —— 本周交付的是登记纪律与审计面，不是生产开关工程；
2. 开关不是回滚：`MCP_OFF` 只隔离写通道依赖，版本回滚仍走第 8 周发布回滚证据；
3. `MODEL_OFF`/`INDEX_STALE` 的生产接线（读路径降级编排）不在本周范围
   （手册 3968：不要在同一周独立重写三套恢复机制）。
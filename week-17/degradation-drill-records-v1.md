# 降级固定演练记录 v1（degradation-drill-records-v1，第 17 周）

- 日期: 2026-10-09 · 运行环境: 本地确定性组件（固定时钟 2026-10-09T00:00:00Z，
  教学模拟，全部结果可复现）
- 运行器: `python -m mcptool.run_degradation_drill --out
  week-17/degradation-drill-evidence-v1.jsonl` · 证据: 86 行 jsonl
  （73 段 span + 8 条演练行 + 1 条 SLI 汇总 + 4 条开关审计）
- 总判定: **pass**（8/8 段全过；遥测脱敏 73 段 0 违规；
  `allowed_terminal_rate` = 7/8（唯一非允许终态是 D-01 诚实失败）；
  `forbidden_effect_count` = 0；`unconfirmed_effects` = 0；外部效果合计恰 3）

## 一、三类故障演练结果（手册 3921-3924 完成例对照）

| 段 | 故障 | 路径 | task_run_id | 可见终态 | 效果 | 手册期望终态 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D-01 | 模型超时 | 读路径 deadline 注入（0.65s > 500ms） | northstar-w17-0001 | `system_failed` | 0 | partial / search_only（本仓库等价机制：晚到结果整体作废，不生成未经验证的答案） | pass |
| D-02 | 索引过期 | INDEX_STALE 开关 -> 演练门 | northstar-w17-0002 | `abstained` | 0 | abstained（「当前资料无法确认」+ 知识所有者入口 corpus-governance） | pass |
| D-03 | MCP 不可用·注入 | WriteChannelProbe 探测不可达 | northstar-w17-0003 | `action_preview` | 0 | action_preview | pass |
| D-04 | MCP 不可用·开关 | MCP_OFF（审批不消耗；负例：无审批执行 -> approval_required） | northstar-w17-0004 | `action_preview` | 0 | action_preview | pass |

## 二、MCP 不可用降级关键证据（D-03/D-04 逐项对照手册 3961-3966）

| 手册要求 | 证据 |
| --- | --- |
| 输入：已生成、未执行的工单预览 | D-03 先 `ticket.preview_create`（policy allowed，0 效果）再注入 |
| 任务停在 action_preview | 降级封套 `status=action_preview, degraded=true`；trace finish `action_preview_held` -> 可见终态 `action_preview` |
| 不调用写入重试 | `probe.attempts=1`（单次探测）；编排无重试循环；`server_calls` 不增（服务端不被打扰） |
| external_effect_count = 0 | 封套 `external_effect_count=0`；工单 store 快照前后逐字节一致（ticket_count 0 -> 0） |
| 用户能复制预览或回人工流程 | `canonical_parameters` 原样保留 + `allowed_next_actions=[copy_preview, submit_manually, retry_later]` |
| trace 记录 mcp_unavailable | mcp_call 段 `error_class=mcp_unavailable` + safe_refs `degraded/trigger/allowed_next_actions` |
| 权限拒绝保持拒绝（手册 3951） | D-04 负例：MCP_OFF 激活 + 无审批执行 -> `approval_required`（开关不吞权限错误） |

## 三、恢复复验（D-05，详见 recovery-verification-v1.md）

| 段 | 场景 | task_run_id | 终态 | 效果 | 关键证据 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| D-05a | 注入路恢复：解除注入 -> 同一 run_id + 同一审批续跑 | northstar-w17-0003 | `action_completed` | 1 | `verified_by=ticket.get`；幂等重放返回同一工单 SIM-5001，计数仍 1 | pass |
| D-05b | 开关路恢复：按批准复位 MCP_OFF（审计留痕）-> 续跑 | northstar-w17-0004 | `action_completed` | 1 | 复位事件 `{event: closed, operator: ops-li-w17}` | pass |
| D-05c | 恢复后受限任务仍拒绝 | northstar-w17-0007 | `approval_denied` | 0 | executive-review 策略外队列照常拒绝（权限不放宽） | pass |
| D-06 | 第 15 周 A-06 断连对账回归 | northstar-w17-0008 | `action_completed` | 1 | result_unknown -> 按幂等键先查后说 -> 独立核验 | pass |

## 四、SLI 汇总（8 任务演练窗口；分母 = 演练 task_run_id，与金丝雀 60 例永不合并）

- `allowed_terminal_rate` = 7/8（0.875）—— D-01 `system_failed` 诚实失败不计入分子；
- 终态分布：`action_preview`=2、`action_completed`=3、`abstained`=1、
  `approval_denied`=1、`system_failed`=1；非终态 0；
- `forbidden_effect_count` = 0；`unconfirmed_effects` = 0；效果合计恰 3
  （两次恢复续跑 + 一次对账确认，全部 `verified_by=ticket.get`）。

## 五、诚实边界

1. 这是**固定剧本的确定性演练**，不是混沌工程：故障点是确定性注入缝
   （deadline 注入 / 开关门 / 探测缝），不是真实网络/进程故障；
2. 模型超时行的「等价机制」映射（partial/search_only -> 本仓库
   deadline_exceeded -> system_failed）是**纪律等价**（无验证不出答案、
   不调写工具），不是终端态复刻 —— 本仓库读路径无 partial 产物态；
3. 索引过期行复用既有 abstain 安全机制 + 登记开关强制触发，不是新的
   索引新鲜度检测器（语料治理属第 10/11 周）；
4. 演练窗口的 7/8 是演示口径，不是 SLO 达标声明。
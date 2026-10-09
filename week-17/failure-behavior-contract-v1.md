# 故障行为合同 v1（failure-behavior-contract-v1，第 17 周）

- 日期: 2026-10-09 · 回链: `specs/business-background.md` 3889-4004（第 17 周：
  让依赖故障进入安全降级路径）
- 实现: `mcptool/kill_switch.py`（关闭开关）+ `mcptool/client.py`
  （`_degradation_gate` / `_degraded_preview` 降级编排）+
  `mcptool/run_degradation_drill.py`（固定演练）+
  `workbench_degradation/`（业务用户工作台）
- 演练证据: `week-17/degradation-drill-evidence-v1.jsonl`（86 行，已过脱敏扫描）

## 一、三类依赖故障 × 七列表（行为合同正文）

| 依赖 / 故障 | 影响的用户任务 | 可重试？ | 安全终态 | 用户下一步 | 关闭开关 | 恢复验证 |
| --- | --- | --- | --- | --- | --- | --- |
| 模型超时 | 生成回答（读路径） | 瞬时只读失败可限时重试 | `system_failed`（手册完成例 `partial`/`search_only` 的本仓库等价机制：读路径 deadline 500ms -> 晚到结果整体作废，不生成未经验证的答案） | 稍后再试或转人工 | MODEL_OFF（登记，演练门） | 重跑读任务，答案必须仍是「无验证不出」 |
| 索引过期 | 检索依据（读路径） | 否（先用旧索引 = 装作正常） | `abstained`（「当前资料无法确认」+ 知识所有者入口 corpus-governance） | 等语料治理更新，或走人工 | INDEX_STALE（登记，演练门） | 语料新鲜度复核通过后，检索/生成恢复正常 |
| MCP 不可用 | 创建主管复核单（写路径） | **否，先保留预览**（绝不自动重试写） | `action_preview` | 复制预览手工提交 / 稍后重试（retry_later） | **MCP_OFF（已接线）** | 按批准复位后：重跑预览与一次获批创建；同一幂等键续跑恰 1 效果 |

## 二、MCP 不可用降级封套合同（用户面，v2 用户面增补）

输入：已生成、但尚未执行的工单预览；注入：MCP 写通道不可达（演练探测缝
`WriteChannelProbe` 或开关 `MCP_OFF` 两条路，落**同一**安全终态）。

```json
{
  "status": "action_preview",
  "degraded": true,
  "reason_code": "mcp_unavailable",
  "message": "写通道暂不可用：工单预览已保留但尚未创建；可复制预览走人工流程提交，或稍后重试",
  "canonical_parameters": { "…": "与预览逐字节一致（预览即合同）" },
  "resource_scope": "case:NS-1042",
  "allowed_next_actions": ["copy_preview", "submit_manually", "retry_later"],
  "idempotency_key": "<task_run_id>:ticket.create:1",
  "external_effect_count": 0,
  "external_ticket_count_before": 0,
  "external_ticket_count_after": 0
}
```

硬性可观察结果（手册 3961-3966 逐条）：
1. 任务停在 `action_preview`（遥测终态名 `action_preview`，outcome
   `action_preview_held`）；2. **不调用写入重试**（探测恰 1 次，编排无重试循环）；
3. `external_effect_count = 0`（工单 store 快照逐字节不变）；4. 用户能复制预览
（`canonical_parameters` 原样保留）或回到人工流程；5. trace 中记录
`mcp_unavailable`（mcp_call 段 error_class + degraded/trigger 安全引用）。

**审批语义**：主缝（编排层前置探测）在消耗审批之前降级 —— 审批不消耗，
恢复后同一审批直接续跑。纵深防御的深层缝（服务端深处失败
`UnavailableWriteTickets`）发生在「先消耗后写」之后 —— 用量如实入账，
恢复后需重新确认审批，幂等键保证绝无第二个效果（见 test_06）。

## 三、判定优先级（决策规则的代码化）

```
允许列表 -> 能力清单门 -> schema 封闭校验 -> [审批当前有效?]
   -> 降级判定（注入 / MCP_OFF） -> 服务端（审批/策略/幂等/写入）
```

四类错误 -> 动作映射（手册 3909/3949-3953 决策规则，权限拒绝保持拒绝）：

| 分类 | 本仓库错误码（读五类 / 写十类中对应） | 动作 | 降级可否吞掉 |
| --- | --- | --- | --- |
| 瞬时只读失败 | `deadline_exceeded`（读） | 限时重试（重试有次数与时限，晚到结果作废） | 否（保持类型化失败） |
| 确定性输入错误 | `invalid_argument` / `contract_violation` / `version_mismatch` | 修正输入，不自动重试 | 否 |
| 权限拒绝 | `not_available` / `approval_required` / `approval_expired` / `approval_mismatch` / `approval_revoked` | 保持拒绝，不切换宽松备用路径 | **否（照常走服务端拿确定性错误）** |
| 结果不明确 | `result_unknown` -> `reconciliation_pending`（写后断连） | 先按幂等键查询对账，禁止伪造终态 | 否（第 15 周 A-06 语义，本周回归 D-06） |
| 依赖故障（本周新增） | `mcp_unavailable`（写路径降级停驻） | 停在 `action_preview`，0 效果，决策留给用户 | ——（这就是降级本身） |

## 四、SLI 口径增补（v1.1，对 week-16/runtime-sli-spec-v1.md 的登记性扩展）

1. **可见终态词表 7 -> 8**：新增第 8 类 `action_preview`（降级停驻），属
   **允许终态**（安全停住，计入 `allowed_terminal_rate` 分子）。既有六类 +
   非终态语义一字未动；正常流程的预览仍是非终态（`classify_terminal("write",
   "action_preview")` 不变），降级停驻用独立 outcome `action_preview_held`
   表达，week-16 基线与回放零影响（test_02/test_05/test_06 全绿）。
2. **`forbidden_effect_count` 覆盖面扩展**：降级停驻终态携带任何效果增量 =
   合同违规，照抓不漏（「停在预览」的定义就是 0 效果）。
3. **遥测词表增补**：mcp_call 段允许 `error_class=mcp_unavailable`，safe_refs
   允许 `degraded=true / trigger ∈ {fault_injection:write_probe,
   kill_switch:MCP_OFF} / allowed_next_actions`（仅枚举与 ID，不含正文）。

## 五、红线（本周不做 / 绝不做）

1. 降级路径照常走 ACL、审批、审计 —— 降级**不豁免**任何安全机制；
2. 不做写入自动重试；写结果不明确先对账（第 15 周 A-06 不退化，D-06 固定回归）；
3. 不新增模型可调用工具（仍 1 读 + 3 写），不加多 Agent，不建值班组织；
4. 不在同一周重写三套恢复机制：模型/索引只做观察与固定演练（手册 3968），
   MODEL_OFF/INDEX_STALE 完成登记 + 演练门，不改生产读路径；
5. 不提前做第 18 周冷交接；所有结果标注为教学/授权环境证据。
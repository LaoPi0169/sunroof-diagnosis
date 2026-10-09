# 可观测性双视图基线报告 v1（observability-baseline-report-v1，第 16 周）

- 日期: 2026-10-09 · 运行环境: 本地确定性组件（固定时钟 2026-10-09T00:00:00Z）·
  复现: `python -m mcptool.run_canary` / `python -m mcptool.run_telemetry_replay` ·
  工件: `week-16/canary-baseline-v1.jsonl`（60 行）+
  `week-16/telemetry-span-sample-v1.jsonl`（90 行）
- 回链: `week-16/task-quality-canary-contract-v1.md` + `week-16/runtime-sli-spec-v1.md`

## 一、两个数字（并排展示，永不合并）

| 视图 | 分母 | 结果 | 指标含义 |
| --- | --- | --- | --- |
| ① 离线考卷金丝雀 | 冻结评测集 **60 例** | 状态匹配 **58/60**；行为漂移 **0**；语料卫生违规 **0** | 内容正确性（对答案） |
| ② 运行期 SLI（合成回放） | 窗口内 task_run_id **10 个** | allowed_terminal_rate **8/10**；forbidden_effect_count **0**；unconfirmed_effects **1** | 过程健康度（看终态） |

**这两个数字不是同一支体温计**：分母不同（考卷例 vs 任务）、证据不同
（状态对账 vs 终态分类）、用途不同（回归门 vs 过程监控）。不相加、不求平均、
不互相换算——各指标输出已硬编码此警示（denominator_note）。

## 二、视图 ① 逐项（考卷金丝雀）

- 版本链绑定：corpus `northstar-corpus-w09-v1` / dataset `northstar-eval-w10-v1`
  / retriever `vector-hashed-ngram-v1` / candidate `northstar-rag-readonly-v1`
  ——与冻结 manifest 逐项一致，输入 sha256 校验通过（fail closed 未触发）；
- 答案键完整性：`sha256(LF) = 5a614cda…dfed` 与第 12 周报告记录一致；
  raw sha 另存（行尾搅动检测口径，与仓库既有惯例一致）；
- 匹配 58/60；**已知失败组合不变**：
  - NS-HOLD-06：期望 `refused`，实际 `answered` → **硬门失败**，指路 F12-01/F12-04；
  - NS-HOLD-08：期望 `needs_review`，实际 `refused` → 安全侧拒答，指路 F12-02；
- 结论：**与冻结基线一致，无行为漂移**（金丝雀的「通过」= 没有新坏消息，
  不是没有坏消息）。

## 三、视图 ② 逐项（运行期回放 O-01..O-10）

| 任务 | 类型 | 剧本 | 可见终态 | 效果 |
| --- | --- | --- | --- | --- |
| O-01 | read | NS-DEV-01（工具在环） | answered ✓ | 0 |
| O-02 | read | NS-DEV-07（期望 refused→abstained） | abstained ✓ | 0 |
| O-03 | read | NS-DEV-08（期望 escalated） | escalated ✓ | 0 |
| O-04 | write | 预览→批准→执行→核验 | action_completed ✓ | 1 |
| O-05 | write | 批准后执行前撤销（A-04） | approval_revoked ✓ | 0 |
| O-06 | write | 策略拒绝 executive-review | approval_denied ✓ | 0 |
| O-07 | write | 断连→按幂等键先查后说（A-06） | action_completed ✓ | 1 |
| O-08 | write | 断连+对账查询不可用（A-06 二段） | **非终态**（reconciliation_pending） | 1（未确认） |
| O-09 | read | 读路径超时（deadline 500ms） | system_failed ✗ | 0 |
| O-10 | read | NS-HOLD-06 内容管线 | answered ✓（**见 §四**） | 0 |

终态分布：answered 2 / abstained 1 / escalated 1 / approval_denied 1 /
approval_revoked 1 / action_completed 2 / system_failed 1；非终态 pending 1。
阶段覆盖：90 段全落（含 not_applicable 留痕）；O-07/O-08 的对账段为真实 span
（断点 #2 实测），O-10 的 mcp_call 段如实标注 not_applicable（holdout 案例
不在只读工单库，内容管线与冻结评测同构）。

## 四、活证据：同一案例，两个视图说法不同

NS-HOLD-06（副卡注销，语料外问句）：
- **运行视图**（O-10）：终态 `answered`，计入 allowed（过程完全健康）；
- **考卷视图**：`state_match=false`，期望 `refused`，**硬门失败**（F12-01：
  作答线薄余量在 supervisor 池上失守，自信作答于无答案例）。
- 结论：**运行期信号看不见内容错误**。若只有一支体温计，这个任务会被当成
  「正常任务」计入通过率——这正是两支都要有的原因（手册 3667–3888 原文场景）。

## 五、结论与边界

1. 当前系统行为与第 12 周冻结基线一致（金丝雀无新坏消息）；过程面合同硬门
   全守住（拒绝/撤销/失败路径零效果；效果合计 3 = 2 已确认创建 + 1 待对账）；
2. 本报告是**基线记录**，不是达标声明：回放是固定剧本，8/10 只证明口径可算、
   剧本可信，不代表真实流量水平（SLO 目标全部 [U]，见 `slo-proposal-v0.md`）；
3. 抽样复核独立进行（SLO-5），不得以金丝雀代填；
4. 工作台 `workbench_observability/` 可交互复现本报告两张表与 NS-HOLD-06 对照
   （业务用户测试脚本 O-00..O-06）。

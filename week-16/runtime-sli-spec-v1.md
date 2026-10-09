# 运行期 SLI 规格 v1（runtime-sli-spec-v1，第 16 周）

- 日期: 2026-10-09 · 回链: `specs/business-background.md` 3667–3888 ·
  实现: `mcptool/telemetry.py`（归约器）+ `mcptool/run_telemetry_replay.py`（合成回放）
- 与 `week-16/task-quality-canary-contract-v1.md` 的关系: 两种体温计之一 ——
  本规格管「运行期过程健康度」；考卷金丝雀管内容正确性。
  **两个分母永不共享，两个数永不合并。**

## 一、观测单元：task_run_id

每个业务任务一个 `task_run_id`（读任务 = 一次 RAG 问答；写任务 = 一次受控升级
动作）。任务走完留下一条九阶段 span 链（记录面见 `telemetry-contract-v1.md`）：
`request_received → identity_and_scope → retrieval → generation →
citation_and_policy_verification → approval → mcp_call → reconciliation →
response_presented`。缺席的阶段落 `not_applicable` span（不留空洞、不假装执行）。

## 二、状态对照表（内部状态 → 六类可见终态）

| 内部状态 | 可见终态 | 允许终态 | 效果预算 |
| --- | --- | --- | --- |
| RAG `answered` | `answered` | ✓ | 0 |
| RAG `refused` / `needs_review` | `abstained` | ✓ | 0 |
| RAG `escalated` | `escalated` | ✓ | 0 |
| `approval_denied`（策略拒绝/审批缺失/过期/不匹配） | `approval_denied` | ✓ | **恒 0** |
| `approval_revoked`（执行前撤销） | `approval_revoked` | ✓ | **恒 0** |
| `action_completed`（含 recovered_from=result_unknown） | `action_completed` | ✓ | 0..1（幂等重放=0） |
| `invalid_argument` / `not_available` / `version_mismatch` / `contract_violation` | `system_failed` | ✗ | **恒 0** |
| `deadline_exceeded`（写） | 非终态（效果未确认 → 先对账） | — | 计未确认 |
| `result_unknown` / `reconciliation_pending` | **非终态**（禁止伪造终态） | — | 计未确认 |

要点：`reconciliation_pending` 是「诚实停住」，不是失败也不是成功——
**绝不折进 allowed_terminal_rate 分子**，单独按未确认口径计数。

## 三、SLI 定义（本版实现的口径）

1. **allowed_terminal_rate** = 进入允许终态的任务数 ÷ 窗口内 task_run_id 总数。
   分母 = 真实流量（上线后）/ 合成回放任务数（本周）；**≠ 考卷的 60 例**。
2. **forbidden_effect_count** = 终态为 `approval_denied` / `approval_revoked` /
   `system_failed` 的任务的效果增量总和。恒等目标 **= 0**（第 14/15 周硬门：
   拒/撤/失败路径效果恒 0；违反即安全事件）。与指标 1 相互独立——
   「过程健康」≠「合同未违规」，两个都要看。
3. **unconfirmed_effects** = 非终态任务（unknown/pending/写超时）携带的效果
   增量总和。这些效果「已发生但未确认」，不进分子也不算禁止，只催对账。
4. **stage_coverage** = 九阶段逐段 emitted / not_applicable 计数（观测覆盖，
   不设时长门；duration_ms 是观测值，本版不断言）。

每份指标输出硬编码两条警示（`denominator_note` / `content_note`）：
分母口径声明 + 「answered 不证明内容正确」——防两视图合并成「一个数」。

## 四、本版基线：合成回放（不是真实流量）

固定剧本 O-01..O-10（固定时钟 2026-10-09T00:00:00Z）：3 读（answered/abstained/
escalated，工具在环）+ 5 写（批准执行 1 效果 / 撤销 0 / 拒绝 0 / 断连对账 1 /
断连+查询不可用=待定 1 未确认）+ 读超时（system_failed）+ **NS-HOLD-06 活证据**
（运行视图 answered=allowed，考卷视图硬门失败——运行期看不见内容错误）。
结果：allowed 8/10、forbidden 0、unconfirmed 1、效果合计 3。

## 五、红线

1. 本版无真实流量：目标值/窗口全部 [U]（见 `slo-proposal-v0.md`），禁止拿
   合成回放的 8/10 冒充 SLO 达标；
2. 两视图数字不相加、不求平均、不互相换算；
3. 非终态不得折进任何通过/失败率（禁止用统计口径伪造终态）；
4. 遥测记录面受 `telemetry-contract-v1.md` 约束：正文/载荷/凭据不入遥测。

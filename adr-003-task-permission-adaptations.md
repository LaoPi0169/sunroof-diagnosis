# ADR-003: Week 07 任务闭环对 mission 参考实现的适配决策

状态：已采纳（2026-09，Week 07）
回链：`specs/mission-week-07.md`（参考实现含 DDL/auth/services 代码）；前置：`adr-002-ingestion-lineage.md`（不动冻结表的先例）。

## 背景

mission 给出了参考 DDL 与参考代码作为"起点而非终点"。其中两处与本项目 001 冻结的 `task_runs` / `policies` 表直接冲突，另有一处验收标准与参考代码不一致。逐项决策如下。

## D1 —— 用新表 `task_action_runs` 承载任务动作审计，不重建 `task_runs`

**问题**：参考 DDL 把 `task_runs.status` 的 CHECK 扩到 7 种任务状态，并要求 `task_id`/`action`/`correlation_id` 列。但 001 已冻结的 `task_runs` 表：
- CHECK 约束只允许 `('answered','needs_review','denied')`；
- 没有 `task_id`/`action`/`correlation_id` 列；
- SQLite 无法 `ALTER` CHECK 约束，加列也无法解除既有约束。

**决策**：保留 001 `task_runs` 原样（继续服务 Week 05 查询审计），新建 `task_action_runs` 表（迁移 003）承载任务动作审计，三向对账键 `correlation_id` 落在新表。

**代价**：审计分裂在两张表（`check_policy` 动作会在两表各留一行）。**补偿**：`check_policy` 的 `task_runs` 行即 Week 05 血缘证据；`task_action_runs` 是任务维度的完整流水。测试用例 1 显式断言两表都有该 correlation_id 记录。

## D2 —— 不重建 `policies`，Week 07 零政策表变更

参考 DDL 重建 `policies`（主键 `(policy_id, source_version)`）。这与 Week 06 数据契约（业务键 = `policy_id` + `source_version`，但表结构已冻结）冲突，且无必要：工单核对复用 `query_policies` 既有 ACL。迁移 003 只新增 `billing_tasks`、`task_action_runs` 与 4 条工单夹具。

## D3 —— 增加 `refuse`（拒绝）动作

验收标准第 4 条要求客服可"确认/拒绝/升级三选一"，但参考代码只有 confirm/escalate。缺 refuse 会让"不做"变成静默猜测而非显式决定，直接违背不猜答案原则。

**决策**：新增 `refuse` 动作：`answered → refused`（终态）、agent/supervisor 均可、幂等、审计留痕，原因码 `REFUSED_BY_AGENT`。权限矩阵与状态模型同步登记。

## D4 —— 工作台交付为 CLI 而非 Web UI

mission 允许"最小前端或 CLI"。选 CLI（`workbench_cli.py`，纯标准库 urllib，零新增依赖）：本课程安全设计全部在服务端，CLI 天然把"前端"压薄成壳，最能证明"前端不是安全屏障"；Web UI 会引入与教学目标无关的构建链。CLI 仍完整实现受保护走查体验（Token 登录 → 任务队列 → SOP 三要素核验 → confirm/refuse/escalate）。

## D5 —— `check_policy` 100% 复用 Week 05 引擎，不实现参考版的简化分支

参考 services 代码只区分"命中/未命中"，未处理 POLICY_CONFLICT 与金额门槛之外的分支。复用 `process_policy_check`（工单事实 → `PolicyCheckRequest` 参数化）保证任务核对与 `/api/v1/policy-check` 零逻辑分叉；附带收益是 Week 05 `task_runs` 审计自动衔接（D1）。`needs_review` 的三类原因码由引擎原样透传进任务状态机。

## 后果与验证

- 全部适配已被 7 个 E2E 用例钉住（tests/test_frontline_tasks.py），全量 20/20 通过；
- 现有 Week 05/06 测试零改动、零失败，证明冻结契约未被破坏；
- 迁移 003 保持幂等（CREATE IF NOT EXISTS + INSERT OR IGNORE），沿用 ADR-002 的无重建原则。

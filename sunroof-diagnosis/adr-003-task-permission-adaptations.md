# ADR-003: 节点 04 任务闭环对参考实现（原仓库 Week 07）的适配决策

状态：已采纳（2026-10，阶段 A 节点 04）
回链：`../application/tasks.py` 与 `../task-state-model.md`（参考实现含 DDL/auth/services 代码）；前置：`adr-002-ingestion-lineage.md`（不动冻结表的先例）。

## 背景

原仓库 Week 07 给出了任务闭环的参考 DDL 与参考代码（`billing_tasks` + `task_action_runs` + RBAC 状态机）作为"起点而非终点"。本子项目将其移植到天窗总成只读诊断领域时，逐项决策如下。

## D1 —— 用新表 `task_action_runs` 承载任务动作审计，不重建 001 冻结的 `task_runs`

**问题**：参考 DDL 把 `task_runs.status` 的 CHECK 扩到 8 种任务状态，并要求 `task_id`/`action`/`correlation_id` 列。但 001 已冻结的 `task_runs` 表：
- CHECK 约束只允许 `('answered','needs_review','denied')`；
- 没有 `task_id`/`action`/`correlation_id` 列；
- SQLite 无法 `ALTER` CHECK 约束，加列也无法解除既有约束。

**决策**：保留 001 `task_runs` 原样（继续服务节点 02 查询审计），新建 `task_action_runs` 表（迁移 003）承载任务动作审计，三向对账键 `correlation_id` 落在新表。

**代价**：审计分裂在两张表（`check_spec` 动作会在两表各留一行）。**补偿**：`check_spec` 的 `task_runs` 行即节点 02 血缘证据；`task_action_runs` 是任务维度的完整流水。测试用例 1 显式断言两表都有该 correlation_id 记录。

## D2 —— 不重建 `specs`，节点 04 零规格表变更

参考 DDL 的任务表与政策/规格表耦合重建。本子项目 `specs` 表在节点 03 已绑定业务键 `spec_id:source_version` 与摄取血缘列（表结构冻结）。迁移 003 只新增 `spec_check_tasks`、`task_action_runs` 与 4 条任务夹具；任务核对复用 `query_current_specs` 既有 ACL 下推。

## D3 —— 保留 `refuse`（拒绝）动作

一线 PQE 可"确认/拒绝/升级三选一"。缺 refuse 会让"不做"变成静默猜测而非显式决定，直接违背不猜答案原则。

**决策**：`refuse`：`answered → refused`（终态）、pqe/quality_supervisor 均可、幂等、审计留痕，原因码 `REFUSED_BY_AGENT`。状态模型同步登记。

## D4 —— 工作台交付为 CLI 而非 Web UI

选 CLI（`workbench_cli.py`，纯标准库 urllib，零新增依赖）：本课程安全设计全部在服务端，CLI 天然把"前端"压薄成壳，最能证明"前端不是安全屏障"；Web UI 会引入与教学目标无关的构建链。CLI 完整实现受保护走查体验（Token 登录 → 任务队列 → 规格三要素核验 → confirm/refuse/escalate）。

## D5 —— `check_spec` 100% 复用节点 02 引擎，不实现简化分支

复用 `process_spec_check`（任务事实 → `SpecCheckRequest` 参数化）保证任务核对与 `/api/v1/spec-check` 零逻辑分叉；附带收益是节点 02 `task_runs` 审计自动衔接（D1）。`needs_review` 的三类原因码（SPEC_HARD_LIMIT_EXCEEDED / NO_MATCHING_SPEC / SPEC_CONFLICT）由引擎原样透传进任务状态机。

## D6 —— 任务级 ACL 交叉校验（本子项目新增，强于参考实现）

**问题**：参考实现的任务表无归属概念，任意同角色用户可触达任意工单。本子项目的硬门槛 P-1（跨 OEM 越权泄露 = 0）不允许：OEM-B PQE 直调 OEM-A 任务时，响应若回显该任务的 `case_id`/SN 等事实即构成跨项目泄漏。

**决策**：`spec_check_tasks` 增加 `oem_project` 列；`process_task_action` 在 RBAC 动作校验之后追加任务级 ACL 交叉校验——用户 `allowed_oem_projects` 不含任务归属项目组（且非 `["*"]`）→ `denied` 路径，`case_id` 回通用常量 `N/A-ACL-DENIED`，不回显归属项目组任何标识；`GET /api/v1/tasks` 队列同样按用户 ACL 过滤。

**与矩阵的关系**：矩阵 §2"规格核对.执行"对跨项目 PQE 的最低要求是 `NO_MATCHING_SPEC`（自供参数场景）；本决策对"任务"这一服务端持有事实的对象采取更强的 `denied + 零回显`，不与矩阵冲突（更严格一侧），并在 `task-state-model.md` §4 登记。

## 后果与验证

- 全部适配已被 9 个 E2E 用例钉住（tests/test_frontline_tasks.py），全量 26/26 通过；
- 现有节点 02/03 测试零改动、零失败，证明冻结契约未被破坏；
- 迁移 003 保持幂等（CREATE IF NOT EXISTS + INSERT OR IGNORE），沿用 adr-002 的无重建原则。

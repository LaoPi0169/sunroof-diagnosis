# ADR-002: Week 06 摄取管道的幂等血缘设计决策

- **状态**：Accepted
- **日期**：2026-09-30
- **关联**：`specs/mission-week-06.md`、`data-contract-v1.md`、`ingestion-runbook-v0.md`、ADR-001（Week 05 基线）

## 背景

mission-week-06.md 给出的参考实现假设 `policies` 表由 `002_ingestion_schema.sql` 全新建表（主键 `(policy_id, source_version)`）。但本仓库的 `policies` 表已在 Week 05 基线（`001_init_schema.sql`，提交 `da56cf0`，tag `week05-baseline`）中冻结建表，主键为 `(policy_id, version)`，且 `001` 是不可变更的既有事实。Week 06 必须在不破坏 Week 05 契约与 5 个基线测试的前提下引入血缘列。

## 决策

### D1. 血缘列以幂等 ALTER 追加，不重建 policies 表

`application/database.py::init_db` 按序执行 `001` → `002`，随后 `PRAGMA table_info(policies)` 检查并幂等 `ALTER TABLE ADD COLUMN`：`source_version TEXT NOT NULL DEFAULT ''`、`owner_id TEXT`、`batch_id TEXT NOT NULL DEFAULT ''`，并补建 `idx_policies_batch` 索引。Week 05 表结构、主键与三条种子夹具保持原样。

**代价与缓解**：业务键 `(policy_id, source_version)` 无法做成数据库主键。幂等性由应用层三层防线保证：批内 `seen` 集合 → `policy_exists` 查重 → 主键约束 `IntegrityError` 兜底（兜底路径归类为 `DUPLICATE_BUSINESS_KEY` 隔离，不产生第二次业务效果）。集成测试 `test_02` / `test_07` 显式验证「无第二个当前版本」。

### D2. 种子数据血缘回填

`002` 迁移后幂等执行两条 `UPDATE`：`batch_id = 'week05-baseline-seed'`（仅空值行）、`source_version = version`（仅空值行）。使验收标准「主表每个业务版本可追溯 source_id/source_version/batch_id」对存量数据同样成立，且后续批次与种子业务键冲突时能正确判重。

### D3. 批次单事务原子提交

参考实现逐条语句 `commit()`，进程中途死亡会留下 `processing` 状态与部分提交。本实现将「登记批次 → 逐行分流 → 写终态」放入单个事务，统一 commit / rollback。批次终态要么完整存在，要么完全不存在，重跑天然安全。

### D4. 普通日志字段白名单

结构化日志（logger `fde.ingestion`）只允许关联 ID、行引用、计数与原因码；政策 ID、标题、公式、所有者、原始行 JSON 一律禁止（`test_06` 用 16 个敏感标记逐一断言）。原始行仅存隔离区证据存储。

### D5. fixed_payload 补丁式合并 + 失败重放也落审计

`execute_targeted_replay` 将 `fixed_payload` 与隔离区 `raw_payload` 合并后重新校验（参考实现要求传完整行）。修复补丁更符合「修补特定错误记录」的运维事实。成功与失败的重放尝试都写入 `ingestion_replay_logs`（含 `fixed_payload` 快照列），修复历史完整可追溯。

### D6. 批次状态补全

`BatchStatus` 在参考实现的 `completed` 基础上增加 `completed_with_quarantine`（存在隔离行时使用，对齐 business-background 第 6 周预期结果）与 `failed`；幂等短路仅对两个完成态生效。

## 后果

- Week 05 基线 5 个测试零改动通过（回归见 `test_08`：摄取数据回接 Week 05 确定性查询引擎）。
- 业务键唯一性由应用层保证而非数据库约束；单进程 / 低并发教学场景下风险可控，多写入方并发写入需在后续周引入 `UNIQUE(policy_id, source_version)` 索引（建索引脚本已预留于迁移清单）。
- 为第 7 周角色权限工作台提供通过质量门、血缘完整、可重放的数据底座。

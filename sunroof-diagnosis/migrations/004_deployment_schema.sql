-- ============================================================================
-- 004_deployment_schema.sql
-- 天窗总成只读诊断 · 阶段 A 节点 05 可重复部署与回滚：发布配置表 + 种子默认值
-- 回链: ../fde-roadmap-v1.md §2 节点 ⑤ + specs/mission-week-08.md (发布清单/配置收敛)
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 适配说明 (adr-004-deployment-config-adaptations.md):
--   1. 不改动 001-003 冻结 DDL/契约/引擎判定表 —— 本迁移只新增独立配置表;
--   2. policy_config 是配置收敛器 (deploy/apply_policy_config.py) 的唯一可写面:
--      白名单键 + 幂等 UPSERT, 不建表、不删库 —— 回滚时数据库兼容性由此保证;
--   3. 引擎 (application/services.py) 读取阈值配置, 缺失/非法时回退引擎默认值
--      (15.0), known-good 配置下行为逐字节等价, 26 个既有测试零回归。
-- 安全边界: 只存部署参数, 不存任何秘密/凭据 (清单与配置均不含 token)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表: policy_config —— 发布配置键值表 (收敛器唯一可写面; 引擎只读)
-- 种子默认值与节点 02 引擎常量一致: |偏差%| > 15.0 -> needs_review / SPEC_HARD_LIMIT_EXCEEDED
-- release.config_version: 当前生效配置版本标记 (收敛器写入, 冒烟/对账可核验)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_config (
    config_key    TEXT NOT NULL PRIMARY KEY,   -- 配置键 (白名单见收敛器)
    config_value  TEXT NOT NULL,               -- 配置值 (字符串承载, 引擎侧解析)
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by    TEXT NOT NULL DEFAULT 'migration_seed'
);

INSERT OR IGNORE INTO policy_config (config_key, config_value, updated_by) VALUES
    ('spec_check.hard_limit_deviation_pct', '15.0', 'migration_seed'),
    ('release.config_version', 'seed-pre-deploy', 'migration_seed');

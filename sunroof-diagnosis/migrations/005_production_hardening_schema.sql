-- ============================================================================
-- 005_production_hardening_schema.sql
-- 天窗总成只读诊断 · 阶段 D 批次 1 生产加固：审计哈希链头表 + 签名令牌吊销表
-- 回链: specs/production-hardening-brief-v1.md D1-b/D1-c + adr-005 D2/D5
--       + decision-memo-02.md §3 第 1/2/3 条 (并发竞态/模拟鉴权/审计无防篡改)
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 适配说明 (adr-005-production-hardening-adaptations.md D1/D2):
--   1. 不重建 003 冻结的 spec_check_tasks / task_action_runs 表 —— version 列与
--      prev_entry_hash/entry_hash 列由 application/database.py 以 PRAGMA 检查后的
--      幂等 ALTER 追加 (与节点 03 specs 血缘列同一先例);
--   2. 本迁移只新增两张独立表, 不触碰 001-004 冻结 DDL/契约/引擎判定表。
-- 安全边界: 链头与吊销表只存哈希与标识, 不存任何秘密/凭据/个人信息
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 1: audit_chain_head —— 任务动作审计哈希链链头 (单行, id=1 CHECK 强制)
-- 追加协议 (adr-005 D2): 每写一行 task_action_runs, 同事务内推进链头
-- (last_entry_hash = 新行 entry_hash, entry_count +1)。校验三重:
-- 重算哈希一致 + 前向链接一致 + 链头与末行/行数一致。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_chain_head (
    id              INTEGER PRIMARY KEY CHECK (id = 1),   -- 单行表 (强约束)
    last_entry_hash TEXT NOT NULL DEFAULT '',             -- 末行 entry_hash (空串 = 空链)
    last_run_id     TEXT NOT NULL DEFAULT '',             -- 末行 run_id
    entry_count     INTEGER NOT NULL DEFAULT 0,           -- 已入链审计行数
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO audit_chain_head (id) VALUES (1);

-- ----------------------------------------------------------------------------
-- 表 2: revoked_tokens —— 已吊销签名令牌 (sr1.* 的 jti 主键)
-- 吊销即 INSERT OR IGNORE (幂等); 鉴权层 decode 后查本表, 命中即 401 Default-Deny。
-- 只存 jti/标识/原因, 绝不存令牌载荷全文或任何秘密。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        TEXT PRIMARY KEY,                          -- 签名令牌载荷内 jti (uuid hex16)
    user_id    TEXT NOT NULL DEFAULT '',                  -- 签发时归属身份 (吊销时可未知, 容许空串)
    revoked_by TEXT NOT NULL DEFAULT '',                  -- 执行吊销的主管 user_id
    reason     TEXT,                                      -- 可选结构化原因 (长度上限 200, 应用层约束)
    revoked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ============================================================================
-- 002_ingestion_schema.sql
-- Week 06 可靠数据入口：摄取批次 / 隔离区 / 重放审计 DDL
-- 回链: specs/mission-week-06.md (第 2/3 天) 与 specs/business-background.md 第 6 周
-- 幂等性: 仅 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，
--         重复执行不改变已有终态；policies 血缘列由应用层幂等 ALTER 补齐
--         (application/database.py::_ensure_policy_lineage_columns)
-- 安全边界: raw_payload 仅进入隔离区证据存储；普通结构化日志绝不记录这些内容
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 3: ingestion_batches —— 摄取批次汇总 (三处一致对账的批次侧锚点)
-- 允许的数据变化: 每个批次先以 processing 落库，事务结束时一次性写入四个计数与终态
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_batches (
    batch_id          TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL,
    source_version    TEXT NOT NULL DEFAULT '',   -- 批次级上游版本 (血缘)
    total_rows        INTEGER NOT NULL DEFAULT 0,
    accepted_count    INTEGER NOT NULL DEFAULT 0,
    duplicate_count   INTEGER NOT NULL DEFAULT 0,
    quarantined_count INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'processing'
                      CHECK (status IN ('processing', 'completed', 'completed_with_quarantine', 'failed')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- 表 4: quarantine_records —— 隔离区 (无静默丢弃: 每条无效/重复行都有原因码)
-- 重复行 (DUPLICATE_BUSINESS_KEY) 同样落隔离表用于批次对账，但不计入 quarantined_count
-- 修复历史: 记录只会从 quarantined -> resolved 状态流转，绝不物理删除
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quarantine_records (
    quarantine_id   TEXT PRIMARY KEY,             -- quar-{uuid12}
    batch_id        TEXT NOT NULL,
    safe_source_ref TEXT NOT NULL DEFAULT '',     -- 安全来源引用 (source_id#R行号，可入日志)
    raw_payload     TEXT NOT NULL,                -- 原始行 JSON (仅存证据存储，绝不入普通日志)
    reason_code     TEXT NOT NULL,                -- MISSING_OWNER / INVALID_EFFECTIVE_RANGE / ...
    status          TEXT NOT NULL DEFAULT 'quarantined'
                    CHECK (status IN ('quarantined', 'resolved', 'rejected')),
    replay_id       TEXT,                         -- 定向重放成功后回填关联
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (batch_id) REFERENCES ingestion_batches (batch_id)
);

-- ----------------------------------------------------------------------------
-- 表 5: ingestion_replay_logs —— 定向重放审计 (修复历史绝不物理删除)
-- 成功与失败的重放尝试都落一行审计，保证每次修复操作可追溯
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_replay_logs (
    replay_id     TEXT PRIMARY KEY,               -- rep-{hex10}
    quarantine_id TEXT NOT NULL,
    batch_id      TEXT NOT NULL,                  -- 原隔离记录所属批次 (血缘回链)
    replayed_by   TEXT NOT NULL,                  -- 执行修复重放的操作者
    fixed_payload TEXT,                           -- 修复后合并的完整行 JSON (审计证据)
    new_policy_id TEXT,                           -- 成功时为写入主表的 policy_id
    status        TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (quarantine_id) REFERENCES quarantine_records (quarantine_id)
);

CREATE INDEX IF NOT EXISTS idx_quarantine_batch_status ON quarantine_records (batch_id, status);
CREATE INDEX IF NOT EXISTS idx_quarantine_reason ON quarantine_records (reason_code);
CREATE INDEX IF NOT EXISTS idx_replay_logs_quarantine ON ingestion_replay_logs (quarantine_id);
-- 注意: policies(batch_id) 索引在应用层血缘列 ALTER 完成后创建 (见 database.py)
-- ----------------------------------------------------------------------------
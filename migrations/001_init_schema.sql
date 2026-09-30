-- ============================================================================
-- 001_init_schema.sql
-- Week 05 确定性垂直薄片：数据库 DDL 与固定合成 Fixtures
-- 回链: specs/business-background.md 第 5 周 "先写迁移，再插入固定合成夹具"
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 安全边界: 只保存结构化摘要字段，不保存政策正文 / 密钥 / 个人信息
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 1: policies —— 政策版本主数据 (确定性 SQL 查询的唯一事实来源)
-- 约束设计原则: 缺少关键标识或非法日期关系应在数据库层尽早失败，不依赖界面校验
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policies (
    policy_id     TEXT    NOT NULL,                -- 跨版本稳定的政策标识
    title         TEXT    NOT NULL,                -- 政策标题 (摘要，可进入响应)
    version       TEXT    NOT NULL,                -- 来源系统中的版本
    region        TEXT    NOT NULL CHECK (region IN ('华东', '华南', '华北', '全国')),
    access_level  TEXT    NOT NULL CHECK (access_level IN ('support_agent', 'supervisor')),
    is_deprecated INTEGER NOT NULL DEFAULT 0 CHECK (is_deprecated IN (0, 1)),
    effective_at  TEXT    NOT NULL,                -- 生效日期 (ISO-8601, YYYY-MM-DD)
    expires_at    TEXT,                            -- 失效日期 (NULL = 长期有效)
    formula_rule  TEXT    NOT NULL DEFAULT '',     -- 确定性公式规则 (结构化，非模型生成)
    source_id     TEXT    NOT NULL,                -- 可追溯来源 ID (可核验说明)
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (policy_id, version),
    CHECK (expires_at IS NULL OR expires_at > effective_at)  -- 非法日期关系尽早失败
);

-- ----------------------------------------------------------------------------
-- 表 2: task_runs —— 任务运行审计日志 (三向对账: API 响应 / DB 终态 / 测试)
-- 允许的数据变化: 每次任务查询仅追加一条记录，不产生任何其他写副作用
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_runs (
    run_id            TEXT PRIMARY KEY,             -- run-{uuid12}
    request_id        TEXT NOT NULL,                -- 客户端关联标识 (三向对账键)
    user_id           TEXT NOT NULL,                -- 服务端可信身份 (非请求体自报)
    user_role         TEXT NOT NULL,
    task_type         TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('answered', 'needs_review', 'denied')),
    reason_code       TEXT,                         -- 异常时为结构化原因码，成功时为 NULL
    matched_policy_id TEXT,                         -- answered 时回填政策 ID
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_runs_request_id ON task_runs (request_id);
CREATE INDEX IF NOT EXISTS idx_policies_region_effective ON policies (region, effective_at, is_deprecated);

-- ----------------------------------------------------------------------------
-- 固定合成 Fixtures (全部为教学模拟数据，不代表真实政策)
-- 设计目标 (保证 5 个集成测试 100% 确定通过):
--   * POL-2026-BIL-01 v3 -> 成功路径唯一命中 (华东 / 2026 生效 / support_agent 可见)
--   * POL-2025-BIL-01 v1 -> 已废弃版本，验证确定性 SQL 拦截过期数据
--   * POL-2026-REF-09 v4 -> supervisor 专属，验证服务端 ACL 隔离 (普通客服不可见)
--   * 三条夹具保证成功路径恰好一条匹配，不触发 POLICY_CONFLICT
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO policies
    (policy_id, title, version, region, access_level, is_deprecated, effective_at, expires_at, formula_rule, source_id)
VALUES
    ('POL-2026-BIL-01', '2026 华东套餐差价计费政策', 'v3', '华东', 'support_agent', 0,
     '2026-01-01', NULL, 'refund = (new_price - old_price) * remaining_months', 'KB-BILLING-2026-08'),
    ('POL-2025-BIL-01', '2025 华东套餐差价计费政策（已废弃）', 'v1', '华东', 'support_agent', 1,
     '2025-01-01', '2025-12-31', 'legacy_rule_v1 (deprecated)', 'KB-BILLING-2025-01'),
    ('POL-2026-REF-09', '2026 特殊退款政策（主管专用）', 'v4', '华东', 'supervisor', 0,
     '2026-07-01', NULL, 'special_refund_rule_v4', 'KB-REFUND-2026-07');

-- ============================================================================
-- 003_tasks_and_permissions_schema.sql
-- Week 07 一线角色与权限任务闭环：客服工单表 + 任务动作审计表 + 固定合成工单夹具
-- 回链: specs/mission-week-07.md 第 2 天 "实现服务端授权与幂等写入"
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 适配说明 (adr-003-ingestion-lineage 的延续, 见 adr-003-task-permission-adaptations.md):
--   1. 不重建 001 冻结的 policies 表 (血缘列复用 Week 06 既有结构);
--   2. 不重建 001 冻结的 task_runs 表 (其 CHECK 约束限定 3 种查询终态且 SQLite
--      无法 ALTER CHECK)，任务动作审计写入独立的 task_action_runs 表。
-- 安全边界: 只保存结构化摘要字段，不保存政策正文 / 密钥 / 个人信息
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 1: billing_tasks —— 客服工单任务表 (任务状态机的唯一事实来源)
-- 状态机: initialized -> answered/needs_review -> confirmed/refused/escalated
--         escalated -> action_completed (主管); 越权动作不改变状态 (denied 仅审计)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_tasks (
    task_id           TEXT PRIMARY KEY,               -- 工单标识 (业务键)
    case_id           TEXT NOT NULL,                  -- 客户案例号 (可进入响应)
    region            TEXT NOT NULL CHECK (region IN ('华东', '华南', '华北', '全国')),
    amount            REAL NOT NULL DEFAULT 0.0 CHECK (amount >= 0),
    purchased_at      TEXT NOT NULL,                  -- 客户购买时间 (ISO-8601, YYYY-MM-DD)
    assigned_agent_id TEXT NOT NULL,                  -- 责任客服 (服务端分配，绝不来自请求体自报)
    status            TEXT NOT NULL DEFAULT 'initialized'
                      CHECK (status IN ('initialized', 'answered', 'needs_review', 'confirmed',
                                        'refused', 'escalated', 'denied', 'action_completed')),
    reason_code       TEXT,                           -- 最近一次终态的结构化原因码
    matched_policy_id TEXT,                           -- answered 时回填的政策 ID (可核验依据)
    idempotency_key   TEXT UNIQUE,                    -- 升级动作幂等键 (首次 escalate 写入)
    correlation_id    TEXT NOT NULL,                  -- 全局关联标识 (响应/DB/日志三向对账键)
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- 表 2: task_action_runs —— 任务动作与安全审计日志
-- 三向对账锚点: HTTP 响应 (correlation_id) / billing_tasks 终态 / 本表动作流水。
-- 越权尝试 (denied) 也必须落一行安全审计事件 (含 task_id/user_id/action)。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_action_runs (
    run_id         TEXT PRIMARY KEY,                  -- run-{uuid12}
    task_id        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    user_id        TEXT NOT NULL,                     -- 服务端可信身份 (非请求体自报)
    user_role      TEXT NOT NULL,
    action         TEXT NOT NULL,                     -- check_policy/confirm/refuse/escalate/supervisor_resolve
    status         TEXT NOT NULL,                     -- 本次动作产生的任务终态 (denied 表示被安全阻断)
    reason_code    TEXT,                              -- 结构化原因码 (如 ALREADY_APPLIED / ACTION_NOT_ALLOWED)
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES billing_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_action_runs_correlation ON task_action_runs (correlation_id);
CREATE INDEX IF NOT EXISTS idx_task_action_runs_task ON task_action_runs (task_id);
CREATE INDEX IF NOT EXISTS idx_billing_tasks_status ON billing_tasks (status);

-- ----------------------------------------------------------------------------
-- 固定合成工单夹具 (教学模拟数据，确定性可回放)
-- 设计目标 (保证 E2E 测试 100% 确定通过):
--   * TASK-W07-001 华东/500 元/2026-08-15 -> check 后 answered 命中 POL-2026-BIL-01 v3
--   * TASK-W07-002 华东/2500 元           -> check 后 needs_review / AMOUNT_EXCEEDS_LIMIT
--   * TASK-W07-003 华东/2025-06-15        -> check 后 needs_review / NO_MATCHING_POLICY (过期拦截)
--   * TASK-W07-004 华北/800 元            -> check 后 needs_review / NO_MATCHING_POLICY (地区无政策)
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO billing_tasks
    (task_id, case_id, region, amount, purchased_at, assigned_agent_id, correlation_id)
VALUES
    ('TASK-W07-001', 'NS-CASE-2026-0801-001', '华东', 500.0, '2026-08-15', 'agent_small_zhang', 'corr-w07-fixture-001'),
    ('TASK-W07-002', 'NS-CASE-2026-0801-002', '华东', 2500.0, '2026-08-15', 'agent_small_zhang', 'corr-w07-fixture-002'),
    ('TASK-W07-003', 'NS-CASE-2026-0801-003', '华东', 300.0, '2025-06-15', 'agent_small_zhang', 'corr-w07-fixture-003'),
    ('TASK-W07-004', 'NS-CASE-2026-0801-004', '华北', 800.0, '2026-08-15', 'agent_small_zhang', 'corr-w07-fixture-004');

-- ============================================================================
-- 003_tasks_and_permissions_schema.sql
-- 天窗总成只读诊断 · 阶段 A 节点 04 双角色任务闭环：任务表 + 任务动作审计表 + 固定合成夹具
-- 回链: specs/role-and-permission-matrix.md §2 (冲突/硬限裁决.人工复核: PQE 发起 / 主管裁决)
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 适配说明 (adr-003-task-permission-adaptations.md):
--   1. 不重建 001 冻结的 specs 表 (节点 03 业务键 spec_id:source_version 已绑定表结构);
--   2. 不重建 001 冻结的 task_runs 表 (其 CHECK 约束限定 3 种查询终态且 SQLite
--      无法 ALTER CHECK)，任务动作审计写入独立的 task_action_runs 表;
--   3. spec_check_tasks 增加 oem_project 列: 任务级 ACL 与用户 ACL 交叉校验 (D6)，
--      跨项目 PQE 直调任务走 denied 路径 (零泄漏)，比矩阵最低要求更强。
-- 安全边界: 只保存结构化摘要字段，不保存 SOP 正文 / 图纸 / 密钥 / 个人信息
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 1: spec_check_tasks —— PQE 规格核对任务表 (任务状态机的唯一事实来源)
-- 状态机: initialized -> answered/needs_review -> confirmed/refused/escalated
--         escalated -> action_completed (主管); 越权动作不改变状态 (denied 仅审计)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS spec_check_tasks (
    task_id           TEXT PRIMARY KEY,               -- 任务标识 (业务键)
    case_id           TEXT NOT NULL,                  -- 案例号 (可进入同项目组响应)
    sn                TEXT NOT NULL,                  -- 天窗总成 SN (追溯标识, 任务事实)
    oem_project       TEXT NOT NULL CHECK (oem_project IN ('OEM-A', 'OEM-B')),
                      -- 任务归属项目组 (D6: 与用户 ACL 交叉校验, 跨项目直调 -> denied)
    part_no           TEXT NOT NULL,                  -- 零件业务键 (任务事实)
    process_step      TEXT NOT NULL CHECK (process_step IN ('torque_tightening', 'gap_check')),
    measured_at       TEXT NOT NULL,                  -- 测量日期 (生效窗口判定键, 任务事实)
    measured_value    REAL NOT NULL CHECK (measured_value > 0),  -- 实测值 (任务事实)
    assigned_pqe_id   TEXT NOT NULL,                  -- 责任 PQE (服务端分配，绝不来自请求体自报)
    status            TEXT NOT NULL DEFAULT 'initialized'
                      CHECK (status IN ('initialized', 'answered', 'needs_review', 'confirmed',
                                        'refused', 'escalated', 'denied', 'action_completed')),
    reason_code       TEXT,                           -- 最近一次终态的结构化原因码
    matched_spec_id   TEXT,                           -- answered 时回填的规格 ID (可核验依据)
    idempotency_key   TEXT UNIQUE,                    -- 升级动作幂等键 (首次 escalate 写入)
    correlation_id    TEXT NOT NULL,                  -- 全局关联标识 (响应/DB/审计三向对账键)
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ----------------------------------------------------------------------------
-- 表 2: task_action_runs —— 任务动作与安全审计日志
-- 三向对账锚点: HTTP 响应 (correlation_id) / spec_check_tasks 终态 / 本表动作流水。
-- 越权尝试 (denied) 也必须落一行安全审计事件 (含 task_id/user_id/action)。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_action_runs (
    run_id         TEXT PRIMARY KEY,                  -- run-{uuid12}
    task_id        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    user_id        TEXT NOT NULL,                     -- 服务端可信身份 (非请求体自报)
    user_role      TEXT NOT NULL,
    action         TEXT NOT NULL,                     -- check_spec/confirm/refuse/escalate/supervisor_resolve
    status         TEXT NOT NULL,                     -- 本次动作产生的任务终态 (denied 表示被安全阻断)
    reason_code    TEXT,                              -- 结构化原因码 (如 ALREADY_APPLIED / ACTION_NOT_ALLOWED)
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),  -- 毫秒精度
    FOREIGN KEY (task_id) REFERENCES spec_check_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_action_runs_correlation ON task_action_runs (correlation_id);
CREATE INDEX IF NOT EXISTS idx_task_action_runs_task ON task_action_runs (task_id);
CREATE INDEX IF NOT EXISTS idx_spec_check_tasks_status ON spec_check_tasks (status);

-- ----------------------------------------------------------------------------
-- 固定合成任务夹具 (教学模拟数据，确定性可回放; 全部归属 OEM-A / pqe_chen_oema)
-- 设计目标 (保证任务闭环测试 100% 确定通过, 复用 001 规格夹具的确定性引擎分支):
--   * TASK-N04-001 MOTOR-MOUNT-BOLT-03/9.6 N·m -> check 后 answered 命中 SPEC-SR-A-V5
--     (偏差 = (9.6-9.5)/9.5*100 = +1.05%, 未超 15% 硬限);
--   * TASK-N04-002 同零件/11.5 N·m -> needs_review / SPEC_HARD_LIMIT_EXCEEDED (21.05% 超硬限);
--   * TASK-N04-003 HEAT-SHIELD-02 gap_check -> needs_review / SPEC_CONFLICT (v1.0/v1.1 双现行);
--   * TASK-N04-004 UNKNOWN-PART-99 -> needs_review / NO_MATCHING_SPEC (零匹配).
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO spec_check_tasks
    (task_id, case_id, sn, oem_project, part_no, process_step, measured_at,
     measured_value, assigned_pqe_id, correlation_id)
VALUES
    ('TASK-N04-001', 'SR-CASE-2026-1001-001', 'SR-20261001-0001', 'OEM-A',
     'MOTOR-MOUNT-BOLT-03', 'torque_tightening', '2026-08-15', 9.6,
     'pqe_chen_oema', 'corr-n04-fixture-001'),
    ('TASK-N04-002', 'SR-CASE-2026-1001-002', 'SR-20261001-0002', 'OEM-A',
     'MOTOR-MOUNT-BOLT-03', 'torque_tightening', '2026-08-15', 11.5,
     'pqe_chen_oema', 'corr-n04-fixture-002'),
    ('TASK-N04-003', 'SR-CASE-2026-1001-003', 'SR-20261001-0003', 'OEM-A',
     'HEAT-SHIELD-02', 'gap_check', '2026-08-15', 4.1,
     'pqe_chen_oema', 'corr-n04-fixture-003'),
    ('TASK-N04-004', 'SR-CASE-2026-1001-004', 'SR-20261001-0004', 'OEM-A',
     'UNKNOWN-PART-99', 'torque_tightening', '2026-08-15', 8.0,
     'pqe_chen_oema', 'corr-n04-fixture-004');
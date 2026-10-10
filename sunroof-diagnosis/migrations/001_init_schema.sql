-- ============================================================================
-- 001_init_schema.sql
-- 天窗总成只读诊断 · 阶段 A 节点 02 确定性垂直薄片：DDL 与固定合成 Fixtures
-- 回链: vertical-slice-contract.md / specs/discovery-brief-v1.md 保护指标 P-1/P-2
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE，重复执行不改变已有终态
-- 安全边界: 只保存结构化摘要字段，不保存 SOP 正文 / 图纸 / 密钥 / 个人信息
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 表 1: specs —— QMS 检验规格主数据 (确定性 SQL 查询的唯一事实来源)
-- ACL 下推键: oem_project (跨主机厂隔离, 检索层排除, 不进任何上层上下文)
-- P-2 语义: is_current = 0 的行 (已被替代/过期) 在 SQL 层被排除, 任何路径不可引用
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS specs (
    spec_id              TEXT    NOT NULL PRIMARY KEY,   -- 跨版本稳定标识
    title                TEXT    NOT NULL,               -- 规格标题 (摘要, 可进入响应)
    version              TEXT    NOT NULL,               -- 来源系统中的版本
    part_no              TEXT    NOT NULL,               -- 零件业务键 (如 MOTOR-MOUNT-BOLT-03)
    process_step         TEXT    NOT NULL CHECK (process_step IN ('torque_tightening', 'gap_check')),
    oem_project          TEXT    NOT NULL CHECK (oem_project IN ('OEM-A', 'OEM-B')),
    is_current           INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    effective_at         TEXT    NOT NULL,               -- 生效日期 (ISO-8601, YYYY-MM-DD)
    expires_at           TEXT,                           -- 失效日期 (NULL = 长期有效)
    nominal_value        REAL    NOT NULL CHECK (nominal_value > 0),
    tol_plus             REAL    NOT NULL CHECK (tol_plus >= 0),
    tol_minus            REAL    NOT NULL CHECK (tol_minus >= 0),
    unit                 TEXT    NOT NULL,
    formula_rule         TEXT    NOT NULL DEFAULT '',    -- 确定性公式规则 (结构化, 非模型生成)
    source_ref           TEXT    NOT NULL,               -- 可追溯来源引用 (如 QMS-SOP-SR-04-v5#Section-3.2)
    owner_id             TEXT    NOT NULL,               -- 知识所有者 (治理元数据, 回链第 9 周血缘口径)
    allowed_oem_projects TEXT    NOT NULL DEFAULT '[]',  -- JSON 数组, 检索层 ACL 下推的镜像声明
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (expires_at IS NULL OR expires_at > effective_at)  -- 非法日期关系尽早失败
);

-- ----------------------------------------------------------------------------
-- 表 2: task_runs —— 任务运行审计日志 (三向对账: API 响应 / DB 终态 / 测试)
-- 允许的数据变化: 每次任务查询仅追加一条记录，不产生任何其他写副作用
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_runs (
    run_id               TEXT PRIMARY KEY,              -- run-{uuid12}
    request_id           TEXT NOT NULL,                 -- 客户端关联标识 (三向对账键)
    user_id              TEXT NOT NULL,                 -- 服务端可信身份 (非请求体自报)
    user_role            TEXT NOT NULL,
    allowed_oem_projects TEXT NOT NULL DEFAULT '[]',    -- 请求时生效的 ACL 范围快照
    task_type            TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('answered', 'needs_review', 'denied')),
    reason_code          TEXT,                          -- 异常时为结构化原因码, 成功时为 NULL
    matched_spec_id      TEXT,                          -- answered 时回填规格 ID; 阻断路径恒为 NULL
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_runs_request_id ON task_runs (request_id);
CREATE INDEX IF NOT EXISTS idx_specs_lookup ON specs (part_no, process_step, oem_project, effective_at, is_current);

-- ----------------------------------------------------------------------------
-- 固定合成 Fixtures (全部为教学模拟数据，不代表任何真实产品/主机厂标准)
-- 设计目标 (保证 8 个集成测试 100% 确定通过):
--   * SPEC-SR-A-V5   v5.0 -> 成功路径唯一现行命中 (OEM-A / 2026-06-01 起生效)
--   * SPEC-SR-A-V4   v4.0 -> 已被替代 (is_current=0)，验证 P-2 拦截
--   * SPEC-SR-B-V2   v2.0 -> 与 A 同零件不同主机厂，验证 ACL 下推 (OEM-B 只见自己的)
--   * SPEC-UNIQ-B    v1.0 -> 仅存在于 OEM-B 的零件，验证跨项目零泄漏
--   * SPEC-HS-A-V1/V1B    -> 同零件同工序两条现行规格，验证 SPEC_CONFLICT
-- ----------------------------------------------------------------------------
INSERT OR IGNORE INTO specs
    (spec_id, title, version, part_no, process_step, oem_project, is_current,
     effective_at, expires_at, nominal_value, tol_plus, tol_minus, unit,
     formula_rule, source_ref, owner_id, allowed_oem_projects)
VALUES
    ('SPEC-SR-A-V5', '天窗电机固定螺栓拧紧扭矩检验标准', 'v5.0',
     'MOTOR-MOUNT-BOLT-03', 'torque_tightening', 'OEM-A', 1,
     '2026-06-01', NULL, 9.5, 0.5, 0.5, 'N·m',
     'lower = nominal - tol_minus; upper = nominal + tol_plus',
     'QMS-SOP-SR-04-v5#Section-3.2', 'owner-qms-sr', '["OEM-A"]'),
    ('SPEC-SR-A-V4', '天窗电机固定螺栓拧紧扭矩检验标准（已被 v5.0 替代）', 'v4.0',
     'MOTOR-MOUNT-BOLT-03', 'torque_tightening', 'OEM-A', 0,
     '2025-01-01', '2026-05-31', 9.5, 0.8, 0.8, 'N·m',
     'legacy_rule_v4 (superseded)',
     'QMS-SOP-SR-04-v4#Section-2.5', 'owner-qms-sr', '["OEM-A"]'),
    ('SPEC-SR-B-V2', 'OEM-B 天窗电机固定螺栓拧紧扭矩检验标准', 'v2.0',
     'MOTOR-MOUNT-BOLT-03', 'torque_tightening', 'OEM-B', 1,
     '2026-03-01', NULL, 10.5, 0.5, 0.5, 'N·m',
     'lower = nominal - tol_minus; upper = nominal + tol_plus',
     'QMS-SOP-SR-B-02-v2#Section-1.4', 'owner-qms-sr-b', '["OEM-B"]'),
    ('SPEC-UNIQ-B', 'OEM-B 遮阳帘导轨装配间隙检验标准（限 OEM-B 项目组）', 'v1.0',
     'SUNSHADE-RAIL-B7', 'gap_check', 'OEM-B', 1,
     '2026-01-01', NULL, 4.2, 0.3, 0.3, 'mm',
     'lower = nominal - tol_minus; upper = nominal + tol_plus',
     'QMS-SOP-SR-B-07-v1#Section-5.1', 'owner-qms-sr-b', '["OEM-B"]'),
    ('SPEC-HS-A-V1', 'OEM-A 隔热板装配间隙检验标准', 'v1.0',
     'HEAT-SHIELD-02', 'gap_check', 'OEM-A', 1,
     '2026-02-01', NULL, 4.0, 0.5, 0.5, 'mm',
     'lower = nominal - tol_minus; upper = nominal + tol_plus',
     'QMS-SOP-SR-HS-01-v1#Section-4.2', 'owner-qms-hs', '["OEM-A"]'),
    ('SPEC-HS-A-V1B', 'OEM-A 隔热板装配间隙检验标准（现行冲突版本）', 'v1.1',
     'HEAT-SHIELD-02', 'gap_check', 'OEM-A', 1,
     '2026-02-01', NULL, 4.6, 0.5, 0.5, 'mm',
     'lower = nominal - tol_minus; upper = nominal + tol_plus',
     'QMS-SOP-SR-HS-01-v1B#Section-4.2', 'owner-qms-hs', '["OEM-A"]');

"""
数据库访问层 (Database Access Layer)
管理 SQLite 关系型数据库的连接、初始化、确定性 SQL 查询与审计日志落盘

设计要点:
1. 确定性优先: 查询只依赖 SQL 规则 (地区/生效时间/废弃标记/访问级别)，零随机、零模型调用
2. 幂等初始化: 重复执行迁移脚本不改变已有终态 (CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE)
3. 三向对账: record_task_run 保存 request_id，使 API 响应、数据库终态与测试可互相对账
"""
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

# 默认数据库文件位于项目根目录 data/ 下 (Windows/Linux 通用路径)；
# 可通过环境变量 FDE_DB_PATH 覆盖 (集成测试用临时库隔离即依赖此机制)。
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "northstar.db"
)

# 迁移脚本按序执行 (001 = Week 05 基线 DDL + Fixtures; 002 = Week 06 摄取/隔离/重放表;
#                     003 = Week 07 工单表 + 任务动作审计表 + 工单夹具)
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "migrations")
MIGRATION_SCRIPTS = ["001_init_schema.sql", "002_ingestion_schema.sql", "003_tasks_and_permissions_schema.sql"]

# Week 06 血缘列: 通过幂等 ALTER 追加到 Week 05 已冻结的 policies 表，
# 不重建表、不改动既有主键 (policy_id, version) 与三条种子 Fixtures。
POLICY_LINEAGE_COLUMNS = {
    "source_version": "TEXT NOT NULL DEFAULT ''",  # 上游源版本 (业务键 policy_id:source_version)
    "owner_id": "TEXT",                            # 知识所有者 (缺失即隔离)
    "batch_id": "TEXT NOT NULL DEFAULT ''",        # 摄取批次 (血缘/对账锚点)
}

# Week 05 种子数据的血缘口径: 迁移后统一回填，使主表每个业务版本均可追溯
SEED_BATCH_ID = "week05-baseline-seed"


def _get_db_path() -> str:
    """每次调用时解析数据库路径，支持测试通过环境变量注入临时库。"""
    return os.environ.get("FDE_DB_PATH") or _DEFAULT_DB_PATH


def get_db_connection() -> sqlite3.Connection:
    """获取行级字典访问 (sqlite3.Row) 的 SQLite 连接。"""
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_policy_lineage_columns(conn: sqlite3.Connection) -> None:
    """幂等为 policies 表补齐 Week 06 血缘列 (PRAGMA 检查后 ALTER)，并创建对账索引。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(policies)")}
    for column, ddl in POLICY_LINEAGE_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE policies ADD COLUMN {column} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_policies_batch ON policies (batch_id)"
    )


def init_db(db_path: Optional[str] = None) -> None:
    """按序执行迁移脚本初始化数据库 (幂等，可重复执行):
    1. 001_init_schema.sql: Week 05 表结构 + 固定 Fixtures (IF NOT EXISTS / OR IGNORE)
    2. 002_ingestion_schema.sql: Week 06 批次 / 隔离区 / 重放审计表
    3. 幂等补齐 policies 血缘列 (source_version / owner_id / batch_id) 并回填种子血缘
    """
    path = db_path or _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for script_name in MIGRATION_SCRIPTS:
            script_path = os.path.join(MIGRATIONS_DIR, script_name)
            with open(script_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        _ensure_policy_lineage_columns(conn)
        # 种子血缘回填 (幂等): 仅命中血缘列为空的历史行，不触碰摄取管道写入的数据
        conn.execute(
            "UPDATE policies SET batch_id = ? WHERE batch_id = ''", (SEED_BATCH_ID,)
        )
        conn.execute(
            "UPDATE policies SET source_version = version WHERE source_version = ''"
        )
        conn.commit()
    finally:
        conn.close()


def query_policies(region: str, purchased_at: str, user_role: str) -> List[Dict[str, Any]]:
    """
    确定性 SQL 查询 (100% 规则可解释):
    1. 匹配地区（指定地区或全国）；
    2. 过滤未废弃 (is_deprecated = 0) 且在购买时间点已生效 (effective_at <= purchased_at) 且未过期；
    3. 服务端角色权限隔离 (access_level: supervisor 可见全部，support_agent 仅见 support_agent)。

    按 effective_at 倒序返回，保证"最新适用版本优先"的确定性排序。
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        sql = """
        SELECT policy_id, title, version, region, access_level, is_deprecated, effective_at, expires_at, formula_rule, source_id
        FROM policies
        WHERE (region = ? OR region = '全国')
          AND is_deprecated = 0
          AND effective_at <= ?
          AND (expires_at IS NULL OR expires_at > ?)
          AND (
              access_level = ?
              OR (access_level = 'support_agent' AND ? IN ('support_agent', 'supervisor'))
          )
        ORDER BY effective_at DESC
        """

        cursor.execute(sql, (region, purchased_at, purchased_at, user_role, user_role))
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
        return results
    finally:
        conn.close()


def record_task_run(
    request_id: str,
    user_id: str,
    user_role: str,
    task_type: str,
    status: str,
    reason_code: Optional[str] = None,
    matched_policy_id: Optional[str] = None,
) -> str:
    """向 task_runs 表记录运行审计日志 (三向对账的数据侧锚点)。"""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        sql = """
        INSERT INTO task_runs (run_id, request_id, user_id, user_role, task_type, status, reason_code, matched_policy_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        cursor.execute(
            sql,
            (run_id, request_id, user_id, user_role, task_type, status, reason_code, matched_policy_id),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def get_task_run_by_request_id(request_id: str) -> Optional[Dict[str, Any]]:
    """根据 request_id 获取审计记录，供集成测试断言数据库终态。"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_runs WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================================
# Week 07: 客服工单任务表与动作审计 (回链 specs/mission-week-07.md 第 2 天)
# 现有 Week 05/06 函数零改动 —— 仅追加新能力。
# ============================================================================


def get_billing_task(task_id: str) -> Optional[Dict[str, Any]]:
    """按 task_id 读取工单终态 (任务状态机的唯一事实来源)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM billing_tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_billing_tasks() -> List[Dict[str, Any]]:
    """列出全部工单 (一线工作台任务队列数据源)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM billing_tasks ORDER BY created_at ASC, task_id ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_billing_task_state(
    task_id: str,
    status: str,
    reason_code: Optional[str] = None,
    matched_policy_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    """写入工单终态 (单事务); None 字段用 COALESCE 保留原值，保证幂等重放不覆盖既有依据"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE billing_tasks
            SET status = ?,
                reason_code = COALESCE(?, reason_code),
                matched_policy_id = COALESCE(?, matched_policy_id),
                idempotency_key = COALESCE(?, idempotency_key),
                updated_at = datetime('now')
            WHERE task_id = ?
            """,
            (status, reason_code, matched_policy_id, idempotency_key, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_task_action_run(
    task_id: str,
    correlation_id: str,
    user_id: str,
    user_role: str,
    action: str,
    status: str,
    reason_code: Optional[str] = None,
) -> str:
    """记录一条任务动作审计事件 (三向对账锚点: 响应 / billing_tasks / 本表)"""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO task_action_runs
                (run_id, task_id, correlation_id, user_id, user_role, action, status, reason_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, task_id, correlation_id, user_id, user_role, action, status, reason_code),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def get_task_action_runs_by_correlation(correlation_id: str) -> List[Dict[str, Any]]:
    """按 correlation_id 汇集动作审计流水，供 E2E 测试三向对账断言"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM task_action_runs WHERE correlation_id = ? ORDER BY created_at ASC, run_id ASC",
            (correlation_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


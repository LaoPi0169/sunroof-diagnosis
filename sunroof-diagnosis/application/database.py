"""
数据库访问层 (Database Access Layer)
管理 SQLite 关系型数据库的连接、初始化、确定性 SQL 查询与审计日志落盘

设计要点:
1. 确定性优先: 查询只依赖 SQL 规则 (零件/工序/生效窗口/现行标记/项目组 ACL)，零随机、零模型调用
2. 幂等初始化: 重复执行迁移脚本不改变已有终态 (CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE)
3. ACL 下推: allowed_oem_projects 作为 SQL WHERE 条件下推，其他项目组的行根本不进入结果集
4. 三向对账: record_task_run 保存 request_id，使 API 响应、数据库终态与测试可互相对账
"""
import json
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

# 默认数据库文件位于本项目根目录 data/ 下 (Windows/Linux 通用路径)；
# 可通过环境变量 FDE_DB_PATH 覆盖 (集成测试用临时库隔离即依赖此机制)。
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "sunroof.db"
)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "migrations")
# 迁移脚本按序执行 (001 = 节点 02 基线 DDL + Fixtures; 002 = 节点 03 摄取/隔离/重放表;
#                    003 = 节点 04 任务表 + 任务动作审计表 + 任务夹具)
MIGRATION_SCRIPTS = [
    "001_init_schema.sql",
    "002_ingestion_schema.sql",
    "003_tasks_and_permissions_schema.sql",
]

# 节点 03 血缘列: 通过幂等 ALTER 追加到 001 已冻结的 specs 表，
# 不重建表、不改动既有主键 (spec_id) 与六条种子 Fixtures (回链 specs/data-contract-v1.md §8)
SPEC_LINEAGE_COLUMNS = {
    "source_id": "TEXT NOT NULL DEFAULT ''",       # 数据源标识 (血缘)
    "source_version": "TEXT NOT NULL DEFAULT ''",  # 上游源版本 (业务键 spec_id:source_version)
    "batch_id": "TEXT NOT NULL DEFAULT ''",        # 摄取批次 (血缘/对账锚点)
}

# 节点 02 种子数据的血缘口径: 迁移后统一回填，使主表每条规格均可追溯
SEED_BATCH_ID = "node02-baseline-seed"


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


def _ensure_spec_lineage_columns(conn: sqlite3.Connection) -> None:
    """幂等为 specs 表补齐节点 03 血缘列 (PRAGMA 检查后 ALTER)，并创建对账索引。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(specs)")}
    for column, ddl in SPEC_LINEAGE_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE specs ADD COLUMN {column} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_specs_batch ON specs (batch_id)"
    )


def init_db(db_path: Optional[str] = None) -> None:
    """按序执行迁移脚本初始化数据库 (幂等，可重复执行):
    1. 001_init_schema.sql: 节点 02 表结构 + 固定 Fixtures (IF NOT EXISTS / OR IGNORE)
    2. 002_ingestion_schema.sql: 节点 03 批次 / 隔离区 / 重放审计表
    3. 幂等补齐 specs 血缘列 (source_id / source_version / batch_id) 并回填种子血缘
    """
    path = db_path or _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for script_name in MIGRATION_SCRIPTS:
            script_path = os.path.join(MIGRATIONS_DIR, script_name)
            with open(script_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        _ensure_spec_lineage_columns(conn)
        # 种子血缘回填 (幂等): 仅命中血缘列为空的历史行，不触碰摄取管道写入的数据
        conn.execute(
            "UPDATE specs SET batch_id = ? WHERE batch_id = ''", (SEED_BATCH_ID,)
        )
        conn.execute(
            "UPDATE specs SET source_version = version WHERE source_version = ''"
        )
        conn.execute(
            "UPDATE specs SET source_id = CASE WHEN instr(source_ref, '#') > 0 "
            "THEN substr(source_ref, 1, instr(source_ref, '#') - 1) "
            "ELSE source_ref END WHERE source_id = ''"
        )
        conn.commit()
    finally:
        conn.close()


def query_current_specs(
    part_no: str, process_step: str, measured_at: str, allowed_oem_projects: List[str]
) -> List[Dict[str, Any]]:
    """
    确定性 SQL 查询: 在请求者 ACL 范围内匹配「现行有效」规格。

    过滤规则 (全部在 SQL 层完成, 保护指标 P-1/P-2):
    1. part_no + process_step 精确匹配 (业务键);
    2. is_current = 1 —— 已被替代/过期版本被排除, 任何路径不可引用;
    3. effective_at <= measured_at 且 (expires_at IS NULL OR expires_at > measured_at);
    4. oem_project IN (allowed_oem_projects) —— ACL 下推; ["*"] (质量主管) 跨项目只读。
    排序: effective_at DESC (同窗口多行时取最新, 供上层冲突检测)。
    """
    conn = get_db_connection()
    try:
        where = (
            "part_no = ? AND process_step = ? "
            "AND is_current = 1 "
            "AND effective_at <= ? "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: List[Any] = [part_no, process_step, measured_at, measured_at]
        if allowed_oem_projects != ["*"]:
            placeholders = ", ".join("?" for _ in allowed_oem_projects)
            where += f" AND oem_project IN ({placeholders})"
            params += list(allowed_oem_projects)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM specs WHERE {where} ORDER BY effective_at DESC",
            tuple(params),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def record_task_run(
    request_id: str,
    user: Any,
    task_type: str,
    status: str,
    reason_code: Optional[str],
    matched_spec_id: Optional[str],
) -> str:
    """写入一条任务审计记录 (三向对账锚点: 响应 / task_runs / 测试共用 request_id)。"""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_runs
                (run_id, request_id, user_id, user_role, allowed_oem_projects,
                 task_type, status, reason_code, matched_spec_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                user.user_id,
                user.user_role,
                json.dumps(user.allowed_oem_projects, ensure_ascii=False),
                task_type,
                status,
                reason_code,
                matched_spec_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def get_task_run_by_request_id(request_id: str) -> Optional[Dict[str, Any]]:
    """按 request_id 读取任务审计终态 (测试三向对账用)。"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_runs WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================================
# 节点 04: 任务闭环数据访问 (spec_check_tasks + task_action_runs)
# 三向对账锚点: HTTP 响应 (correlation_id) / spec_check_tasks 终态 / task_action_runs 流水
# ============================================================================

def get_spec_check_task(task_id: str) -> Optional[Dict[str, Any]]:
    """按 task_id 读取任务终态 (任务状态机的唯一事实来源)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM spec_check_tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_spec_check_tasks() -> List[Dict[str, Any]]:
    """列出全部任务 (工作台任务队列数据源; 端点层按用户 ACL 过滤后返回)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM spec_check_tasks ORDER BY created_at ASC, task_id ASC"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_task_state(
    task_id: str,
    status: str,
    reason_code: Optional[str] = None,
    matched_spec_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    """写入任务终态 (单事务); None 字段用 COALESCE 保留原值，保证幂等重放不覆盖既有依据"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE spec_check_tasks
            SET status = ?,
                reason_code = COALESCE(?, reason_code),
                matched_spec_id = COALESCE(?, matched_spec_id),
                idempotency_key = COALESCE(?, idempotency_key),
                updated_at = datetime('now')
            WHERE task_id = ?
            """,
            (status, reason_code, matched_spec_id, idempotency_key, task_id),
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
    """记录一条任务动作审计事件 (三向对账锚点: 响应 / spec_check_tasks / 本表)"""
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
    """按 correlation_id 汇集动作审计流水，供 E2E 测试三向对账断言 (按插入序 rowid 排序)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM task_action_runs WHERE correlation_id = ? ORDER BY rowid ASC",
            (correlation_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

"""
数据库访问层 (Database Access Layer)
管理 SQLite 关系型数据库的连接、初始化、确定性 SQL 查询与审计日志落盘

设计要点:
1. 确定性优先: 查询只依赖 SQL 规则 (零件/工序/生效窗口/现行标记/项目组 ACL)，零随机、零模型调用
2. 幂等初始化: 重复执行迁移脚本不改变已有终态 (CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE)
3. ACL 下推: allowed_oem_projects 作为 SQL WHERE 条件下推，其他项目组的行根本不进入结果集
4. 三向对账: record_task_run 保存 request_id，使 API 响应、数据库终态与测试可互相对账
"""
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 默认数据库文件位于本项目根目录 data/ 下 (Windows/Linux 通用路径)；
# 可通过环境变量 FDE_DB_PATH 覆盖 (集成测试用临时库隔离即依赖此机制)。
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "sunroof.db"
)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "migrations")
# 迁移脚本按序执行 (001 = 节点 02 基线 DDL + Fixtures; 002 = 节点 03 摄取/隔离/重放表;
#                    003 = 节点 04 任务表 + 任务动作审计表 + 任务夹具;
#                    004 = 节点 05 发布配置表 policy_config + 种子默认值;
#                    005 = 阶段 D 批次 1 审计哈希链头表 + 签名令牌吊销表)
MIGRATION_SCRIPTS = [
    "001_init_schema.sql",
    "002_ingestion_schema.sql",
    "003_tasks_and_permissions_schema.sql",
    "004_deployment_schema.sql",
    "005_production_hardening_schema.sql",
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
    """获取行级字典访问 (sqlite3.Row) 的 SQLite 连接。

    阶段 D 批次 1 (adr-005 D7): WAL (写不阻塞读) + busy_timeout=5000 (写并发
    排队等待而非立即 SQLITE_BUSY)。journal_mode 是库级持久属性, 随库文件生效。
    """
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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


# ============================================================================
# 阶段 D 批次 1 (adr-005 D1/D2/D3): 乐观锁版本列 + 审计哈希链
# ============================================================================

# 冻结表幂等追加列 (PRAGMA 检查后 ALTER; 与节点 03 specs 血缘列同一先例, 不重建表)
TASK_VERSION_COLUMN = {"version": "INTEGER NOT NULL DEFAULT 0"}
AUDIT_CHAIN_COLUMNS = {
    "prev_entry_hash": "TEXT",   # 前一审计行的 entry_hash (空串 = 链首)
    "entry_hash": "TEXT",        # 本行哈希 (口径见 _audit_entry_hash, 逐字节可复算)
}

# 哈希链载荷字段 (冻结, adr-005 D2): canonical_json 只含业务事实字段, 不含哈希列本身
AUDIT_CHAIN_HASH_FIELDS = (
    "run_id", "task_id", "correlation_id", "user_id", "user_role",
    "action", "status", "reason_code", "created_at",
)


def _utcnow_ms() -> str:
    """毫秒精度 UTC 时间戳 (与迁移内 strftime('%Y-%m-%dT%H:%M:%fZ','now') 同格式)。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _audit_entry_hash(entry: Dict[str, Any], prev_hash: str) -> str:
    """entry_hash = sha256(prev + "|" + canonical_json); 确定性可复算 (adr-005 D2)。"""
    canonical = json.dumps(
        {key: entry.get(key) for key in AUDIT_CHAIN_HASH_FIELDS},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(f"{prev_hash}|{canonical}".encode("utf-8")).hexdigest()


def _ensure_task_version_column(conn: sqlite3.Connection) -> None:
    """幂等为 spec_check_tasks 补乐观锁版本列 (D1: 不重建冻结表)。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(spec_check_tasks)")}
    for column, ddl in TASK_VERSION_COLUMN.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE spec_check_tasks ADD COLUMN {column} {ddl}")


def _ensure_audit_chain_columns(conn: sqlite3.Connection) -> None:
    """幂等为 task_action_runs 补哈希链列 (D2: 不重建冻结表)。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(task_action_runs)")}
    for column, ddl in AUDIT_CHAIN_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE task_action_runs ADD COLUMN {column} {ddl}")


def _backfill_audit_chain(conn: sqlite3.Connection) -> int:
    """历史审计行一次性补链 (D3: 只补两个哈希注记列, 业务字段零改动; 幂等)。"""
    head = conn.execute("SELECT last_entry_hash FROM audit_chain_head WHERE id = 1").fetchone()
    prev = head["last_entry_hash"] if head else ""
    pending = conn.execute(
        "SELECT * FROM task_action_runs WHERE entry_hash IS NULL ORDER BY rowid ASC"
    ).fetchall()
    if not pending:
        return 0
    for row in pending:
        entry = dict(row)
        entry_hash = _audit_entry_hash(entry, prev)
        conn.execute(
            "UPDATE task_action_runs SET prev_entry_hash = ?, entry_hash = ? WHERE run_id = ?",
            (prev, entry_hash, entry["run_id"]),
        )
        prev = entry_hash
    conn.execute(
        "UPDATE audit_chain_head SET last_entry_hash = ?, last_run_id = ?, "
        "entry_count = entry_count + ?, updated_at = ? WHERE id = 1",
        (prev, pending[-1]["run_id"], len(pending), _utcnow_ms()),
    )
    return len(pending)


def init_db(db_path: Optional[str] = None) -> None:
    """按序执行迁移脚本初始化数据库 (幂等，可重复执行):
    1. 001_init_schema.sql: 节点 02 表结构 + 固定 Fixtures (IF NOT EXISTS / OR IGNORE)
    2. 002_ingestion_schema.sql: 节点 03 批次 / 隔离区 / 重放审计表
    3. 幂等补齐 specs 血缘列 (source_id / source_version / batch_id) 并回填种子血缘
    """
    path = db_path or _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # 链回填/血缘回填按列名访问 (迁移为纯 DDL/DML, 不受影响)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        for script_name in MIGRATION_SCRIPTS:
            script_path = os.path.join(MIGRATIONS_DIR, script_name)
            with open(script_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        _ensure_spec_lineage_columns(conn)
        # 阶段 D 批次 1 (adr-005 D1/D2/D3): 乐观锁版本列 + 审计哈希链列 + 历史行补链
        # (全部幂等: PRAGMA 检查后 ALTER / 只命中空链行)
        _ensure_task_version_column(conn)
        _ensure_audit_chain_columns(conn)
        _backfill_audit_chain(conn)
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
    expected_version: Optional[int] = None,
) -> bool:
    """写入任务终态 (单事务); None 字段用 COALESCE 保留原值，保证幂等重放不覆盖既有依据。

    阶段 D 乐观锁 (adr-005 D1/D4): expected_version 提供时仅当读时版本仍一致才更新
    (SET version = version + 1); 不一致返回 False (乐观锁冲突, 任务终态不变)。
    缺省 None 与冻结基线行为等价 (无条件更新 + 版本自增)。
    返回: True=已应用, False=版本过期未生效 (调用方转结构化 CONCURRENCY_CONFLICT)。
    """
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
                version = version + 1,
                updated_at = datetime('now')
            WHERE task_id = ? AND (? IS NULL OR version = ?)
            """,
            (
                status, reason_code, matched_spec_id, idempotency_key,
                task_id, expected_version, expected_version,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
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
    """记录一条任务动作审计事件 (三向对账锚点: 响应 / spec_check_tasks / 本表)。

    阶段 D 防篡改 (adr-005 D2): 同事务 (BEGIN IMMEDIATE) 「读链头 -> 算哈希 ->
    追加行 -> 推进链头」; 任何业务字段篡改/删行/链头破坏可被 verify_audit_chain 定位。
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    created_at = _utcnow_ms()
    entry = {
        "run_id": run_id,
        "task_id": task_id,
        "correlation_id": correlation_id,
        "user_id": user_id,
        "user_role": user_role,
        "action": action,
        "status": status,
        "reason_code": reason_code,
        "created_at": created_at,
    }
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        head = conn.execute(
            "SELECT last_entry_hash FROM audit_chain_head WHERE id = 1"
        ).fetchone()
        prev = head["last_entry_hash"] if head else ""
        entry_hash = _audit_entry_hash(entry, prev)
        conn.execute(
            """
            INSERT INTO task_action_runs
                (run_id, task_id, correlation_id, user_id, user_role, action,
                 status, reason_code, created_at, prev_entry_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, task_id, correlation_id, user_id, user_role, action,
                status, reason_code, created_at, prev, entry_hash,
            ),
        )
        conn.execute(
            "UPDATE audit_chain_head SET last_entry_hash = ?, last_run_id = ?, "
            "entry_count = entry_count + 1, updated_at = ? WHERE id = 1",
            (entry_hash, run_id, created_at),
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


# ============================================================================
# 节点 05: 发布配置数据访问 (policy_config —— 收敛器唯一可写面, 引擎只读)
# ============================================================================

def get_policy_config_value(config_key: str) -> Optional[str]:
    """按配置键读取发布配置值 (引擎侧阈值来源; 缺失时返回 None 由调用方回退默认)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT config_value FROM policy_config WHERE config_key = ?", (config_key,)
        )
        row = cursor.fetchone()
        return row["config_value"] if row else None
    finally:
        conn.close()


def list_policy_config() -> List[Dict[str, Any]]:
    """列出全部发布配置 (回滚演练/对账核验用: 收敛前后投影一致 = 幂等)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM policy_config ORDER BY config_key ASC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


# ============================================================================
# 阶段 D 批次 1: 审计哈希链校验 (adr-005 D2/D3) —— 防篡改的核验面
# ============================================================================

def get_audit_chain_head() -> Optional[Dict[str, Any]]:
    """读取链头单行 (CI STAGE-1 与运维校验用)。"""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM audit_chain_head WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def verify_audit_chain(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """哈希链三重校验: 逐行重算哈希 + 前向链接一致 + 链头与末行/行数一致。

    任何业务字段篡改 (entry_hash_mismatch) / 删行或乱序 (prev_hash_mismatch) /
    未入链行 (missing_entry_hash) / 链头破坏 (head_ok=False) 都会令 ok=False,
    并定位首个坏 run_id。传 conn 可复用外部连接 (CI 临时库), 缺省读默认库。
    """
    owns_conn = conn is None
    if owns_conn:
        conn = get_db_connection()
    try:
        # 游标级 Row 行工厂: 兼容外部传入的裸连接 (CI 临时库), 按列名访问不依赖调用方设置
        reader = conn.cursor()
        reader.row_factory = sqlite3.Row
        rows = reader.execute(
            "SELECT * FROM task_action_runs ORDER BY rowid ASC"
        ).fetchall()
        head = reader.execute("SELECT * FROM audit_chain_head WHERE id = 1").fetchone()
        prev = ""
        first_bad = None
        for row in rows:
            entry = dict(row)
            if entry["entry_hash"] is None:
                first_bad = {"run_id": entry["run_id"], "reason": "missing_entry_hash"}
                break
            if entry["prev_entry_hash"] != prev:
                first_bad = {"run_id": entry["run_id"], "reason": "prev_hash_mismatch"}
                break
            if _audit_entry_hash(entry, prev) != entry["entry_hash"]:
                first_bad = {"run_id": entry["run_id"], "reason": "entry_hash_mismatch"}
                break
            prev = entry["entry_hash"]
        head_ok = (
            bool(head)
            and head["last_entry_hash"] == prev
            and head["entry_count"] == len(rows)
        )
        return {
            "ok": first_bad is None and head_ok,
            "entry_count": len(rows),
            "first_bad": first_bad,
            "head_ok": head_ok,
        }
    finally:
        if owns_conn:
            conn.close()

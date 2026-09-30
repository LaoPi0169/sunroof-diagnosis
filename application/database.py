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

SQL_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "migrations", "001_init_schema.sql"
)


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


def init_db(db_path: Optional[str] = None) -> None:
    """根据 migration SQL 脚本初始化数据库表结构与 Fixtures (幂等，可重复执行)。"""
    path = db_path or _get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        with open(SQL_SCRIPT_PATH, "r", encoding="utf-8") as f:
            sql_script = f.read()
        conn.executescript(sql_script)
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

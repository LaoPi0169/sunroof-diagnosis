"""
Ingestion Repository Layer (阶段 A 节点 03)
SQLite 持久化层: 批次汇总、specs 主表写入 (带血缘)、隔离区、定向重放事务与三处一致对账。

设计要点 (回链 specs/data-contract-v1.md):
1. 复用 application.database 的连接管理与 FDE_DB_PATH 机制 (单一事实来源，测试可注入临时库)
2. 批次内写操作均接收外部连接 conn，由 service 层控制单事务原子提交 (无部分提交)
3. resolve_quarantine_and_replay 是自包含原子事务: 主表写入 + 隔离 resolved + 重放审计
4. 安全边界: raw_payload 仅落隔离区证据存储，绝不进入普通结构化日志
"""
import json
import sqlite3
import uuid
from typing import Any, Dict, Optional, Tuple

from application.database import get_db_connection


def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """按 batch_id 读取批次汇总行 (幂等判定的依据)。"""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    source_id: str,
    source_version: str,
    total_rows: int,
) -> None:
    """在当前事务内登记批次 (processing 终态由事务结束时的 update_batch_summary 覆盖)。"""
    conn.execute(
        """
        INSERT INTO ingestion_batches
            (batch_id, source_id, source_version, total_rows,
             accepted_count, duplicate_count, quarantined_count, status)
        VALUES (?, ?, ?, ?, 0, 0, 0, 'processing')
        """,
        (batch_id, source_id, source_version, total_rows),
    )


def update_batch_summary(
    conn: sqlite3.Connection,
    batch_id: str,
    accepted: int,
    duplicate: int,
    quarantined: int,
    status: str,
) -> None:
    """在当前事务内写入批次终态与三处一致统计。"""
    conn.execute(
        """
        UPDATE ingestion_batches
        SET accepted_count = ?, duplicate_count = ?, quarantined_count = ?,
            status = ?, updated_at = datetime('now')
        WHERE batch_id = ?
        """,
        (accepted, duplicate, quarantined, status, batch_id),
    )


def spec_key_exists(conn: sqlite3.Connection, spec_id: str, source_version: str) -> bool:
    """稳定业务键 (spec_id + source_version) 查重 —— 幂等与去重的唯一依据，绝不用行号。"""
    row = conn.execute(
        "SELECT 1 FROM specs WHERE spec_id = ? AND source_version = ?",
        (spec_id, source_version),
    ).fetchone()
    return row is not None


def spec_id_taken(conn: sqlite3.Connection, spec_id: str, source_version: str) -> bool:
    """spec_id 已被其他 source_version 占用 -> 上游修订必须换发新 spec_id (版本化 ID 惯例)。"""
    row = conn.execute(
        "SELECT 1 FROM specs WHERE spec_id = ? AND source_version != ?",
        (spec_id, source_version),
    ).fetchone()
    return row is not None


def insert_accepted_spec(
    conn: sqlite3.Connection, payload: Dict[str, Any], batch_id: str
) -> None:
    """accepted 行写入主表: 携带完整血缘 (source_id / source_version / batch_id / owner_id)。"""
    conn.execute(
        """
        INSERT INTO specs (
            spec_id, title, version, part_no, process_step, oem_project, is_current,
            effective_at, expires_at, nominal_value, tol_plus, tol_minus, unit,
            formula_rule, source_ref, owner_id, allowed_oem_projects,
            source_id, source_version, batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["spec_id"],
            payload["title"],
            payload["version"],
            payload["part_no"],
            payload["process_step"],
            payload["oem_project"],
            payload["is_current"],
            payload["effective_at"],
            payload.get("expires_at"),
            payload["nominal_value"],
            payload["tol_plus"],
            payload["tol_minus"],
            payload["unit"],
            payload["formula_rule"],
            payload["source_ref"],
            payload["owner_id"],
            payload["allowed_oem_projects"],
            payload["source_id"],
            payload["source_version"],
            batch_id,
        ),
    )
def insert_quarantine_record(
    conn: sqlite3.Connection,
    batch_id: str,
    safe_source_ref: str,
    raw_payload: Dict[str, Any],
    reason_code: str,
) -> str:
    """隔离行落库 (仅进入证据存储; raw_payload 绝不进入普通结构化日志)。"""
    quarantine_id = f"quar-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO quarantine_records
            (quarantine_id, batch_id, safe_source_ref, raw_payload, reason_code, status)
        VALUES (?, ?, ?, ?, ?, 'quarantined')
        """,
        (
            quarantine_id,
            batch_id,
            safe_source_ref,
            json.dumps(raw_payload, ensure_ascii=False),
            reason_code,
        ),
    )
    return quarantine_id


def get_quarantine_record(quarantine_id: str) -> Optional[Dict[str, Any]]:
    """读取单条隔离记录 (raw_payload 反序列化为字典)。"""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM quarantine_records WHERE quarantine_id = ?", (quarantine_id,)
        ).fetchone()
        if not row:
            return None
        record = dict(row)
        record["raw_payload"] = json.loads(record["raw_payload"])
        return record
    finally:
        conn.close()


def record_failed_replay(
    replay_id: str,
    quarantine_id: str,
    batch_id: str,
    replayed_by: str,
    fixed_payload: Dict[str, Any],
    fail_reason: str,
) -> None:
    """失败重放尝试同样落审计 (修复历史完整保留，便于 runbook 排查)。"""
    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO ingestion_replay_logs
                (replay_id, quarantine_id, batch_id, replayed_by, fixed_payload,
                 new_spec_id, status, notes)
            VALUES (?, ?, ?, ?, ?, NULL, 'failed', ?)
            """,
            (
                replay_id,
                quarantine_id,
                batch_id,
                replayed_by,
                json.dumps(fixed_payload, ensure_ascii=False),
                fail_reason,
            ),
        )
        conn.commit()
    finally:
        conn.close()
def resolve_quarantine_and_replay(
    quarantine_id: str,
    replay_id: str,
    fixed_payload: Dict[str, Any],
    replayed_by: str,
    notes: Optional[str] = None,
) -> Tuple[bool, str]:
    """定向重放原子事务: 写入主表 (血缘批次 = replay-{replay_id})
    + 隔离记录置 resolved 并回填 replay_id + 落重放审计日志。
    任一步失败则整体回滚，不产生部分提交。"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        source = cursor.execute(
            "SELECT batch_id, status FROM quarantine_records WHERE quarantine_id = ?",
            (quarantine_id,),
        ).fetchone()
        if source is None:
            return False, f"Quarantine record {quarantine_id} not found"
        if source["status"] != "quarantined":
            return False, f"Quarantine record is not replayable (status={source['status']})"

        cursor.execute(
            """
            INSERT INTO specs (
                spec_id, title, version, part_no, process_step, oem_project, is_current,
                effective_at, expires_at, nominal_value, tol_plus, tol_minus, unit,
                formula_rule, source_ref, owner_id, allowed_oem_projects,
                source_id, source_version, batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fixed_payload["spec_id"],
                fixed_payload["title"],
                fixed_payload["version"],
                fixed_payload["part_no"],
                fixed_payload["process_step"],
                fixed_payload["oem_project"],
                fixed_payload["is_current"],
                fixed_payload["effective_at"],
                fixed_payload.get("expires_at"),
                fixed_payload["nominal_value"],
                fixed_payload["tol_plus"],
                fixed_payload["tol_minus"],
                fixed_payload["unit"],
                fixed_payload["formula_rule"],
                fixed_payload["source_ref"],
                fixed_payload["owner_id"],
                fixed_payload["allowed_oem_projects"],
                fixed_payload["source_id"],
                fixed_payload["source_version"],
                f"replay-{replay_id}",
            ),
        )
        cursor.execute(
            """
            UPDATE quarantine_records
            SET status = 'resolved', replay_id = ?, updated_at = datetime('now')
            WHERE quarantine_id = ? AND status = 'quarantined'
            """,
            (replay_id, quarantine_id),
        )
        cursor.execute(
            """
            INSERT INTO ingestion_replay_logs
                (replay_id, quarantine_id, batch_id, replayed_by, fixed_payload,
                 new_spec_id, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'success', ?)
            """,
            (
                replay_id,
                quarantine_id,
                source["batch_id"],
                replayed_by,
                json.dumps(fixed_payload, ensure_ascii=False),
                fixed_payload["spec_id"],
                notes,
            ),
        )
        conn.commit()
        return True, "Replay executed successfully"
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        return False, str(exc)
    finally:
        conn.close()
def get_three_way_reconciliation(batch_id: str) -> Dict[str, Any]:
    """三处一致对账: 批次汇总计数 vs 主表实际行数 vs 隔离区实际行数。

    一致性公式 (隔离表计数与状态无关，resolved 行仍计入，因此重放后依旧成立):
      total_rows == accepted_count + duplicate_count + quarantined_count
      accepted_count                      == COUNT(specs WHERE batch_id = ?)
      quarantined_count + duplicate_count == COUNT(quarantine_records WHERE batch_id = ?)
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        batch_info = cursor.execute(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if not batch_info:
            return {"error": f"Batch {batch_id} not found"}

        batch = dict(batch_info)
        accepted_in_db = cursor.execute(
            "SELECT COUNT(*) AS n FROM specs WHERE batch_id = ?", (batch_id,)
        ).fetchone()["n"]
        quarantine_rows_in_db = cursor.execute(
            "SELECT COUNT(*) AS n FROM quarantine_records WHERE batch_id = ?", (batch_id,)
        ).fetchone()["n"]
        resolved_in_db = cursor.execute(
            "SELECT COUNT(*) AS n FROM quarantine_records "
            "WHERE batch_id = ? AND status = 'resolved'",
            (batch_id,),
        ).fetchone()["n"]

        total = batch["total_rows"]
        recorded_accepted = batch["accepted_count"]
        recorded_duplicate = batch["duplicate_count"]
        recorded_quarantined = batch["quarantined_count"]

        is_consistent = (
            total == recorded_accepted + recorded_duplicate + recorded_quarantined
            and recorded_accepted == accepted_in_db
            and (recorded_quarantined + recorded_duplicate) == quarantine_rows_in_db
        )

        return {
            "batch_id": batch_id,
            "total_input_rows": total,
            "recorded_accepted": recorded_accepted,
            "recorded_duplicate": recorded_duplicate,
            "recorded_quarantined": recorded_quarantined,
            "accepted_in_specs_table": accepted_in_db,
            "quarantined_in_quarantine_table": quarantine_rows_in_db,
            "resolved_quarantine_records": resolved_in_db,
            "three_way_consistent": is_consistent,
        }
    finally:
        conn.close()
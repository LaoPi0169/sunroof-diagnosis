"""
Ingestion Service Layer (阶段 A 节点 03)
数据合同校验、批次幂等路由、隔离区分流与定向重放的核心业务逻辑。

处理顺序 (回链 specs/data-contract-v1.md):
  1. validate_row 数据合同校验 -> 失败即隔离 (携带原因码，绝不静默丢弃)
  2. 稳定业务键 (spec_id:source_version) 查重 -> 重复不产生第二次业务效果
  3. accepted 行写入 specs 主表 (完整血缘: source_id / source_version / batch_id / owner_id)
  4. 批次单事务原子提交 + 三处一致对账 (get_three_way_reconciliation)

安全边界: 普通结构化日志只含关联 ID、行引用、计数与原因码;
零件号、规格 ID/标题、来源引用、项目组、原始行内容一律不落日志 (跨主机厂敏感)。
"""
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from application.database import get_db_connection
from ingestion.repository import (
    create_batch,
    get_batch,
    get_quarantine_record,
    insert_accepted_spec,
    insert_quarantine_record,
    record_failed_replay,
    resolve_quarantine_and_replay,
    spec_id_taken,
    spec_key_exists,
    update_batch_summary,
)
from ingestion.schema import (
    BatchIngestionRequest,
    BatchIngestionResult,
    BatchStatus,
    QuarantineReasonCode,
    QuarantineStatus,
    ReplayRequest,
    ReplayResult,
)

logger = logging.getLogger("sunroof.ingestion")

# 数据合同 v1 的封闭枚举与计量约定 (与 001 迁移 CHECK 约束一致)
VALID_PROCESS_STEPS = {"torque_tightening", "gap_check"}
VALID_OEM_PROJECTS = {"OEM-A", "OEM-B"}
UNIT_BY_PROCESS = {
    "torque_tightening": "N·m",
    "gap_check": "mm",
}

# 核心标识字段: 任一缺失即 MISSING_BUSINESS_KEY (业务键无法构造或无法追溯来源)
REQUIRED_BUSINESS_FIELDS = (
    "spec_id",
    "title",
    "version",
    "part_no",
    "unit",
    "formula_rule",
    "source_ref",
    "source_id",
    "source_version",
)


def _is_iso_date(value: Any) -> bool:
    """数据合同要求日期为 ISO-8601 (YYYY-MM-DD)，保证字符串比较与日期语义一致。"""
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _normalize_allowed_projects(value: Any) -> Optional[List[str]]:
    """行级 ACL 声明归一化: 接受 JSON 数组字符串或列表; 无法解析返回 None。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return None
def validate_row(
    row: Dict[str, Any],
) -> Tuple[bool, Optional[QuarantineReasonCode], Dict[str, Any]]:
    """按数据合同 v1 校验单行。返回 (是否合法, 原因码, 归一化行)。

    校验顺序即诊断优先级: 业务键 -> 所有者 -> 工序 -> 项目组 -> ACL 声明 (默认隔离)
    -> 数值 -> 单位 -> 时间边界。归一化行对数值/is_current/ACL 声明做强类型化。
    """
    # 1. 业务键与核心标识字段
    for field in REQUIRED_BUSINESS_FIELDS:
        if not row.get(field):
            return False, QuarantineReasonCode.MISSING_BUSINESS_KEY, row

    # 2. 知识所有者: 缺失即隔离 (MISSING_OWNER)
    if not row.get("owner_id"):
        return False, QuarantineReasonCode.MISSING_OWNER, row

    # 3. 工序封闭枚举
    process_step = row.get("process_step")
    if process_step not in VALID_PROCESS_STEPS:
        return False, QuarantineReasonCode.INVALID_PROCESS_STEP, row

    # 4. 项目组封闭枚举
    oem_project = row.get("oem_project")
    if oem_project not in VALID_OEM_PROJECTS:
        return False, QuarantineReasonCode.INVALID_OEM_PROJECT, row

    # 5. 行级 ACL 声明: 必须与 oem_project 完全一致 (单元素)。
    #    缺失/空/越权/多元素一律默认隔离，绝不自动收窄或扩大可见范围
    #    (Default Deny，保护指标 P-1 在数据入口处的第一道防线)。
    allowed = _normalize_allowed_projects(row.get("allowed_oem_projects"))
    if (
        not allowed
        or len(allowed) != 1
        or allowed[0] != oem_project
        or allowed[0] not in VALID_OEM_PROJECTS
    ):
        return False, QuarantineReasonCode.ACL_SCOPE_MISMATCH, row

    # 6. 数值范围: 名义值 > 0、公差 >= 0 (与主表 CHECK 约束一致)
    try:
        nominal_value = float(row.get("nominal_value"))
        tol_plus = float(row.get("tol_plus"))
        tol_minus = float(row.get("tol_minus"))
    except (TypeError, ValueError):
        return False, QuarantineReasonCode.INVALID_VALUE_RANGE, row
    if nominal_value <= 0 or tol_plus < 0 or tol_minus < 0:
        return False, QuarantineReasonCode.INVALID_VALUE_RANGE, row

    # 6b. is_current: 缺失默认 0 (绝不自动赋予现行效力); 非法值隔离
    try:
        is_current = int(row.get("is_current", 0))
    except (TypeError, ValueError):
        return False, QuarantineReasonCode.INVALID_VALUE_RANGE, row
    if is_current not in (0, 1):
        return False, QuarantineReasonCode.INVALID_VALUE_RANGE, row

    # 7. 计量单位与工序约定一致 (扭矩 N·m / 间隙 mm，错配会污染偏差计算)
    if str(row.get("unit")) != UNIT_BY_PROCESS[process_step]:
        return False, QuarantineReasonCode.UNIT_PROCESS_MISMATCH, row

    # 8. 时间边界: 必须是 ISO 日期，且严格 expires_at > effective_at (与主表 CHECK 一致)
    effective_at = row.get("effective_at")
    if not effective_at or not _is_iso_date(effective_at):
        return False, QuarantineReasonCode.INVALID_SCHEMA, row
    expires_at = row.get("expires_at")
    if expires_at is not None:
        if not _is_iso_date(expires_at):
            return False, QuarantineReasonCode.INVALID_SCHEMA, row
        if str(expires_at) <= str(effective_at):
            return False, QuarantineReasonCode.INVALID_EFFECTIVE_RANGE, row

    normalized = dict(row)
    normalized["nominal_value"] = nominal_value
    normalized["tol_plus"] = tol_plus
    normalized["tol_minus"] = tol_minus
    normalized["is_current"] = is_current
    normalized["allowed_oem_projects"] = json.dumps(allowed, ensure_ascii=False)
    return True, None, normalized
def _record_failed_batch(batch_id: str, source_id: str, source_version: str) -> None:
    """基础设施错误后尽力落一条 failed 批次标记 (整批已回滚, total_rows=0 表示零持久化效果)。"""
    try:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO ingestion_batches "
                "(batch_id, source_id, source_version, total_rows, status) "
                "VALUES (?, ?, ?, 0, 'failed')",
                (batch_id, source_id, source_version),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # 审计标记失败不掩盖原始错误


def process_batch_ingestion(request: BatchIngestionRequest) -> BatchIngestionResult:
    """批次摄取入口 (幂等):
    - 同一 batch_id 已达终态 (completed / completed_with_quarantine / failed)
      -> 直接返回既有对账结果 (is_idempotent_replay=True)，零写副作用;
         failed 是已消费的幂等键，重试必须换新 batch_id;
    - 首次处理 -> 整批单事务原子提交: 无效行进隔离区、重复行只计数落隔离、
      有效行写入 specs 主表; 进程崩溃不会留下部分提交的批次。
    """
    existing_batch = get_batch(request.batch_id)
    if existing_batch and existing_batch["status"] in (
        BatchStatus.COMPLETED.value,
        BatchStatus.COMPLETED_WITH_QUARANTINE.value,
        BatchStatus.FAILED.value,
    ):
        logger.info(
            "ingestion_batch_already_processed batch_id=%s correlation_id=%s input_rows=%d",
            request.batch_id,
            request.correlation_id or "unknown",
            existing_batch["total_rows"],
        )
        return BatchIngestionResult(
            batch_id=existing_batch["batch_id"],
            source_id=existing_batch["source_id"],
            correlation_id=request.correlation_id,
            total_rows=existing_batch["total_rows"],
            accepted_count=existing_batch["accepted_count"],
            duplicate_count=existing_batch["duplicate_count"],
            quarantined_count=existing_batch["quarantined_count"],
            status=BatchStatus(existing_batch["status"]),
            is_idempotent_replay=True,
            quarantine_summary={},
        )

    correlation_id = request.correlation_id or f"ing-{uuid.uuid4().hex[:8]}"
    logger.info(
        "ingestion_batch_started batch_id=%s correlation_id=%s source_id=%s input_rows=%d",
        request.batch_id,
        correlation_id,
        request.source_id,
        len(request.rows),
    )

    accepted = 0
    duplicate = 0
    quarantined = 0
    quarantine_reasons: Dict[str, int] = {}
    seen_in_batch: set = set()
    duplicate_reason = QuarantineReasonCode.DUPLICATE_BUSINESS_KEY.value

    conn = get_db_connection()
    try:
        create_batch(
            conn, request.batch_id, request.source_id,
            request.source_version, len(request.rows),
        )
        for index, row in enumerate(request.rows, start=1):
            safe_source_ref = f"{request.source_id}#R{index}"

            # 1. 数据合同校验 -> 失败进隔离区 (携带原因码，绝不静默丢弃)
            is_valid, reason, validated_row = validate_row(row)
            if not is_valid:
                quarantined += 1
                reason_code = (
                    reason.value if reason else QuarantineReasonCode.INVALID_SCHEMA.value
                )
                quarantine_reasons[reason_code] = quarantine_reasons.get(reason_code, 0) + 1
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, reason_code
                )
                logger.info(
                    "ingestion_row_quarantined batch_id=%s correlation_id=%s "
                    "row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, reason_code,
                )
                continue

            # 2. 稳定业务键 (spec_id:source_version) 去重 (批内 + 主表):
            #    重复行只计数并落隔离表用于对账，绝不产生第二次业务效果
            business_key = f"{validated_row['spec_id']}:{validated_row['source_version']}"
            if business_key in seen_in_batch or spec_key_exists(
                conn, validated_row["spec_id"], validated_row["source_version"]
            ):
                duplicate += 1
                quarantine_reasons[duplicate_reason] = (
                    quarantine_reasons.get(duplicate_reason, 0) + 1
                )
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, duplicate_reason
                )
                logger.info(
                    "ingestion_row_duplicate batch_id=%s correlation_id=%s "
                    "row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, duplicate_reason,
                )
                continue

            # 2b. spec_id 已被其他源版本占用 -> 上游修订必须换发新 spec_id (版本化 ID 惯例)
            if spec_id_taken(conn, validated_row["spec_id"], validated_row["source_version"]):
                duplicate += 1
                quarantine_reasons[duplicate_reason] = (
                    quarantine_reasons.get(duplicate_reason, 0) + 1
                )
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, duplicate_reason
                )
                logger.info(
                    "ingestion_row_duplicate batch_id=%s correlation_id=%s "
                    "row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, duplicate_reason,
                )
                continue

            # 3. accepted: 写入主表 (完整血缘); 主键约束兜底 (防御性，正常路径不可达)
            try:
                insert_accepted_spec(conn, validated_row, request.batch_id)
            except sqlite3.IntegrityError:
                duplicate += 1
                quarantine_reasons[duplicate_reason] = (
                    quarantine_reasons.get(duplicate_reason, 0) + 1
                )
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, duplicate_reason
                )
                logger.info(
                    "ingestion_row_duplicate batch_id=%s correlation_id=%s "
                    "row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, duplicate_reason,
                )
                continue
            accepted += 1
            seen_in_batch.add(business_key)
            logger.info(
                "ingestion_row_accepted batch_id=%s correlation_id=%s row_ref=%s",
                request.batch_id, correlation_id, safe_source_ref,
            )

        batch_status = (
            BatchStatus.COMPLETED if quarantined == 0
            else BatchStatus.COMPLETED_WITH_QUARANTINE
        )
        update_batch_summary(
            conn, request.batch_id, accepted, duplicate, quarantined, batch_status.value
        )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        _record_failed_batch(request.batch_id, request.source_id, request.source_version)
        logger.error(
            "ingestion_batch_failed batch_id=%s correlation_id=%s error_type=%s",
            request.batch_id, correlation_id, type(exc).__name__,
        )
        return BatchIngestionResult(
            batch_id=request.batch_id,
            source_id=request.source_id,
            correlation_id=correlation_id,
            total_rows=0,
            accepted_count=0,
            duplicate_count=0,
            quarantined_count=0,
            status=BatchStatus.FAILED,
            is_idempotent_replay=False,
            quarantine_summary={},
        )
    finally:
        conn.close()

    logger.info(
        "ingestion_batch_completed batch_id=%s correlation_id=%s "
        "accepted_rows=%d duplicate_rows=%d quarantined_rows=%d",
        request.batch_id, correlation_id, accepted, duplicate, quarantined,
    )
    return BatchIngestionResult(
        batch_id=request.batch_id,
        source_id=request.source_id,
        correlation_id=correlation_id,
        total_rows=len(request.rows),
        accepted_count=accepted,
        duplicate_count=duplicate,
        quarantined_count=quarantined,
        status=batch_status,
        is_idempotent_replay=False,
        quarantine_summary=quarantine_reasons,
    )
def execute_targeted_replay(request: ReplayRequest) -> ReplayResult:
    """定向重放: 修复隔离记录 -> 原子事务 (主表 + resolved + 重放审计)，历史绝不物理删除。

    fixed_payload 支持部分补丁: 与隔离区 raw_payload 合并后重新过数据合同校验。
    禁止直接手工 SQL 修改 specs 表 (回链 ingestion-runbook-v0.md)。
    """
    quarantine_record = get_quarantine_record(request.quarantine_id)
    if not quarantine_record:
        return ReplayResult(
            replay_id=f"rep-err-{uuid.uuid4().hex[:6]}",
            quarantine_id=request.quarantine_id,
            status="failed",
            message=f"Quarantine record {request.quarantine_id} not found",
        )

    if quarantine_record["status"] != QuarantineStatus.QUARANTINED.value:
        return ReplayResult(
            replay_id=quarantine_record.get("replay_id")
            or f"rep-err-{uuid.uuid4().hex[:6]}",
            quarantine_id=request.quarantine_id,
            status="failed",
            message=(
                f"Quarantine record is not replayable (status={quarantine_record['status']})"
            ),
        )

    merged_payload = {**quarantine_record["raw_payload"], **request.fixed_payload}
    is_valid, reason, fixed_row = validate_row(merged_payload)
    replay_id = f"rep-{uuid.uuid4().hex[:10]}"

    if not is_valid:
        fail_reason = reason.value if reason else QuarantineReasonCode.INVALID_SCHEMA.value
        record_failed_replay(
            replay_id=replay_id,
            quarantine_id=request.quarantine_id,
            batch_id=quarantine_record["batch_id"],
            replayed_by=request.replayed_by,
            fixed_payload=merged_payload,
            fail_reason=f"Fixed payload failed Data Contract validation: {fail_reason}",
        )
        logger.info(
            "ingestion_replay_failed replay_id=%s quarantine_id=%s reason_code=%s",
            replay_id, request.quarantine_id, fail_reason,
        )
        return ReplayResult(
            replay_id=replay_id,
            quarantine_id=request.quarantine_id,
            status="failed",
            message=f"Fixed payload failed Data Contract validation: {fail_reason}",
        )

    success, message = resolve_quarantine_and_replay(
        quarantine_id=request.quarantine_id,
        replay_id=replay_id,
        fixed_payload=fixed_row,
        replayed_by=request.replayed_by,
        notes=request.notes,
    )

    if success:
        logger.info(
            "ingestion_replay_completed replay_id=%s quarantine_id=%s result=success",
            replay_id, request.quarantine_id,
        )
        return ReplayResult(
            replay_id=replay_id,
            quarantine_id=request.quarantine_id,
            status="success",
            accepted_spec_id=fixed_row["spec_id"],
            message="Quarantined record fixed and successfully replayed into specs table",
        )

    logger.info(
        "ingestion_replay_failed replay_id=%s quarantine_id=%s reason_code=TRANSACTION_ERROR",
        replay_id, request.quarantine_id,
    )
    return ReplayResult(
        replay_id=replay_id,
        quarantine_id=request.quarantine_id,
        status="failed",
        message=f"Replay transaction error: {message}",
    )
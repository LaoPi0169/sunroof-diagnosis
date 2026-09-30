"""
Ingestion Service Layer (Week 06)
数据合同校验、批次幂等路由、隔离区分流与定向重放的核心业务逻辑。

处理顺序 (回链 specs/mission-week-06.md 架构图):
  1. validate_row 数据合同校验 -> 失败即隔离 (携带原因码，绝不静默丢弃)
  2. 稳定业务键 (policy_id + source_version) 查重 -> 重复不产生第二次业务效果
  3. accepted 行写入主表 (完整血缘: source_id / source_version / batch_id / owner_id)
  4. 批次单事务原子提交 + 三处一致对账 (get_three_way_reconciliation)

安全边界: 普通结构化日志只含关联 ID、行引用、计数与原因码;
政策正文、公式规则、所有者 ID、原始行内容一律不落日志。
"""
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from application.database import get_db_connection
from ingestion.repository import (
    create_batch,
    get_batch,
    get_quarantine_record,
    insert_accepted_policy,
    insert_quarantine_record,
    policy_exists,
    record_failed_replay,
    resolve_quarantine_and_replay,
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

logger = logging.getLogger("fde.ingestion")

VALID_REGIONS = {"华东", "华南", "华北", "全国"}
VALID_ACCESS_LEVELS = {"support_agent", "supervisor"}

# 核心标识字段: 任一缺失即 MISSING_BUSINESS_KEY (业务键无法构造或无法追溯来源)
REQUIRED_BUSINESS_FIELDS = (
    "policy_id",
    "title",
    "version",
    "source_id",
    "source_version",
    "formula_rule",
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


def validate_row(
    row: Dict[str, Any],
) -> Tuple[bool, Optional[QuarantineReasonCode], Dict[str, Any]]:
    """按数据合同 v1 校验单行。返回 (是否合法, 原因码, 行)。

    校验顺序即诊断优先级: 先业务键 -> 所有者 -> 权限 (默认拒绝) -> 地区 -> 时间边界。
    """
    # 1. 业务键与核心标识字段
    for field in REQUIRED_BUSINESS_FIELDS:
        if not row.get(field):
            return False, QuarantineReasonCode.MISSING_BUSINESS_KEY, row

    # 2. 知识所有者: 缺失即隔离 (MISSING_OWNER)
    if not row.get("owner_id"):
        return False, QuarantineReasonCode.MISSING_OWNER, row

    # 3. 访问级别: 缺失或未知一律默认隔离 (Default Deny，绝不自动扩大可见范围)
    access_level = row.get("access_level")
    if not access_level or access_level not in VALID_ACCESS_LEVELS:
        return False, QuarantineReasonCode.UNKNOWN_ACCESS_LEVEL, row

    # 4. 地区枚举
    if row.get("region") not in VALID_REGIONS:
        return False, QuarantineReasonCode.INVALID_REGION, row

    # 5. 时间边界: 必须是 ISO 日期，且严格 expires_at > effective_at (与主表 CHECK 一致)
    effective_at = row.get("effective_at")
    expires_at = row.get("expires_at")
    if not effective_at or not _is_iso_date(effective_at):
        return False, QuarantineReasonCode.INVALID_SCHEMA, row
    if expires_at is not None:
        if not _is_iso_date(expires_at):
            return False, QuarantineReasonCode.INVALID_SCHEMA, row
        if str(expires_at) <= str(effective_at):
            return False, QuarantineReasonCode.INVALID_EFFECTIVE_RANGE, row

    return True, None, row


def process_batch_ingestion(request: BatchIngestionRequest) -> BatchIngestionResult:
    """批次摄取入口 (幂等):
    - 同一 batch_id 已达终态 -> 直接返回既有对账结果 (is_idempotent_replay=True)，零写副作用;
    - 首次处理 -> 整批单事务原子提交: 无效行进隔离区、重复行只计数、有效行写入主表。
    """
    existing_batch = get_batch(request.batch_id)
    if existing_batch and existing_batch["status"] in (
        BatchStatus.COMPLETED.value,
        BatchStatus.COMPLETED_WITH_QUARANTINE.value,
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
                    "ingestion_row_quarantined batch_id=%s correlation_id=%s row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, reason_code,
                )
                continue

            # 2. 稳定业务键去重 (批内 + 主表): 重复不产生第二次业务效果
            business_key = (
                f"{validated_row['policy_id']}:{validated_row['source_version']}"
            )
            if business_key in seen_in_batch or policy_exists(
                conn, validated_row["policy_id"], validated_row["source_version"]
            ):
                duplicate += 1
                quarantine_reasons[duplicate_reason] = (
                    quarantine_reasons.get(duplicate_reason, 0) + 1
                )
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, duplicate_reason
                )
                logger.info(
                    "ingestion_row_duplicate batch_id=%s correlation_id=%s row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, duplicate_reason,
                )
                continue

            # 3. accepted: 写入主表 (完整血缘); 主键约束兜底 (防御性，正常路径不可达)
            try:
                insert_accepted_policy(conn, validated_row, request.batch_id)
            except sqlite3.IntegrityError:
                duplicate += 1
                quarantine_reasons[duplicate_reason] = (
                    quarantine_reasons.get(duplicate_reason, 0) + 1
                )
                insert_quarantine_record(
                    conn, request.batch_id, safe_source_ref, row, duplicate_reason
                )
                logger.info(
                    "ingestion_row_duplicate batch_id=%s correlation_id=%s row_ref=%s reason_code=%s",
                    request.batch_id, correlation_id, safe_source_ref, duplicate_reason,
                )
                continue

            seen_in_batch.add(business_key)
            accepted += 1

        # 4. 批次终态 + 单事务原子提交 (无部分提交)
        batch_status = (
            BatchStatus.COMPLETED_WITH_QUARANTINE
            if quarantined > 0
            else BatchStatus.COMPLETED
        )
        update_batch_summary(
            conn, request.batch_id, accepted, duplicate, quarantined, batch_status.value
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "ingestion_batch_completed batch_id=%s correlation_id=%s source_id=%s "
        "input_rows=%d accepted_rows=%d duplicate_rows=%d quarantined_rows=%d",
        request.batch_id,
        correlation_id,
        request.source_id,
        len(request.rows),
        accepted,
        duplicate,
        quarantined,
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
            replay_id=quarantine_record.get("replay_id") or f"rep-err-{uuid.uuid4().hex[:6]}",
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
            accepted_policy_id=fixed_row["policy_id"],
            message="Quarantined record fixed and successfully replayed into policies table",
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


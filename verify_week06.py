# -*- coding: utf-8 -*-
"""Week 06 人工验收演示脚本：端到端走一遍摄取管道（使用临时数据库）。

用法:
    python verify_week06.py

- 全程使用 tempfile 临时 SQLite 库，绝不触碰 data/northstar.db
- 覆盖: 批次摄取 -> 幂等重跑 -> 隔离区排查 -> 定向重放 -> 三处一致对账 -> 血缘终态 + 日志安全
- 任一断言失败即抛出异常（退出码非 0），全部通过最后打印 VERIFICATION-OK
"""
import json
import logging
import os
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

tmpdir = tempfile.mkdtemp(prefix="fde-week06-verify-")
DB_PATH = os.path.join(tmpdir, "verify.db")
os.environ["FDE_DB_PATH"] = DB_PATH

from application import database  # noqa: E402

database.init_db(DB_PATH)

from ingestion import service as ingestion_service  # noqa: E402
from ingestion.repository import get_three_way_reconciliation  # noqa: E402
from ingestion.schema import BatchIngestionRequest, ReplayRequest  # noqa: E402

BATCH_ID = "batch-w06-verify"
SOURCE_ID = "northstar-policy-export-v1"

# 与 tests/test_ingestion_reliability.py 完全一致的 6 行参考夹具
R1 = {
    "policy_id": "POL-PLAN-01", "title": "2026 套餐计费政策 v3", "version": "v3",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-01-01", "expires_at": None,
    "formula_rule": "refund = (new_price - old_price) * remaining_months",
    "source_id": "QMS-SOP-SR-01", "source_version": "v3", "owner_id": "knowledge-billing",
}
R2 = dict(R1)  # 与 R1 业务键相同 -> duplicate
R3 = {
    "policy_id": "POL-REFUND-02", "title": "2026 退款处理政策", "version": "v1",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-02-01", "expires_at": None,
    "formula_rule": "refund_rule_v1", "source_id": "QMS-SOP-SR-05",
    "source_version": "1", "owner_id": None,  # 缺所有者 -> MISSING_OWNER
}
R4 = {
    "policy_id": "POL-CREDIT-03", "title": "2026 华北积分补偿政策", "version": "v1",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-10-01", "expires_at": "2026-09-01",  # 失效早于生效 -> INVALID_EFFECTIVE_RANGE
    "formula_rule": "credit_rule_v1", "source_id": "QMS-SOP-SR-06",
    "source_version": "1", "owner_id": "knowledge-credit",
}
R5 = {  # 与 Week 05 种子 POL-PLAN-01 v2 不同 source_version, 合法历史版本 -> accepted
    "policy_id": "POL-PLAN-01", "title": "2025 套餐计费政策 v2（已过期）", "version": "v2",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2025-01-01", "expires_at": "2025-12-31",
    "formula_rule": "legacy_rule_v2", "source_id": "QMS-SOP-SR-01",
    "source_version": "v2", "owner_id": "knowledge-billing",
}
R6 = {  # supervisor 数据 -> accepted 且不降级
    "policy_id": "POL-SPECIAL-09", "title": "2026 特殊升级政策（主管专用）", "version": "v4",
    "region": "华北", "access_level": "supervisor", "is_deprecated": 0,
    "effective_at": "2026-03-01", "expires_at": None,
    "formula_rule": "special_upgrade_rule_v4", "source_id": "QMS-SOP-SR-09",
    "source_version": "v4", "owner_id": "knowledge-special",
}


def hr(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


log_capture = _LogCapture()
ingestion_logger = logging.getLogger("fde.ingestion")
ingestion_logger.setLevel(logging.INFO)  # 与 test_06 相同: 裸脚本需显式放开 INFO 级
ingestion_logger.addHandler(log_capture)

hr("步骤 1/6 · 初始化临时数据库（迁移 001 + 002 幂等执行）")
print("临时库:", DB_PATH)
print("policies 血缘列:", "source_version / owner_id / batch_id 已就绪")
print("新表: ingestion_batches / quarantine_records / ingestion_replay_logs")

hr("步骤 2/6 · 首次摄取 6 行批次（预期 3 accepted / 1 duplicate / 2 quarantined）")
request = BatchIngestionRequest(
    batch_id=BATCH_ID, source_id=SOURCE_ID, source_version="v1.0",
    correlation_id="verify-w06-001",
    rows=[dict(r) for r in (R1, R2, R3, R4, R5, R6)],
)
result = ingestion_service.process_batch_ingestion(request)
print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
assert result.total_rows == 6
assert (result.accepted_count, result.duplicate_count, result.quarantined_count) == (3, 1, 2)
assert result.status == "completed_with_quarantine"

hr("步骤 3/6 · 幂等重跑同一 batch_id（预期零写副作用）")
rerun = ingestion_service.process_batch_ingestion(request)
print("status =", rerun.status.value, "| is_idempotent_replay =", rerun.is_idempotent_replay)
assert rerun.is_idempotent_replay is True
conn = sqlite3.connect(DB_PATH)
audit_rows = conn.execute(
    "SELECT COUNT(*) FROM ingestion_batches WHERE batch_id = ?", (BATCH_ID,)
).fetchone()[0]
batch_policies = conn.execute(
    "SELECT COUNT(*) FROM policies WHERE batch_id = ?", (BATCH_ID,)
).fetchone()[0]
print("批次审计行数 =", audit_rows, "| 主表本批行数 =", batch_policies)
assert audit_rows == 1 and batch_policies == 3

hr("步骤 4/6 · 排查隔离区（原因码 + 安全来源引用）")
conn.row_factory = sqlite3.Row
quarantine_rows = conn.execute(
    "SELECT quarantine_id, reason_code, safe_source_ref, status FROM quarantine_records "
    "WHERE batch_id = ? ORDER BY reason_code", (BATCH_ID,),
).fetchall()
for row in quarantine_rows:
    print(" ", row["quarantine_id"], "|", row["reason_code"], "|", row["safe_source_ref"], "|", row["status"])
assert len(quarantine_rows) == 3
owner_row = next(r for r in quarantine_rows if r["reason_code"] == "MISSING_OWNER")

hr("步骤 5/6 · 补齐 owner_id 后定向重放（部分补丁 + 原子事务）")
replay = ingestion_service.execute_targeted_replay(ReplayRequest(
    quarantine_id=owner_row["quarantine_id"],
    replayed_by="knowledge-admin",
    fixed_payload={"owner_id": "knowledge-refund"},
    notes="verify_week06 演示: 补齐丢失的 owner_id",
))
print(json.dumps(replay.model_dump(mode="json"), ensure_ascii=False, indent=2))
assert replay.status == "success"

hr("步骤 6/6 · 三处一致对账 + 主表血缘终态 + 日志安全检查")
reconciliation = get_three_way_reconciliation(BATCH_ID)
print(json.dumps(reconciliation, ensure_ascii=False, indent=2))
assert reconciliation["three_way_consistent"] is True
assert reconciliation["accepted_in_policies_table"] == 3
print("\n主表血缘终态（含 Week 05 种子行）:")
for row in conn.execute(
    "SELECT policy_id, version, access_level, owner_id, source_id, source_version, batch_id "
    "FROM policies ORDER BY policy_id, version"
):
    print(" ", row["policy_id"], "|", row["version"], "|", row["access_level"], "|",
          row["owner_id"], "|", row["source_id"], "|", row["source_version"], "|", row["batch_id"])
assert any(row["batch_id"] == f"replay-{replay.replay_id}" for row in conn.execute(
    "SELECT batch_id FROM policies WHERE batch_id LIKE 'replay-%'"))
resolved = conn.execute(
    "SELECT status, replay_id FROM quarantine_records WHERE quarantine_id = ?",
    (owner_row["quarantine_id"],),
).fetchone()
print("\n重放后隔离记录:", resolved["status"], "| replay_id =", resolved["replay_id"])
assert resolved["status"] == "resolved" and resolved["replay_id"] == replay.replay_id

SENSITIVE_MARKERS = sorted({R["title"] for R in (R1, R3, R4, R5, R6)}
                           | {R["formula_rule"] for R in (R1, R3, R4, R5, R6)}
                           | {R["owner_id"] for R in (R1, R4, R5, R6) if R["owner_id"]}
                           | {R["policy_id"] for R in (R1, R3, R4, R5, R6)})
leaked = [marker for marker in SENSITIVE_MARKERS
          if any(marker in message for message in log_capture.messages)]
print("\n结构化日志检查: 共捕获", len(log_capture.messages), "条, 敏感标记泄漏:", leaked or "无")
assert not leaked

hr("验收结论")
print("三处一致 = true | 幂等重跑零副作用 | 重放保留历史 | 日志无敏感字段")
print("VERIFICATION-OK")


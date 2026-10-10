"""
阶段 A 节点 03 可靠数据入口 —— 自动化集成测试 (9/9)

测试原则 (回链 specs/data-contract-v1.md 验收标准 与 specs/discovery-brief-v1.md 硬阻断门槛):
1. 每条输入行都有可解释终态: accepted / duplicate / quarantined，无静默丢弃、无假成功;
2. 批次幂等: 相同 batch_id 重跑返回既有对账结果，主表与隔离区零写副作用;
3. 入口 Default-Deny: ACL 声明缺失/越权、is_current 缺失一律隔离，绝不自动扩大可见范围;
4. 定向重放: 修复记录原子入主表，隔离历史 (原 raw_payload + 状态流转) 绝不物理删除;
5. 三处一致: 批次汇总计数 == 主表行数 == 隔离区行数 (input = accepted + duplicate + quarantined);
6. 安全日志: 普通日志只含关联 ID / 行引用 / 计数 / 原因码，绝不包含零件号/规格 ID/标题/来源引用/项目组;
7. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

运行方式 (本项目根目录):
    python -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.database import query_current_specs  # noqa: E402
from application.main import app  # noqa: E402
from ingestion import service as ingestion_service  # noqa: E402
from ingestion.repository import get_three_way_reconciliation  # noqa: E402
from ingestion.schema import (  # noqa: E402
    BatchIngestionRequest,
    BatchStatus,
    ReplayRequest,
)

TOKEN_PQE_A = {"X-Auth-Token": "token_pqe_oem_a"}

BATCH_ID = "batch-node03-20261010-a"
SOURCE_ID = "qms-export-w40-2026"
SOURCE_VERSION = "v1.0"
CORRELATION_ID = "ing-node03-a-001"

# --------------------------------------------------------------------------
# 节点 03 固定合成批次 (6 行，全部为教学模拟数据):
#   B1 accepted  SPEC-GSK-A-V1 OEM-A 间隙新规格 (带完整血缘与 ACL 声明)
#   B2 duplicate 同业务键 SPEC-GSK-A-V1:v1.0 重复出现
#   B3 quarantined SPEC-SR-A-V6 缺 owner_id -> MISSING_OWNER (用例 8 定向重放修复)
#   B4 quarantined SPEC-RNG-A-V1 失效早于生效 -> INVALID_EFFECTIVE_RANGE
#   B5 quarantined SPEC-ACL-A-V1 ACL 声明越权 (多元素) -> ACL_SCOPE_MISMATCH
#   B6 quarantined SPEC-UNIT-A-V1 扭矩工序配 mm -> UNIT_PROCESS_MISMATCH
# --------------------------------------------------------------------------
B1 = {
    "spec_id": "SPEC-GSK-A-V1", "title": "OEM-A 密封条压装间隙检验标准", "version": "v1.0",
    "part_no": "GASKET-STRIP-A9", "process_step": "gap_check", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-07-01", "expires_at": None,
    "nominal_value": 3.0, "tol_plus": 0.3, "tol_minus": 0.3, "unit": "mm",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-GSK-A9-v1#Section-2.1",
    "source_id": "QMS-SOP-SR-GSK-A9", "source_version": "v1.0",
    "owner_id": "owner-qms-gsk", "allowed_oem_projects": ["OEM-A"],
}
B2 = dict(B1)  # 与 B1 完全相同的业务键 SPEC-GSK-A-V1:v1.0
B3 = {
    "spec_id": "SPEC-SR-A-V6", "title": "OEM-A 天窗电机固定螺栓拧紧扭矩检验标准 v6", "version": "v6.0",
    "part_no": "MOTOR-MOUNT-BOLT-03", "process_step": "torque_tightening", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-08-01", "expires_at": None,
    "nominal_value": 9.6, "tol_plus": 0.5, "tol_minus": 0.5, "unit": "N·m",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-04-v6#Section-3.2",
    "source_id": "QMS-SOP-SR-04", "source_version": "v6.0",
    "owner_id": None,  # 缺失 -> MISSING_OWNER (用例 8 定向重放修复)
    "allowed_oem_projects": ["OEM-A"],
}
B4 = {
    "spec_id": "SPEC-RNG-A-V1", "title": "OEM-A 雨刮装饰条间隙检验标准", "version": "v1.0",
    "part_no": "WINDSHIELD-TRIM-11", "process_step": "gap_check", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-07-01", "expires_at": "2026-06-30",  # 倒错
    "nominal_value": 4.0, "tol_plus": 0.4, "tol_minus": 0.4, "unit": "mm",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-TRIM-11-v1#Section-1.1",
    "source_id": "QMS-SOP-SR-TRIM-11", "source_version": "v1.0",
    "owner_id": "owner-qms-trim", "allowed_oem_projects": ["OEM-A"],
}
B5 = {
    "spec_id": "SPEC-ACL-A-V1", "title": "OEM-A 雨刮装饰条平面度检验标准", "version": "v1.0",
    "part_no": "WINDSHIELD-TRIM-11", "process_step": "gap_check", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-07-01", "expires_at": None,
    "nominal_value": 2.0, "tol_plus": 0.2, "tol_minus": 0.2, "unit": "mm",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-TRIM-11-v1#Section-2.2",
    "source_id": "QMS-SOP-SR-TRIM-11", "source_version": "v1.0",
    "owner_id": "owner-qms-trim",
    "allowed_oem_projects": ["OEM-A", "OEM-B"],  # 声明越权 -> ACL_SCOPE_MISMATCH
}
B6 = {
    "spec_id": "SPEC-UNIT-A-V1", "title": "OEM-A 雨刮装饰条螺栓扭矩检验标准", "version": "v1.0",
    "part_no": "WINDSHIELD-TRIM-11", "process_step": "torque_tightening", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-07-01", "expires_at": None,
    "nominal_value": 8.0, "tol_plus": 0.5, "tol_minus": 0.5,
    "unit": "mm",  # 扭矩工序应为 N·m -> UNIT_PROCESS_MISMATCH
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-TRIM-11-v1#Section-3.3",
    "source_id": "QMS-SOP-SR-TRIM-11", "source_version": "v1.0",
    "owner_id": "owner-qms-trim", "allowed_oem_projects": ["OEM-A"],
}
NODE03_ROWS = [B1, B2, B3, B4, B5, B6]

# 节点 02 种子业务键重推样本 (用例 6: 血缘回填 + 业务键守护)
SEED_V5 = {
    "spec_id": "SPEC-SR-A-V5", "title": "天窗电机固定螺栓拧紧扭矩检验标准", "version": "v5.0",
    "part_no": "MOTOR-MOUNT-BOLT-03", "process_step": "torque_tightening", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-06-01", "expires_at": None,
    "nominal_value": 9.5, "tol_plus": 0.5, "tol_minus": 0.5, "unit": "N·m",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-04-v5#Section-3.2",
    "source_id": "QMS-SOP-SR-04", "source_version": "v5.0",
    "owner_id": "owner-qms-sr", "allowed_oem_projects": ["OEM-A"],
}
SEED_V5_REVISED = {
    **SEED_V5,
    "source_version": "v5.9",  # 同 spec_id 不同源版本 -> 必须换发新 spec_id
    "source_ref": "QMS-SOP-SR-04-v5#Section-3.9",
}


def _node03_request(batch_id: str = BATCH_ID) -> BatchIngestionRequest:
    return BatchIngestionRequest(
        batch_id=batch_id,
        source_id=SOURCE_ID,
        source_version=SOURCE_VERSION,
        correlation_id=CORRELATION_ID,
        rows=[dict(row) for row in NODE03_ROWS],
    )


class IngestionReliabilityTests(unittest.TestCase):
    """集成测试链路: 批次请求 -> 数据合同校验 -> 主表/隔离区分流 -> 三处一致对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-node03-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 幂等执行 001 + 002 迁移与血缘回填
        self.client = TestClient(app)

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)

    # ---------------- 通用断言辅助 ----------------
    def _fetch_all(self, sql: str, params: tuple = ()):
        conn = database.get_db_connection()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _count(self, sql: str, params: tuple = ()) -> int:
        conn = database.get_db_connection()
        try:
            return int(conn.execute(sql, params).fetchone()["n"])
        finally:
            conn.close()

    def _ingest_node03(self):
        return ingestion_service.process_batch_ingestion(_node03_request())

    def _quarantine_rows(self, batch_id: str, reason_code: str = None):
        if reason_code:
            return self._fetch_all(
                "SELECT * FROM quarantine_records WHERE batch_id = ? AND reason_code = ?",
                (batch_id, reason_code),
            )
        return self._fetch_all(
            "SELECT * FROM quarantine_records WHERE batch_id = ?", (batch_id,)
        )
    # ------------------------------------------------------------------
    # 用例 1: 首次摄取分流正确 + 血缘完整 + 三处一致
    # 6 行输入 = 1 accepted + 1 duplicate + 4 quarantined (四种原因码)
    # ------------------------------------------------------------------
    def test_01_first_ingestion_routes_rows_and_reconciles(self):
        result = self._ingest_node03()

        # --- 批次结果 (批次侧对账口径) ---
        self.assertEqual(result.total_rows, 6)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.quarantined_count, 4)
        self.assertEqual(result.status, BatchStatus.COMPLETED_WITH_QUARANTINE)
        self.assertFalse(result.is_idempotent_replay)
        self.assertEqual(result.correlation_id, CORRELATION_ID)
        self.assertEqual(
            result.quarantine_summary,
            {
                "DUPLICATE_BUSINESS_KEY": 1,
                "MISSING_OWNER": 1,
                "INVALID_EFFECTIVE_RANGE": 1,
                "ACL_SCOPE_MISMATCH": 1,
                "UNIT_PROCESS_MISMATCH": 1,
            },
        )

        # --- 主表血缘可追溯: source_id / source_version / batch_id / owner_id ---
        accepted = self._fetch_all(
            "SELECT spec_id, source_id, source_version, batch_id, owner_id, "
            "is_current, allowed_oem_projects FROM specs WHERE batch_id = ?",
            (BATCH_ID,),
        )
        self.assertEqual(len(accepted), 1)
        row = accepted[0]
        self.assertEqual(row["spec_id"], "SPEC-GSK-A-V1")
        self.assertEqual(row["source_id"], "QMS-SOP-SR-GSK-A9")
        self.assertEqual(row["source_version"], "v1.0")
        self.assertEqual(row["batch_id"], BATCH_ID)
        self.assertEqual(row["owner_id"], "owner-qms-gsk")
        self.assertEqual(row["is_current"], 1)
        self.assertEqual(json.loads(row["allowed_oem_projects"]), ["OEM-A"])

        # --- 隔离区: 重复行也落隔离表用于对账 (1 dup + 4 invalid = 5 行) ---
        quarantined = self._fetch_all(
            "SELECT quarantine_id, safe_source_ref, reason_code, raw_payload, status "
            "FROM quarantine_records WHERE batch_id = ? ORDER BY safe_source_ref",
            (BATCH_ID,),
        )
        self.assertEqual(len(quarantined), 5)
        self.assertEqual(
            sorted(q["reason_code"] for q in quarantined),
            [
                "ACL_SCOPE_MISMATCH",
                "DUPLICATE_BUSINESS_KEY",
                "INVALID_EFFECTIVE_RANGE",
                "MISSING_OWNER",
                "UNIT_PROCESS_MISMATCH",
            ],
        )
        self.assertTrue(
            all(q["safe_source_ref"].startswith(f"{SOURCE_ID}#R") for q in quarantined)
        )
        self.assertTrue(all(q["status"] == "quarantined" for q in quarantined))
        owner_row = [q for q in quarantined if q["reason_code"] == "MISSING_OWNER"][0]
        self.assertEqual(json.loads(owner_row["raw_payload"]), B3)

        # --- 三处一致对账 ---
        recon = get_three_way_reconciliation(BATCH_ID)
        self.assertTrue(recon["three_way_consistent"])

    # ------------------------------------------------------------------
    # 用例 2: 批次幂等 —— 相同 batch_id 重跑返回既有对账结果，零写副作用
    # ------------------------------------------------------------------
    def test_02_batch_id_rerun_is_idempotent_with_zero_side_effects(self):
        first = self._ingest_node03()
        batch_before = self._fetch_all(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (BATCH_ID,)
        )[0]

        rerun = self._ingest_node03()
        self.assertTrue(rerun.is_idempotent_replay)
        self.assertEqual(rerun.status, first.status)
        self.assertEqual(rerun.accepted_count, 1)
        self.assertEqual(rerun.duplicate_count, 1)
        self.assertEqual(rerun.quarantined_count, 4)
        self.assertEqual(rerun.quarantine_summary, {})  # 既有对账结果，无新隔离

        # 零写副作用: 主表 / 隔离区 / 批次行 (含 created_at 与计数) 完全不变
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM specs WHERE batch_id = ?", (BATCH_ID,)
            ),
            1,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM quarantine_records WHERE batch_id = ?",
                (BATCH_ID,),
            ),
            5,
        )
        batch_after = self._fetch_all(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (BATCH_ID,)
        )[0]
        self.assertEqual(batch_after, batch_before)
    # ------------------------------------------------------------------
    # 用例 3: 入口 Default-Deny —— ACL 声明缺失/越权一律隔离;
    # is_current 缺失默认 0，绝不自动赋予现行效力 (P-1 第一道防线)
    # ------------------------------------------------------------------
    def test_03_default_deny_acl_scope_and_is_current(self):
        base = dict(B1)
        missing = {
            **base,
            "spec_id": "SPEC-ACL-A-M1",
            "title": "OEM-A 密封条间隙检验标准（缺 ACL 声明）",
            "allowed_oem_projects": None,
        }
        cross = {
            **base,
            "spec_id": "SPEC-ACL-A-C1",
            "title": "OEM-A 密封条间隙检验标准（声明越权）",
            "allowed_oem_projects": ["OEM-B"],
        }
        ok = {
            **base,
            "spec_id": "SPEC-ACL-A-OK",
            "title": "OEM-A 密封条间隙检验标准（未声明现行）",
        }
        del ok["is_current"]  # 未声明现行 -> 默认 0

        result = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-node03-acl-b",
                source_id=SOURCE_ID,
                source_version=SOURCE_VERSION,
                correlation_id="ing-node03-b-001",
                rows=[missing, cross, ok],
            )
        )
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.quarantined_count, 2)
        self.assertEqual(result.quarantine_summary, {"ACL_SCOPE_MISMATCH": 2})

        # 两条 ACL 违规行绝不落主表 (入口 Default Deny, 绝不自动收窄/扩大可见范围)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM specs "
                "WHERE spec_id IN ('SPEC-ACL-A-M1', 'SPEC-ACL-A-C1')"
            ),
            0,
        )
        landed = self._fetch_all(
            "SELECT is_current FROM specs WHERE spec_id = 'SPEC-ACL-A-OK'"
        )[0]
        self.assertEqual(landed["is_current"], 0)

        # is_current=0 的行不参与现行规格匹配 (查询层二次防线)
        self.assertEqual(
            query_current_specs("GASKET-STRIP-A9", "gap_check", "2026-08-01", ["OEM-A"]),
            [],
        )
    # ------------------------------------------------------------------
    # 用例 4: 定向重放成功 —— 修复记录原子入主表，隔离与重放历史绝不物理删除
    # ------------------------------------------------------------------
    def test_04_targeted_replay_success_preserves_history(self):
        self._ingest_node03()
        q_missing = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]

        replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_missing["quarantine_id"],
                replayed_by="knowledge-admin-qms",
                fixed_payload={"owner_id": "owner-qms-sr"},
                notes="补齐丢失的规格所有者并重新发布",
            )
        )
        self.assertEqual(replay.status, "success")
        self.assertEqual(replay.accepted_spec_id, "SPEC-SR-A-V6")

        # 主表新增业务版本，血缘批次带 replay- 前缀 (可追溯到重放事件)
        landed = self._fetch_all(
            "SELECT spec_id, owner_id, source_version, batch_id FROM specs "
            "WHERE spec_id = 'SPEC-SR-A-V6'"
        )
        self.assertEqual(len(landed), 1)
        self.assertEqual(landed[0]["owner_id"], "owner-qms-sr")
        self.assertEqual(landed[0]["source_version"], "v6.0")
        self.assertEqual(landed[0]["batch_id"], f"replay-{replay.replay_id}")

        # 原隔离记录: quarantined -> resolved + replay_id 回填; 原始内容与创建时间保留
        q_after = self._fetch_all(
            "SELECT * FROM quarantine_records WHERE quarantine_id = ?",
            (q_missing["quarantine_id"],),
        )[0]
        self.assertEqual(q_after["status"], "resolved")
        self.assertEqual(q_after["replay_id"], replay.replay_id)
        self.assertEqual(q_after["created_at"], q_missing["created_at"])
        self.assertEqual(json.loads(q_after["raw_payload"]), B3)

        # 定向: 其他隔离记录不受影响 (绝不物理删除任何历史)
        others = self._fetch_all(
            "SELECT status, replay_id FROM quarantine_records "
            "WHERE batch_id = ? AND quarantine_id != ?",
            (BATCH_ID, q_missing["quarantine_id"]),
        )
        self.assertTrue(
            all(r["status"] == "quarantined" and r["replay_id"] is None for r in others)
        )

        # 重放审计日志完整可追溯
        logs = self._fetch_all(
            "SELECT * FROM ingestion_replay_logs WHERE quarantine_id = ?",
            (q_missing["quarantine_id"],),
        )
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "success")
        self.assertEqual(logs[0]["new_spec_id"], "SPEC-SR-A-V6")
        self.assertEqual(logs[0]["replayed_by"], "knowledge-admin-qms")

        # 重复重放同一条 -> 已 resolved 的记录被系统拒绝 (不产生假成功)
        again = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_missing["quarantine_id"],
                replayed_by="knowledge-admin-qms",
                fixed_payload={},
            )
        )
        self.assertEqual(again.status, "failed")
    # ------------------------------------------------------------------
    # 用例 5: 重放失败路径 —— 补丁仍非法 / 未知记录 -> 无假成功，审计完整
    # ------------------------------------------------------------------
    def test_05_replay_failure_paths_no_fake_success(self):
        self._ingest_node03()
        q_range = self._quarantine_rows(BATCH_ID, "INVALID_EFFECTIVE_RANGE")[0]

        # (a) 补丁仍然非法 (失效仍早于生效) -> 拒绝, 隔离记录原状, 落失败审计
        bad = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_range["quarantine_id"],
                replayed_by="knowledge-admin-qms",
                fixed_payload={"expires_at": "2026-06-30"},
            )
        )
        self.assertEqual(bad.status, "failed")
        self.assertIn("INVALID_EFFECTIVE_RANGE", bad.message)
        still = self._fetch_all(
            "SELECT status FROM quarantine_records WHERE quarantine_id = ?",
            (q_range["quarantine_id"],),
        )[0]
        self.assertEqual(still["status"], "quarantined")
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM specs WHERE spec_id = 'SPEC-RNG-A-V1'"),
            0,
        )

        # (b) 正确补丁 -> 成功入主表
        good = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_range["quarantine_id"],
                replayed_by="knowledge-admin-qms",
                fixed_payload={"expires_at": "2027-06-30"},
                notes="修正日期倒错",
            )
        )
        self.assertEqual(good.status, "success")
        self.assertEqual(good.accepted_spec_id, "SPEC-RNG-A-V1")

        # (c) 未知隔离记录 -> failed (rep-err 前缀, 不产生假成功)
        unknown = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id="quar-not-exist-0000",
                replayed_by="ops",
                fixed_payload={},
            )
        )
        self.assertEqual(unknown.status, "failed")
        self.assertTrue(unknown.replay_id.startswith("rep-err-"))

        # 审计: 失败与成功的尝试都落一行 (修复历史绝不物理删除)
        logs = self._fetch_all(
            "SELECT status FROM ingestion_replay_logs WHERE quarantine_id = ? ORDER BY rowid",
            (q_range["quarantine_id"],),
        )
        self.assertEqual([r["status"] for r in logs], ["failed", "success"])

    # ------------------------------------------------------------------
    # 用例 6: 存量种子血缘回填 + 业务键守护 (种子键重推 / 同 spec_id 换源版本)
    # ------------------------------------------------------------------
    def test_06_baseline_seed_lineage_backfill_and_business_key_guard(self):
        seeds = self._fetch_all(
            "SELECT spec_id, version, source_id, source_version, batch_id "
            "FROM specs ORDER BY spec_id"
        )
        self.assertEqual(len(seeds), 6)
        expected_source_ids = {
            "SPEC-SR-A-V5": "QMS-SOP-SR-04-v5",
            "SPEC-SR-A-V4": "QMS-SOP-SR-04-v4",
            "SPEC-SR-B-V2": "QMS-SOP-SR-B-02-v2",
            "SPEC-UNIQ-B": "QMS-SOP-SR-B-07-v1",
            "SPEC-HS-A-V1": "QMS-SOP-SR-HS-01-v1",
            "SPEC-HS-A-V1B": "QMS-SOP-SR-HS-01-v1B",
        }
        for row in seeds:
            self.assertEqual(row["batch_id"], "node02-baseline-seed")
            self.assertEqual(row["source_version"], row["version"])
            self.assertEqual(row["source_id"], expected_source_ids[row["spec_id"]])

        # 与种子相同业务键重推 -> duplicate; 同 spec_id 不同源版本 -> 同样 duplicate
        result = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-node03-dup-c",
                source_id=SOURCE_ID,
                source_version=SOURCE_VERSION,
                correlation_id="ing-node03-c-001",
                rows=[SEED_V5, SEED_V5_REVISED],
            )
        )
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.duplicate_count, 2)
        self.assertEqual(result.quarantine_summary, {"DUPLICATE_BUSINESS_KEY": 2})
        self.assertEqual(self._count("SELECT COUNT(*) AS n FROM specs"), 6)  # 主表零新增
    # ------------------------------------------------------------------
    # 用例 7: 结构化日志不泄密 —— 只含 ID/计数/原因码，绝无敏感内容
    # ------------------------------------------------------------------
    def test_07_structured_logs_never_leak_sensitive_content(self):
        with self.assertLogs("sunroof.ingestion", level="INFO") as captured:
            self._ingest_node03()
        joined = " ".join(record.getMessage() for record in captured.records)

        # 事件名与白名单元数据在场
        for event in (
            "ingestion_batch_started",
            "ingestion_row_accepted",
            "ingestion_row_quarantined",
            "ingestion_row_duplicate",
            "ingestion_batch_completed",
        ):
            self.assertIn(event, joined)
        self.assertIn(BATCH_ID, joined)
        self.assertIn(CORRELATION_ID, joined)
        self.assertIn(SOURCE_ID, joined)
        self.assertIn("reason_code=", joined)

        # 敏感内容绝不在普通日志: 零件号/规格ID/标题/来源引用/项目组/计量单位
        for banned in (
            "GASKET-STRIP-A9",
            "SPEC-GSK-A-V1",
            "SPEC-SR-A-V6",
            "密封条",
            "天窗电机",
            "QMS-SOP-SR-GSK-A9",
            "OEM-A",
            "N·m",
        ):
            self.assertNotIn(banned, joined)

    # ------------------------------------------------------------------
    # 用例 8: 摄取管道回接节点 02 确定性查询引擎 (为节点 ④ 准备通过质量门的数据)
    # 隔离区记录不影响业务判定; 重放修复后 v5.0/v6.0 并存 -> 确定性 SPEC_CONFLICT
    # ------------------------------------------------------------------
    def test_08_ingested_data_feeds_deterministic_spec_check(self):
        payload = {
            "request_id": "req-node03-001",
            "task_type": "spec_check",
            "sn": "SR-20261010-0001",
            "part_no": "MOTOR-MOUNT-BOLT-03",
            "process_step": "torque_tightening",
            "measured_at": "2026-08-15",
            "measured_value": 8.2,
        }
        resp = self.client.post("/api/v1/spec-check", json=payload, headers=TOKEN_PQE_A)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["spec"]["spec_id"], "SPEC-SR-A-V5")
        run_before = database.get_task_run_by_request_id("req-node03-001")
        self.assertEqual(run_before["status"], "answered")
        self.assertEqual(run_before["matched_spec_id"], "SPEC-SR-A-V5")

        self._ingest_node03()
        # 重放前: V6 仍在隔离区 -> 查询结果不变 (隔离区绝不影响业务判定)
        resp_mid = self.client.post(
            "/api/v1/spec-check",
            json={**payload, "request_id": "req-node03-002"},
            headers=TOKEN_PQE_A,
        )
        self.assertEqual(resp_mid.json()["status"], "answered")

        q_missing = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]
        replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_missing["quarantine_id"],
                replayed_by="knowledge-admin-qms",
                fixed_payload={"owner_id": "owner-qms-sr"},
            )
        )
        self.assertEqual(replay.status, "success")

        # 重放后: v5.0 与 v6.0 两条现行规格并存 -> 确定性 SPEC_CONFLICT, 无假成功
        resp_after = self.client.post(
            "/api/v1/spec-check",
            json={**payload, "request_id": "req-node03-003"},
            headers=TOKEN_PQE_A,
        )
        self.assertEqual(resp_after.status_code, 200)
        body_after = resp_after.json()
        self.assertEqual(body_after["status"], "needs_review")
        self.assertEqual(body_after["reason_code"], "SPEC_CONFLICT")
        self.assertIsNone(body_after["spec"])
        self.assertIsNone(body_after["evaluation"])
        self.assertNotIn("OEM-B", resp_after.text)  # P-1: 响应体零跨项目泄漏
        run_after = database.get_task_run_by_request_id("req-node03-003")
        self.assertEqual(run_after["status"], "needs_review")
        self.assertEqual(run_after["reason_code"], "SPEC_CONFLICT")
        self.assertIsNone(run_after["matched_spec_id"])

        # 重放后三处一致依旧成立 (resolved 行仍计入隔离表)
        recon = get_three_way_reconciliation(BATCH_ID)
        self.assertTrue(recon["three_way_consistent"])
    # ------------------------------------------------------------------
    # 用例 9: 基础设施错误 -> 整批回滚零残留 (无部分提交), failed 幂等键不复活
    # ------------------------------------------------------------------
    def test_09_infra_failure_leaves_no_partial_commit(self):
        with patch(
            "ingestion.service.insert_accepted_spec",
            side_effect=sqlite3.OperationalError("simulated disk I/O error"),
        ):
            result = self._ingest_node03()
        self.assertEqual(result.status, BatchStatus.FAILED)
        self.assertFalse(result.is_idempotent_replay)

        # 整批回滚: 主表 / 隔离区零残留, 仅保留 failed 批次标记 (total_rows=0)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM specs WHERE batch_id = ?", (BATCH_ID,)
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM quarantine_records WHERE batch_id = ?",
                (BATCH_ID,),
            ),
            0,
        )
        batch = self._fetch_all(
            "SELECT total_rows, status FROM ingestion_batches WHERE batch_id = ?",
            (BATCH_ID,),
        )[0]
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(batch["total_rows"], 0)
        recon = get_three_way_reconciliation(BATCH_ID)
        self.assertTrue(recon["three_way_consistent"])

        # failed 是已消费的幂等键: 同 batch_id 重提交返回既有终态, 不产生第二次写入
        rerun = self._ingest_node03()
        self.assertTrue(rerun.is_idempotent_replay)
        self.assertEqual(rerun.status, BatchStatus.FAILED)

        # 运维恢复路径: 换新批次键重试 -> 正常分流
        retry = ingestion_service.process_batch_ingestion(
            _node03_request("batch-node03-retry-d")
        )
        self.assertEqual(retry.status, BatchStatus.COMPLETED_WITH_QUARANTINE)
        self.assertEqual(retry.accepted_count, 1)


if __name__ == "__main__":
    unittest.main()
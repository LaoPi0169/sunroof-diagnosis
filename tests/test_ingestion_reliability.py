"""
Week 06 可靠数据入口 —— 自动化集成测试 (批次幂等 / 隔离 / 定向重放 / 三处一致 / 日志安全)

测试原则 (回链 specs/mission-week-06.md 验收标准 与 specs/business-background.md 第 6 周):
1. 每条输入行都有可解释终态: accepted / duplicate / quarantined，无静默丢弃、无假成功;
2. 批次幂等: 相同 batch_id 重跑返回既有对账结果，主表与隔离区零写副作用;
3. 定向重放: 修复记录原子入主表，隔离历史 (原 raw_payload + 状态流转) 绝不物理删除;
4. 三处一致: 批次汇总计数 == 主表行数 == 隔离区行数 (input = accepted + duplicate + quarantined);
5. 安全日志: 普通日志只含关联 ID / 行引用 / 计数 / 原因码，绝不包含正文、公式、所有者与政策 ID;
6. 隔离性: 每个用例通过 FDE_DB_PATH 注入独立临时 SQLite 库，结果 100% 确定。

运行方式 (项目根目录):
    python -m unittest discover -s tests -v
"""
import json
import logging
import os
import sys
import tempfile
import unittest

# 保证项目根目录在 sys.path，便于从任意工作目录运行测试
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.main import app  # noqa: E402
from ingestion import service as ingestion_service  # noqa: E402
from ingestion.repository import get_three_way_reconciliation  # noqa: E402
from ingestion.schema import (  # noqa: E402
    BatchIngestionRequest,
    BatchStatus,
    ReplayRequest,
)

AGENT_TOKEN = {"X-Auth-Token": "token_agent_valid"}

BATCH_ID = "batch-w06-20260825-a"
SOURCE_ID = "northstar-policy-export-v1"
SOURCE_VERSION = "v1.0"
CORRELATION_ID = "ing-w06-a-001"

# --------------------------------------------------------------------------
# Week 06 固定合成批次 (6 行，对应 business-background.md 第 6 周 R1-R6):
#   R1 accepted  POL-PLAN-01 v3 当前有效 (support_agent, 华北)
#   R2 duplicate 同业务键 POL-PLAN-01:v3 重复出现
#   R3 quarantined POL-REFUND-02 缺 owner_id -> MISSING_OWNER
#   R4 quarantined POL-CREDIT-03 失效早于生效 -> INVALID_EFFECTIVE_RANGE
#   R5 accepted  POL-PLAN-01 v2 生命周期已过期 (主表保留, SQL 按时效拦截)
#   R6 accepted  POL-SPECIAL-09 v4 supervisor 专属 (ACL 不得降级)
# 区域统一取华北，避免污染 Week 05 华东成功路径的确定性查询。
# --------------------------------------------------------------------------
R1 = {
    "policy_id": "POL-PLAN-01", "title": "2026 套餐计费政策 v3", "version": "v3",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-01-01", "expires_at": None,
    "formula_rule": "refund = (new_price - old_price) * remaining_months",
    "source_id": "QMS-SOP-SR-01", "source_version": "v3", "owner_id": "knowledge-billing",
}
R2 = dict(R1)  # 与 R1 完全相同的业务键 POL-PLAN-01:v3
R3 = {
    "policy_id": "POL-REFUND-02", "title": "2026 退款处理政策", "version": "v1",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-02-01", "expires_at": None,
    "formula_rule": "refund_rule_v1", "source_id": "QMS-SOP-SR-05",
    "source_version": "1", "owner_id": None,  # 缺失 -> MISSING_OWNER
}
R4 = {
    "policy_id": "POL-CREDIT-03", "title": "2026 华北积分补偿政策", "version": "v1",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2026-10-01", "expires_at": "2026-09-01",  # 倒错 -> INVALID_EFFECTIVE_RANGE
    "formula_rule": "credit_rule_v1", "source_id": "QMS-SOP-SR-06",
    "source_version": "1", "owner_id": "knowledge-credit",
}
R5 = {
    "policy_id": "POL-PLAN-01", "title": "2025 套餐计费政策 v2（已过期）", "version": "v2",
    "region": "华北", "access_level": "support_agent", "is_deprecated": 0,
    "effective_at": "2025-01-01", "expires_at": "2025-12-31",
    "formula_rule": "legacy_rule_v2", "source_id": "QMS-SOP-SR-01",
    "source_version": "v2", "owner_id": "knowledge-billing",
}
R6 = {
    "policy_id": "POL-SPECIAL-09", "title": "2026 特殊升级政策（主管专用）", "version": "v4",
    "region": "华北", "access_level": "supervisor", "is_deprecated": 0,
    "effective_at": "2026-03-01", "expires_at": None,
    "formula_rule": "special_upgrade_rule_v4", "source_id": "QMS-SOP-SR-09",
    "source_version": "v4", "owner_id": "knowledge-special",
}
W06_ROWS = [R1, R2, R3, R4, R5, R6]


def _w06_request(batch_id: str = BATCH_ID) -> BatchIngestionRequest:
    return BatchIngestionRequest(
        batch_id=batch_id,
        source_id=SOURCE_ID,
        source_version=SOURCE_VERSION,
        correlation_id=CORRELATION_ID,
        rows=[dict(row) for row in W06_ROWS],
    )


class _CaptureHandler(logging.Handler):
    """捕获 fde.ingestion 结构化日志记录，用于『日志不泄密』断言。"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class IngestionReliabilityTests(unittest.TestCase):
    """集成测试链路: 批次请求 -> 数据合同校验 -> 主表/隔离区分流 -> 三处一致对账"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fde-week06-test-")
        self.db_path = os.path.join(self._tmpdir.name, "northstar_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 幂等执行 001 + 002 迁移与血缘补齐
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

    def _ingest_w06(self):
        return ingestion_service.process_batch_ingestion(_w06_request())

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
    # 6 行输入 = 3 accepted + 1 duplicate + 2 quarantined
    # ------------------------------------------------------------------
    def test_01_first_ingestion_routes_rows_and_reconciles(self):
        result = self._ingest_w06()

        # --- 批次结果 (批次侧对账口径) ---
        self.assertEqual(result.total_rows, 6)
        self.assertEqual(result.accepted_count, 3)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.quarantined_count, 2)
        self.assertEqual(result.status, BatchStatus.COMPLETED_WITH_QUARANTINE)
        self.assertFalse(result.is_idempotent_replay)
        self.assertEqual(
            result.quarantine_summary,
            {
                "MISSING_OWNER": 1,
                "INVALID_EFFECTIVE_RANGE": 1,
                "DUPLICATE_BUSINESS_KEY": 1,
            },
        )
        self.assertEqual(result.correlation_id, CORRELATION_ID)

        # --- 主表血缘可追溯: source_id / source_version / batch_id / owner_id ---
        batch_policies = self._fetch_all(
            "SELECT policy_id, source_version, version, region, access_level, "
            "owner_id, batch_id, source_id "
            "FROM policies WHERE batch_id = ? ORDER BY source_version",
            (BATCH_ID,),
        )
        self.assertEqual(
            sorted((row["policy_id"], row["source_version"]) for row in batch_policies),
            [("POL-PLAN-01", "v2"), ("POL-PLAN-01", "v3"), ("POL-SPECIAL-09", "v4")],
        )
        by_key = {(row["policy_id"], row["source_version"]): row for row in batch_policies}
        plan_v3 = by_key[("POL-PLAN-01", "v3")]
        self.assertEqual(plan_v3["access_level"], "support_agent")
        self.assertEqual(plan_v3["owner_id"], "knowledge-billing")
        self.assertEqual(plan_v3["source_id"], "QMS-SOP-SR-01")
        special = by_key[("POL-SPECIAL-09", "v4")]
        # 受限记录的访问级别没有被降级
        self.assertEqual(special["access_level"], "supervisor")

        # --- 隔离区: 无静默丢弃，原因码 + 安全来源引用 ---
        quarantine_rows = self._quarantine_rows(BATCH_ID)
        self.assertEqual(len(quarantine_rows), 3)  # 1 重复 + 2 非法
        self.assertEqual(
            sorted(row["reason_code"] for row in quarantine_rows),
            ["DUPLICATE_BUSINESS_KEY", "INVALID_EFFECTIVE_RANGE", "MISSING_OWNER"],
        )
        for row in quarantine_rows:
            self.assertEqual(row["status"], "quarantined")
            self.assertIsNone(row["replay_id"])
            self.assertRegex(row["safe_source_ref"], rf"^{SOURCE_ID}#R\d$")

        # --- 三处一致: 批次汇总 == 主表 == 隔离区 ---
        reconciliation = get_three_way_reconciliation(BATCH_ID)
        self.assertTrue(reconciliation["three_way_consistent"])
        self.assertEqual(reconciliation["total_input_rows"], 6)
        self.assertEqual(reconciliation["recorded_accepted"], 3)
        self.assertEqual(reconciliation["recorded_duplicate"], 1)
        self.assertEqual(reconciliation["recorded_quarantined"], 2)
        self.assertEqual(reconciliation["accepted_in_policies_table"], 3)
        self.assertEqual(reconciliation["quarantined_in_quarantine_table"], 3)  # 2 隔离 + 1 重复
        self.assertEqual(reconciliation["resolved_quarantine_records"], 0)

    # ------------------------------------------------------------------
    # 用例 2: 同批次重跑幂等 —— 主表/隔离区零写副作用，原审计不被覆盖
    # ------------------------------------------------------------------
    def test_02_batch_rerun_is_idempotent(self):
        first = self._ingest_w06()
        batch_before = self._fetch_all(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (BATCH_ID,)
        )[0]
        policies_before = self._count(
            "SELECT COUNT(*) AS n FROM policies WHERE batch_id = ?", (BATCH_ID,)
        )
        quarantine_before = self._count(
            "SELECT COUNT(*) AS n FROM quarantine_records WHERE batch_id = ?", (BATCH_ID,)
        )

        second = self._ingest_w06()  # 完全相同的 batch_id 与输入

        # 返回既有对账结果，并携带幂等重放信号
        self.assertTrue(second.is_idempotent_replay)
        self.assertEqual(second.total_rows, first.total_rows)
        self.assertEqual(second.accepted_count, first.accepted_count)
        self.assertEqual(second.duplicate_count, first.duplicate_count)
        self.assertEqual(second.quarantined_count, first.quarantined_count)
        self.assertEqual(second.status, first.status)
        self.assertEqual(second.quarantine_summary, {})

        # 业务状态不变: 主表不新增重复版本，隔离区不新增记录
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM policies WHERE batch_id = ?", (BATCH_ID,)
            ),
            policies_before,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM quarantine_records WHERE batch_id = ?",
                (BATCH_ID,),
            ),
            quarantine_before,
        )
        # 不能产生第二个当前政策版本
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM policies "
                "WHERE policy_id = 'POL-PLAN-01' AND source_version = 'v3'"
            ),
            1,
        )
        # 最初审计记录不被覆盖 (整行含 created_at / updated_at / 计数全部保持)
        batch_after = self._fetch_all(
            "SELECT * FROM ingestion_batches WHERE batch_id = ?", (BATCH_ID,)
        )[0]
        self.assertEqual(batch_after, batch_before)

    # ------------------------------------------------------------------
    # 用例 3: 定向重放 —— 修复记录入主表，隔离历史 (原 raw_payload/状态流转) 保留
    # ------------------------------------------------------------------
    def test_03_targeted_replay_fixed_record_accepted_history_kept(self):
        self._ingest_w06()
        q_missing_owner = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]
        quarantine_id = q_missing_owner["quarantine_id"]

        replay_request = ReplayRequest(
            quarantine_id=quarantine_id,
            replayed_by="knowledge-admin",
            fixed_payload={"owner_id": "knowledge-refund"},  # 补丁式修复
            notes="已由知识管理员在隔离区补齐丢失的 owner_id 并重新发布",
        )
        replay = ingestion_service.execute_targeted_replay(replay_request)

        # --- 重放结果 ---
        self.assertEqual(replay.status, "success")
        self.assertEqual(replay.accepted_policy_id, "POL-REFUND-02")
        self.assertTrue(replay.replay_id.startswith("rep-"))

        # --- 主表新增业务版本，血缘批次带 replay- 前缀 ---
        replayed_rows = self._fetch_all(
            "SELECT policy_id, source_version, owner_id, batch_id, access_level "
            "FROM policies WHERE policy_id = 'POL-REFUND-02'"
        )
        self.assertEqual(len(replayed_rows), 1)
        row = replayed_rows[0]
        self.assertEqual(row["source_version"], "1")
        self.assertEqual(row["owner_id"], "knowledge-refund")
        self.assertEqual(row["batch_id"], f"replay-{replay.replay_id}")
        self.assertEqual(row["access_level"], "support_agent")

        # --- 原隔离记录: 状态 resolved + 关联 replay_id，原始内容与创建时间保留 ---
        q_after = self._fetch_all(
            "SELECT * FROM quarantine_records WHERE quarantine_id = ?", (quarantine_id,)
        )[0]
        self.assertEqual(q_after["status"], "resolved")
        self.assertEqual(q_after["replay_id"], replay.replay_id)
        self.assertEqual(q_after["created_at"], q_missing_owner["created_at"])
        # 原始 raw_payload 与修复前的输入行完全一致 (修复历史不被改写)
        self.assertEqual(json.loads(q_after["raw_payload"]), R3)

        # --- 其他隔离记录不受影响 (定向: 只改变目标记录) ---
        q_others = self._fetch_all(
            "SELECT * FROM quarantine_records "
            "WHERE batch_id = ? AND quarantine_id != ?",
            (BATCH_ID, quarantine_id),
        )
        self.assertTrue(all(r["status"] == "quarantined" for r in q_others))
        self.assertTrue(all(r["replay_id"] is None for r in q_others))

        # --- 重放审计日志完整可追溯 ---
        logs = self._fetch_all(
            "SELECT * FROM ingestion_replay_logs WHERE replay_id = ?", (replay.replay_id,)
        )
        self.assertEqual(len(logs), 1)
        log = logs[0]
        self.assertEqual(log["status"], "success")
        self.assertEqual(log["quarantine_id"], quarantine_id)
        self.assertEqual(log["batch_id"], BATCH_ID)
        self.assertEqual(log["replayed_by"], "knowledge-admin")
        self.assertEqual(log["new_policy_id"], "POL-REFUND-02")
        self.assertIn("knowledge-refund", log["fixed_payload"])

        # --- 重放后三处一致依旧成立 (resolved 行仍计入隔离表) ---
        reconciliation = get_three_way_reconciliation(BATCH_ID)
        self.assertTrue(reconciliation["three_way_consistent"])
        self.assertEqual(reconciliation["resolved_quarantine_records"], 1)
        self.assertEqual(reconciliation["quarantined_in_quarantine_table"], 3)

    # ------------------------------------------------------------------
    # 用例 4: 重放拒绝路径 —— 未修复/已 resolved 的记录不得进入主表
    # ------------------------------------------------------------------
    def test_04_replay_rejections_keep_quarantine_state(self):
        self._ingest_w06()

        # 4a. 已 resolved 的记录不可再次重放
        q_owner = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]
        first_replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_owner["quarantine_id"],
                replayed_by="knowledge-admin",
                fixed_payload={"owner_id": "knowledge-refund"},
            )
        )
        self.assertEqual(first_replay.status, "success")
        second_attempt = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_owner["quarantine_id"],
                replayed_by="knowledge-admin",
                fixed_payload={"owner_id": "knowledge-refund-2"},
            )
        )
        self.assertEqual(second_attempt.status, "failed")
        self.assertIn("not replayable", second_attempt.message)

        # 4b. 未修复的记录重放: 校验失败 -> rejected，不写入主表，隔离状态不变
        q_range = self._quarantine_rows(BATCH_ID, "INVALID_EFFECTIVE_RANGE")[0]
        bad_replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_range["quarantine_id"],
                replayed_by="knowledge-admin",
                fixed_payload={},  # 什么都没修
            )
        )
        self.assertEqual(bad_replay.status, "failed")
        self.assertIn("INVALID_EFFECTIVE_RANGE", bad_replay.message)

        q_range_after = self._fetch_all(
            "SELECT * FROM quarantine_records WHERE quarantine_id = ?",
            (q_range["quarantine_id"],),
        )[0]
        self.assertEqual(q_range_after["status"], "quarantined")
        self.assertIsNone(q_range_after["replay_id"])
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM policies WHERE policy_id = 'POL-CREDIT-03'"
            ),
            0,
        )
        # 失败重放尝试也保留审计 (修复历史完整)
        failed_logs = self._fetch_all(
            "SELECT * FROM ingestion_replay_logs WHERE status = 'failed'"
        )
        self.assertEqual(len(failed_logs), 1)
        self.assertEqual(failed_logs[0]["quarantine_id"], q_range["quarantine_id"])

    # ------------------------------------------------------------------
    # 用例 5: 权限默认隔离 (Default Deny) —— 缺失/未知 access_level 绝不入主表
    # ------------------------------------------------------------------
    def test_05_unknown_access_level_default_deny(self):
        public_row = {**R1, "policy_id": "POL-PUBLIC-77", "access_level": "public"}
        missing_acl_row = {**R1, "policy_id": "POL-NOACL-78"}
        del missing_acl_row["access_level"]
        supervisor_row = {**R6, "policy_id": "POL-SUPER-79"}

        result = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-w06-acl-check",
                source_id=SOURCE_ID,
                source_version=SOURCE_VERSION,
                correlation_id="ing-w06-acl-001",
                rows=[public_row, missing_acl_row, supervisor_row],
            )
        )

        self.assertEqual(result.status, BatchStatus.COMPLETED_WITH_QUARANTINE)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.duplicate_count, 0)
        self.assertEqual(result.quarantined_count, 2)
        self.assertEqual(result.quarantine_summary, {"UNKNOWN_ACCESS_LEVEL": 2})

        # 未知/缺失 ACL 的行绝不进入主表 (默认拒绝，不自动扩大可见范围)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM policies "
                "WHERE policy_id IN ('POL-PUBLIC-77', 'POL-NOACL-78')"
            ),
            0,
        )
        quarantined = self._fetch_all(
            "SELECT reason_code, safe_source_ref FROM quarantine_records "
            "WHERE batch_id = 'batch-w06-acl-check'"
        )
        self.assertEqual(
            sorted(row["reason_code"] for row in quarantined),
            ["UNKNOWN_ACCESS_LEVEL", "UNKNOWN_ACCESS_LEVEL"],
        )
        # supervisor 行正常入库且访问级别未被降级
        supervisor_rows = self._fetch_all(
            "SELECT access_level FROM policies WHERE policy_id = 'POL-SUPER-79'"
        )
        self.assertEqual(len(supervisor_rows), 1)
        self.assertEqual(supervisor_rows[0]["access_level"], "supervisor")

    # ------------------------------------------------------------------
    # 用例 6: 结构化日志不泄密 —— 只含关联 ID/行引用/计数/原因码，对账公式可核对
    # ------------------------------------------------------------------
    def test_06_structured_logs_never_leak_sensitive_content(self):
        logger = logging.getLogger("fde.ingestion")
        handler = _CaptureHandler()
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            self._ingest_w06()
            q_owner = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]
            ingestion_service.execute_targeted_replay(
                ReplayRequest(
                    quarantine_id=q_owner["quarantine_id"],
                    replayed_by="knowledge-admin",
                    fixed_payload={"owner_id": "knowledge-refund"},
                )
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        messages = [record.getMessage() for record in handler.records]
        self.assertTrue(messages, "应产生结构化日志事件")

        # 1. 批次完成事件携带三处一致对账口径 (input = accepted + duplicate + quarantined)
        completed = [m for m in messages if m.startswith("ingestion_batch_completed")]
        self.assertEqual(len(completed), 1)
        fields = dict(
            token.split("=", 1) for token in completed[0].split() if "=" in token
        )
        self.assertEqual(fields["batch_id"], BATCH_ID)
        self.assertEqual(fields["correlation_id"], CORRELATION_ID)
        input_rows = int(fields["input_rows"])
        accepted_rows = int(fields["accepted_rows"])
        duplicate_rows = int(fields["duplicate_rows"])
        quarantined_rows = int(fields["quarantined_rows"])
        self.assertEqual(input_rows, 6)
        self.assertEqual(
            input_rows, accepted_rows + duplicate_rows + quarantined_rows
        )
        self.assertEqual((accepted_rows, duplicate_rows, quarantined_rows), (3, 1, 2))

        # 2. 日志绝对不包含政策正文 / 公式 / 所有者 / 政策 ID 等敏感内容
        forbidden_markers = [
            "套餐", "退款", "积分", "升级政策",          # 政策标题片段
            "refund =", "refund_rule", "remaining_months", "legacy_rule", "credit_rule",
            "special_upgrade",                          # 公式规则
            "knowledge-",                               # 知识所有者 ID
            "POL-PLAN-01", "POL-REFUND-02", "POL-CREDIT-03", "POL-SPECIAL-09",
            '"policy_id"', "raw_payload",               # 原始行内容
        ]
        for message in messages:
            for marker in forbidden_markers:
                self.assertNotIn(
                    marker, message, f"日志不得包含敏感内容 '{marker}': {message}"
                )

        # 3. 重放事件仅记录关联 ID 与结果
        replay_events = [m for m in messages if m.startswith("ingestion_replay_")]
        self.assertTrue(any("result=success" in m for m in replay_events))

    # ------------------------------------------------------------------
    # 用例 7: 跨批次重复检测 + Week 05 种子数据血缘口径
    # ------------------------------------------------------------------
    def test_07_cross_batch_duplicate_and_seed_lineage(self):
        self._ingest_w06()

        # 新批次再次推送同一业务键 (POL-PLAN-01:v3) -> duplicate，不产生第二次业务效果
        followup = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-w06-followup-b",
                source_id=SOURCE_ID,
                source_version=SOURCE_VERSION,
                correlation_id="ing-w06-b-001",
                rows=[dict(R1)],
            )
        )
        self.assertEqual(followup.accepted_count, 0)
        self.assertEqual(followup.duplicate_count, 1)
        self.assertEqual(followup.status, BatchStatus.COMPLETED)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM policies "
                "WHERE policy_id = 'POL-PLAN-01' AND source_version = 'v3'"
            ),
            1,
        )
        followup_quarantine = self._fetch_all(
            "SELECT reason_code FROM quarantine_records WHERE batch_id = 'batch-w06-followup-b'"
        )
        self.assertEqual(
            [row["reason_code"] for row in followup_quarantine],
            ["DUPLICATE_BUSINESS_KEY"],
        )

        # Week 05 种子数据血缘回填: 每个业务版本均可追溯 source_id/source_version/batch_id
        seed_rows = self._fetch_all(
            "SELECT policy_id, source_version, batch_id, source_id FROM policies "
            "WHERE batch_id = 'week05-baseline-seed' ORDER BY policy_id"
        )
        self.assertEqual(len(seed_rows), 3)
        seed_keys = {row["policy_id"]: row["source_version"] for row in seed_rows}
        self.assertEqual(
            seed_keys,
            {"POL-2025-BIL-01": "v1", "POL-2026-BIL-01": "v3", "POL-2026-REF-09": "v4"},
        )

    # ------------------------------------------------------------------
    # 用例 8: 摄取管道回接 Week 05 确定性查询引擎 (为第 7 周准备好通过质量门的数据)
    # ------------------------------------------------------------------
    def test_08_ingested_data_feeds_week05_policy_check(self):
        self._ingest_w06()

        # 重放前: 华北 support_agent 只有一个有效版本 POL-PLAN-01 v3 -> answered
        payload_before = {
            "request_id": "req-w06-check-001",
            "task_type": "billing_policy_check",
            "region": "华北",
            "purchased_at": "2026-08-15",
            "amount": 100.0,
        }
        resp = self.client.post(
            "/api/v1/policy-check", json=payload_before, headers=AGENT_TOKEN
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["policy_details"]["policy_id"], "POL-PLAN-01")
        self.assertEqual(body["policy_details"]["version"], "v3")
        run_before = database.get_task_run_by_request_id("req-w06-check-001")
        self.assertEqual(run_before["status"], "answered")
        self.assertEqual(run_before["matched_policy_id"], "POL-PLAN-01")

        # 定向重放 R3 修复记录 -> 华北出现第二个 support_agent 版本
        q_owner = self._quarantine_rows(BATCH_ID, "MISSING_OWNER")[0]
        replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=q_owner["quarantine_id"],
                replayed_by="knowledge-admin",
                fixed_payload={"owner_id": "knowledge-refund"},
            )
        )
        self.assertEqual(replay.status, "success")

        # 重放后: 两个版本 (v3 与 v1) 同时有效 -> 确定性 POLICY_CONFLICT，无假成功
        payload_after = {**payload_before, "request_id": "req-w06-check-002"}
        resp = self.client.post(
            "/api/v1/policy-check", json=payload_after, headers=AGENT_TOKEN
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "needs_review")
        self.assertFalse(body["eligible"])
        self.assertIsNone(body["policy_details"])
        self.assertEqual(body["reason_code"], "POLICY_CONFLICT")
        run_after = database.get_task_run_by_request_id("req-w06-check-002")
        self.assertEqual(run_after["status"], "needs_review")
        self.assertEqual(run_after["reason_code"], "POLICY_CONFLICT")
        self.assertIsNone(run_after["matched_policy_id"])


if __name__ == "__main__":
    unittest.main()


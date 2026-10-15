"""
SLO 基线聚合器测试 (阶段 E 批次 2) —— 回链 specs/phase-e-brief-draft-v1.md §E2 验收口径

  01 [U] 守卫: 分母 < min_denominator -> insufficient_data 且比率置 None (小样本不下结论)
  02 分母足够 -> baseline_only_no_target_merge: 口径可算但目标仍 [U]; denied 单独计数不折进分子
  03 恒等硬门交叉对账: 被阻断动作改变任务终态的矛盾被抓住 (违反即安全事件); 正常库恒 0
  04 分母口径声明 + 两数永不合并 + 目标全 [U] + 禁止合成回放代填 (诚实性字段完备)
  05 SLO-3 不适用登记: 任务表无未确认终态 -> not_applicable, 不发明代用口径
  06 只读聚合: 聚合前后数据库字节一致 (绝不写库)

运行: python -m unittest tests.test_slo_baselining -v
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from application.database import init_db  # noqa: E402
from deploy.slo_baseline_aggregator import aggregate  # noqa: E402

WIN_START, WIN_END = "2026-11-01 00:00:00", "2026-11-08 00:00:00"


def _make_db(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "slo-test.db")
    init_db(path)
    return path


def _insert_task(path: str, task_id: str, status: str, created_at: str = "2026-11-03 12:00:00",
                 oem_project: str = "OEM-A"):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO spec_check_tasks (task_id, case_id, sn, oem_project, part_no,"
            " process_step, measured_at, measured_value, assigned_pqe_id, correlation_id,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, f"SR-CASE-{task_id}", f"SN-{task_id}", oem_project,
             "MOTOR-MOUNT-BOLT-03", "torque_tightening", "2026-08-15", 9.6,
             "pqe_chen_oema", f"corr-{task_id}", status, created_at, created_at))
        conn.commit()
    finally:
        conn.close()


def _insert_denied_action(path: str, run_id: str, task_id: str, action_status: str = "denied"):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO task_action_runs (run_id, task_id, correlation_id, user_id,"
            " user_role, action, status) VALUES (?,?,?,?,?,?,?)",
            (run_id, task_id, f"corr-{run_id}", "pqe_chen_oemb", "pqe",
             "check_spec", action_status))
        conn.commit()
    finally:
        conn.close()


class SloBaselineAggregatorTests(unittest.TestCase):
    """[U] 守卫 + 口径机械化 + 恒等硬门交叉对账。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sunroof-slo-test-")
        self.db = _make_db(self._tmp.name)
        # 001/003 夹具任务 created_at 为建库时刻 (非窗口内) —— 窗口过滤生效的基线

    def tearDown(self):
        self._tmp.cleanup()

    def test_01_insufficient_data_guard(self):
        # 窗口内只有 1 条任务 -> verdict=insufficient_data, slo_1.value=None
        _insert_task(self.db, "TASK-SLO-001", "answered")
        report = aggregate(self.db, WIN_START, WIN_END)
        self.assertEqual(report["verdict"], "insufficient_data")
        self.assertIsNone(report["slo_1_allowed_terminal_rate"]["value"])
        self.assertEqual(report["denominator"]["total_tasks"], 1)
        # 小样本比率绝不输出 (拿 1/1=100% 当基线是被禁止的行为)
        self.assertEqual(report["min_denominator"], 30)

    def test_02_baseline_only_no_target_merge(self):
        # 40 条: 30 answered + 4 needs_review + 4 initialized + 2 denied
        # -> allowed=34/40=0.85; initialized 不折叠、denied 单独计数; 目标仍 [U]
        for i in range(30):
            _insert_task(self.db, f"TASK-SLO-A{i:02d}", "answered")
        for i in range(4):
            _insert_task(self.db, f"TASK-SLO-N{i:02d}", "needs_review")
        for i in range(4):
            _insert_task(self.db, f"TASK-SLO-I{i:02d}", "initialized")
        for i in range(2):
            _insert_task(self.db, f"TASK-SLO-D{i:02d}", "denied")
            _insert_denied_action(self.db, f"run-d{i}", f"TASK-SLO-D{i:02d}")
        report = aggregate(self.db, WIN_START, WIN_END)
        self.assertEqual(report["verdict"], "baseline_only_no_target_merge")
        self.assertAlmostEqual(report["slo_1_allowed_terminal_rate"]["value"], 0.85)
        self.assertEqual(report["blocked_count"], 2)
        self.assertEqual(report["non_terminal_count"], 4)
        self.assertEqual(report["slo_1_allowed_terminal_rate"]["target"], "[U]")
        self.assertTrue(report["honesty"]["targets_all_u"])

    def test_03_identity_gate_crosscheck(self):
        # 正常库: denied 任务终态仍为 denied/initialized -> 矛盾 0 (结构保证)
        for i in range(35):
            _insert_task(self.db, f"TASK-SLO-B{i:02d}", "answered")
        report = aggregate(self.db, WIN_START, WIN_END)
        self.assertEqual(report["slo_2_forbidden_effect_count"]["value"], 0)
        # 矛盾注入: denied 动作行对应任务终态被"改变"为 answered -> 抓住 = 1
        _insert_task(self.db, "TASK-SLO-X", "answered")
        _insert_denied_action(self.db, "run-x", "TASK-SLO-X")
        report2 = aggregate(self.db, WIN_START, WIN_END)
        self.assertEqual(report2["slo_2_forbidden_effect_count"]["value"], 1)
        self.assertIn("安全事件", report2["slo_2_forbidden_effect_count"]["definition"])

    def test_04_honesty_fields_complete(self):
        _insert_task(self.db, "TASK-SLO-001", "answered")
        report = aggregate(self.db, WIN_START, WIN_END)
        blob = json.dumps(report, ensure_ascii=False)
        self.assertIn("永不共享", report["denominator"]["note"])   # 两数永不合并
        self.assertIn("永不合并", report["denominator"]["note"])
        self.assertIn("[U]", blob)
        self.assertTrue(report["honesty"]["no_synthetic_replay_substitution"])
        self.assertIn("slo-adoption-runbook-v1.md", report["honesty"]["note"])

    def test_05_slo3_not_applicable_honest(self):
        _insert_task(self.db, "TASK-SLO-001", "answered")
        report = aggregate(self.db, WIN_START, WIN_END)
        slo3 = report["slo_3_unconfirmed_resolution"]
        self.assertIsNone(slo3["value"])
        self.assertIn("not_applicable", slo3["applicability"])

    def test_06_aggregation_is_readonly(self):
        _insert_task(self.db, "TASK-SLO-001", "answered")
        before = hashlib.sha256(open(self.db, "rb").read()).hexdigest()
        aggregate(self.db, WIN_START, WIN_END)
        after = hashlib.sha256(open(self.db, "rb").read()).hexdigest()
        self.assertEqual(before, after)  # 只读承诺: 绝不写库


if __name__ == "__main__":
    unittest.main()


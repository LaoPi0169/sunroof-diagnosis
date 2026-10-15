"""
SRE 值守工作台测试 (阶段 E 批次 3) —— 回链 specs/phase-e-brief-draft-v1.md §E3 验收口径

  01 班前健康检查: GREEN 且五项全过 (完整性/哈希链/评测器 v2/聚合器守卫/冻结产物)
  02 评测复核台: 60 例列表 + 状态门 58/60 (v2 真判定) + 汇总字段完备
  03 复核详情 (HOLD-06): 子仓库候选正确拦截 -> v2 match + required 全 hit (真实奖励)
  04 复核详情 (HOLD-12): 跨族漏答 -> v2 miss 如实呈现
  05 病灶对照: v1 等价构造虚 hit vs v2 专属触发终局 miss (教学演示语义)
  06 SLO 采集台: 教学库 insufficient_data + 比率 None ([U] 守卫) + 两数不合并声明
  07 定标看板: 4 红线 + 5 阶段 + 诚实注
  08 只读纪律: POST 405 / 未知路径 404 / 静态目录穿越防护 / index 可服务

运行: python -m unittest tests.test_workbench_sre -v
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from workbench_sre.app import WorkbenchSREHandler  # noqa: E402

BASE = None


def _get(path: str):
    """GET 返回 (status, json dict / bytes)。"""
    try:
        with urllib.request.urlopen(BASE + path) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body[:1] in (b"{", b"[") else body)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def setUpModule():
    global BASE, _SERVER
    _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchSREHandler)
    threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{_SERVER.server_address[1]}"
    time.sleep(0.2)


def tearDownModule():
    if _SERVER:
        _SERVER.shutdown()


import time  # noqa: E402  (setUpModule 用)

_SERVER = None


class WorkbenchSREReadonlyTests(unittest.TestCase):
    """五面板只读 API + 值守纪律。"""

    def test_01_precheck_green(self):
        status, r = _get("/api/v1/duty/precheck")
        self.assertEqual(status, 200)
        self.assertEqual(r["overall"], "GREEN")
        self.assertEqual(len(r["checks"]), 5)
        self.assertTrue(all(c["ok"] for c in r["checks"]))

    def test_02_evaluator_cases_60(self):
        status, r = _get("/api/v1/evaluator/cases")
        self.assertEqual(status, 200)
        self.assertEqual(len(r["cases"]), 60)
        self.assertEqual(r["summary"]["state_gate_v2"], "58/60")
        self.assertEqual(r["summary"]["fact_layer"]["required_facts_total"], 81)

    def test_03_case_detail_hold06_rewarded(self):
        _, r = _get("/api/v1/evaluator/case/SR-HOLD-06")
        c = r["case"]
        self.assertTrue(c["state_match_v2"])          # 子仓库短语门正确拦截
        self.assertTrue(all(f["hit"] for f in c["required_fact_checks"]))
        self.assertTrue(all(f["rule_id"] != "substance_referral"
                            for f in c["required_fact_checks"]))  # 真实判定非兜底

    def test_04_case_detail_hold12_honest_miss(self):
        _, r = _get("/api/v1/evaluator/case/SR-HOLD-12")
        self.assertFalse(r["case"]["state_match_v2"])  # 跨族漏答如实 miss
        self.assertTrue(r["case"]["state_match_v1_recorded"])  # v1 恒真差异在档

    def test_05_fallthrough_demo(self):
        _, r = _get("/api/v1/defect/fallthrough-demo")
        v1 = r["v1_semantic_simulated"]
        self.assertIn("空真", v1["scan_trace"][1]["verdict"])
        self.assertIn("等价构造", v1["disclaimer"])
        self.assertEqual(r["v2_semantic_real"]["rule_id"], "no_current_source")
        self.assertIs(r["v2_semantic_real"]["hit"], False)

    def test_06_slo_collect_u_guard(self):
        _, r = _get("/api/v1/slo/collect?start=2026-11-01&end=2026-11-08")
        p = r["report"]
        self.assertEqual(p["verdict"], "insufficient_data")  # 教学库分母 < 30
        self.assertIsNone(p["slo_1_allowed_terminal_rate"]["value"])
        self.assertIn("永不合并", p["denominator"]["note"])
        self.assertTrue(p["honesty"]["targets_all_u"])

    def test_07_runbook_steps(self):
        _, r = _get("/api/v1/slo/runbook-steps")
        self.assertEqual(len(r["red_lines"]), 4)
        self.assertEqual(len(r["phases"]), 5)

    def test_08_readonly_discipline(self):
        # POST 显式拒绝 (零写端点)
        req = urllib.request.Request(BASE + "/api/v1/duty/precheck", method="POST")
        try:
            urllib.request.urlopen(req)
            self.fail("POST should be rejected")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 405)
        # 未知路径 404
        status, _ = _get("/api/v1/nope")
        self.assertEqual(status, 404)
        # 静态目录穿越防护
        status, _ = _get("/static/../app.py")
        self.assertEqual(status, 404)
        # index 可服务
        status, body = _get("/")
        self.assertEqual(status, 200)
        self.assertIn("SRE 值守工作台".encode("utf-8"), body)


if __name__ == "__main__":
    unittest.main()


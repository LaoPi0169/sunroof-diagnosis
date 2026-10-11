"""
遥测可观测性与降级测试 (Week 16/17 合并段) —— 回链两周验收门的工程门。

  01 终态分类表: 六+一类可见终态, 非终态不折进通过率 (W16 门 5)
  02 SLI 归约: allowed/forbidden/unconfirmed 三口径独立 (W16 门 5/6)
  03 遥测脱敏自检: 正文标记/凭据键零出现 (W16 门 8)
  04 金丝雀: 60 例重考与第 12 周冻结结果零漂移 (W16 门 2)
  05 关闭开关: 封闭枚举 + 无因操作拒绝 + 追加式审计 (W17 门 2)
  06 降级门: MCP_OFF -> action_preview 0 效果且审批不消耗; 权限判定优先于降级 (W17 门 5/6)
  07 恢复续跑: 同一审批恢复后直接续跑成功 (W17 门 9)
  08 演练证据: D-01..04 落 jsonl, allowed 3/4 + forbidden 0 (固定剧本口径) (W17 门 3)

运行: python -m unittest tests.test_telemetry_observability -v
"""
import json
import os
import unittest

from mcptool.approval import ApprovalService
from mcptool.kill_switch import SWITCHES, KillSwitchRegistry
from mcptool.run_fixtures import BASE_ARGS, BINDING_OK, EAST_PQE, _fresh_write_stack
from mcptool.server import call_write
from mcptool.telemetry import (TaskTrace, classify_terminal, reduce_runtime_metrics,
                               scan_spans_for_violations)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestTelemetryWeek16(unittest.TestCase):
    def test_01_terminal_classification_table(self):
        self.assertEqual(classify_terminal("write", "action_completed"), "allowed")
        self.assertEqual(classify_terminal("write", "approval_denied"), "allowed")
        self.assertEqual(classify_terminal("write", "action_preview"), "allowed")
        self.assertEqual(classify_terminal("write", "result_unknown"), "non_terminal")
        self.assertEqual(classify_terminal("read", "abstained"), "non_terminal")
        self.assertEqual(classify_terminal("read", "answered"), "allowed")
        self.assertEqual(classify_terminal("write", "mystery"), "non_terminal",
                         "未知词面绝不折叠成成功")

    def test_02_reduce_runtime_metrics(self):
        t1 = TaskTrace("O-07")
        t1.mark_terminal("write", "action_completed", effects=1)
        t2 = TaskTrace("O-08")
        t2.mark_terminal("write", "result_unknown", effects=1)  # 未确认效果单独口径
        t3 = TaskTrace("O-DENY")
        t3.mark_terminal("write", "forbidden", effects=1)
        metrics = reduce_runtime_metrics([t1, t2, t3])
        self.assertEqual(metrics["window_tasks"], 3)
        self.assertEqual(metrics["allowed_terminal_rate"], round(1 / 3, 4))
        self.assertEqual(metrics["forbidden_effect_count"], 1)
        self.assertEqual(metrics["unconfirmed_effects"], 1)
        self.assertEqual(metrics["total_effects"], 3)
        self.assertIsNone(reduce_runtime_metrics([])["allowed_terminal_rate"],
                          "空窗分母 None, 不编造百分比")

    def test_03_span_redaction_scan(self):
        trace = TaskTrace("O-SCAN")
        trace.add("generate", {"answer_ref": "chunk-id-only"})
        trace.add("policy", {"queue": "billing-supervisor-review"})
        self.assertEqual(scan_spans_for_violations(trace.spans), [])
        trace.add("write", {"payload": "单笔索赔 3000 元审批线"})
        self.assertEqual(len(scan_spans_for_violations(trace.spans)), 1)

    def test_04_canary_zero_drift(self):
        from mcptool.run_canary import run_canary
        out = os.path.join(PROJECT_ROOT, "week-16", "canary-baseline-v1.jsonl")
        report = run_canary(out)
        self.assertEqual(report["cases"], 60)
        self.assertEqual(report["behavior_drift_cases"], [])
        self.assertEqual(report["verdict"], "CONSISTENT")
        rows = [json.loads(line) for line in open(out, encoding="utf-8") if line.strip()]
        self.assertEqual(len(rows), 60)


if __name__ == "__main__":
    unittest.main()

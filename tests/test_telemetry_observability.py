"""可观测性测试 (Week 16) —— 金丝雀/回放/遥测合同/双视图纪律/工作台 API。

用例 (12):
  01 任务追踪九阶段: 任一 task_run_id 可还原全程 (版本链 + 审批/幂等键关联);
  02 终态分类对照表: 内部状态 -> 六类可见终态逐项断言 (pending/unknown 非终态);
  03 金丝雀复现冻结基线: 58/60 + 行为零漂移 + 失败指路 F12-01/F12-02;
  04 金丝雀只读消费冻结物: week-12 文件 sha 前后一致 + 答案键 LF sha 对齐第 12 周;
  05 回放终态序列与效果: 10 任务剧本 + allowed 8/10 + 效果合计恰 2 + 未确认 1;
  06 两视图永不合并: 同一案例 (NS-HOLD-06) 双视图结论不同 + 分母口径警示在位;
  07 遥测脱敏: 90 段 span 流零违规 + 扫描器对污染样本有效 (报出键名不回显内容);
  08 工作台 API 端到端: replay/canary/trace/redaction/slo/reset + 404;
  09 SLO 草案 v0 诚实性: draft 状态 + [U] 目标 + 禁止代填声明 + 无编造百分比;
  10 工具允许列表不变: 仍 1 读 + 3 写, 效果只来自 ticket.create 路径;
  11 样例工件门: week-16/telemetry-span-sample-v1.jsonl 90 行且逐行可扫描零违规;
  12 归约器语义: 拒绝终态携带效果 -> forbidden_effect_count 抓得住 (合同违规可见)。

边界: canary 全量 60 例在 setUpClass 只跑一次 (类级缓存); 冻结物零写入由
test_04 字节级复核; duration_ms 为观测值, 全套不设时延断言门。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from mcptool import contract as C  # noqa: E402
from mcptool.client import ALLOWED_TOOLS  # noqa: E402
from mcptool.run_canary import run_canary  # noqa: E402
from mcptool.run_telemetry_replay import run_replay  # noqa: E402
from mcptool.telemetry import (  # noqa: E402
    STAGES, TaskTrace, classify_terminal, reduce_runtime_metrics,
    scan_spans_for_violations,
)
from workbench_observability.app import SLO_PROPOSAL_V0, app  # noqa: E402

WEEK12_READONLY_FILES = (
    "week-12/rag-per-case-results-v1.jsonl",
    "week-12/rag-system-manifest-v1.json",
    "week-12/failure-register-v1.jsonl",
    "week-12/rag-evaluation-report-v1.md",
    "week-12/regression-seeds-v1.jsonl",
)
ANSWER_KEY_SHA256_FROZEN = (  # 第 12 周报告记录的逐例结果 sha256 (LF 归一口径)
    "5a614cda697c90990b5a035c8ac3cedf42ab47977085aa9d3af644f40da8dfed")
SPAN_SAMPLE_PATH = os.path.join(PROJECT_ROOT, "week-16",
                                "telemetry-span-sample-v1.jsonl")


def _sha256_of(rel_path: str) -> str:
    with open(os.path.join(PROJECT_ROOT, rel_path), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Week16ObservabilityTest(unittest.TestCase):
    """类级夹具: canary/replay 各跑一次, 供全部用例复用。"""

    @classmethod
    def setUpClass(cls):
        cls.shas_before = {p: _sha256_of(p) for p in WEEK12_READONLY_FILES}
        cls.canary_rows, cls.canary_summary = run_canary()
        cls.replay = run_replay(seed="unittest")
        cls.shas_after = {p: _sha256_of(p) for p in WEEK12_READONLY_FILES}

    # ---- 01 任务追踪九阶段 ----
    def test_01_task_trace_nine_stages_with_versions_and_refs(self):
        by_id = {t.task_run_id: t for t in self.replay["task_traces"]}
        approved = by_id[[k for k in by_id if k.endswith("O-04")][0]]
        self.assertEqual([sp["stage"] for sp in approved.spans], list(STAGES))
        for span in approved.spans:
            self.assertEqual(span["task_run_id"], approved.task_run_id)
        stages = {sp["stage"]: sp for sp in approved.spans}
        # 断点 #1 (写侧): 策略段带 policy_version; 引用同一允许队列
        self.assertIn("policy_version",
                      stages["citation_and_policy_verification"]["versions"])
        # 断点 #2: MCP -> 审批 + 幂等键 + 工单号关联
        mcp_refs = stages["mcp_call"]["safe_refs"]
        self.assertEqual(mcp_refs["tool"], "ticket.create")
        self.assertTrue(mcp_refs["idempotency_key"].endswith("ticket.create:1"))
        self.assertTrue(str(mcp_refs["ticket_id"]).startswith("SIM-"))
        self.assertEqual(mcp_refs["verified_by"], "ticket.get")
        approval_refs = stages["approval"]["safe_refs"]
        self.assertTrue(approval_refs["approval_id"].startswith("approval-"))
        self.assertEqual(approval_refs["decision"], "approved")
        # 断点 #1 (读侧): RAG 版本链在 retrieval/generation 段
        read_task = by_id[[k for k in by_id if k.endswith("O-01")][0]]
        read_stages = {sp["stage"]: sp for sp in read_task.spans}
        self.assertEqual(read_stages["retrieval"]["versions"]["corpus_version"],
                         "northstar-corpus-w09-v1")
        self.assertEqual(read_stages["generation"]["versions"]["candidate_id"],
                         "northstar-rag-readonly-v1")

    # ---- 02 终态分类对照表 ----
    def test_02_terminal_classification_table(self):
        from mcptool import write_contract as W
        cases = [
            ("write", W.STATUS_COMPLETED, "action_completed", True),
            ("write", W.STATUS_DENIED, "approval_denied", True),
            ("write", W.ERROR_APPROVAL_REVOKED, "approval_revoked", True),
            ("write", W.ERROR_APPROVAL_REQUIRED, "approval_denied", True),
            ("write", W.ERROR_APPROVAL_EXPIRED, "approval_denied", True),
            ("write", W.ERROR_APPROVAL_MISMATCH, "approval_denied", True),
            ("write", W.STATUS_UNKNOWN, None, False),
            ("write", W.STATUS_RECONCILIATION_PENDING, None, False),
            ("write", W.STATUS_PREVIEW, None, False),
            ("write", W.ERROR_DEADLINE_EXCEEDED, None, False),
            ("write", W.ERROR_INVALID_ARGUMENT, "system_failed", False),
            ("write", W.ERROR_CONTRACT_VIOLATION, "system_failed", False),
            ("read", "answered", "answered", True),
            ("read", "refused", "abstained", True),
            ("read", "needs_review", "abstained", True),
            ("read", "escalated", "escalated", True),
            ("read", "deadline_exceeded", "system_failed", False),
            ("read", "not_available", "system_failed", False),
        ]
        for kind, outcome, expected_visible, expected_allowed in cases:
            visible, allowed = classify_terminal(kind, outcome)
            self.assertEqual((visible, allowed),
                             (expected_visible, expected_allowed),
                             f"{kind}/{outcome}")

    # ---- 03 金丝雀复现冻结基线 ----
    def test_03_canary_reproduces_frozen_baseline(self):
        s = self.canary_summary
        self.assertEqual(s["view"], "canary")
        self.assertEqual(s["cases_total"], 60)
        self.assertEqual(s["state_match_rate"], "58/60")
        self.assertEqual(s["behavior_drift_cases"], [])
        self.assertEqual(s["hygiene_violation_cases"], [])
        mismatch_ids = sorted(m["case_id"] for m in s["mismatches"])
        self.assertEqual(mismatch_ids, ["NS-HOLD-06", "NS-HOLD-08"])
        hold06 = next(m for m in s["mismatches"] if m["case_id"] == "NS-HOLD-06")
        self.assertTrue(hold06["hard_failure"])
        self.assertIn("F12-01", hold06["register_refs"])
        hold08 = next(m for m in s["mismatches"] if m["case_id"] == "NS-HOLD-08")
        self.assertFalse(hold08["hard_failure"])  # 安全侧拒答, 非硬门
        self.assertIn("F12-02", hold08["register_refs"])
        self.assertIn("冻结基线一致", s["verdict"])
        self.assertEqual(s["external_write_effects"], 0)

    # ---- 04 金丝雀只读消费冻结物 ----
    def test_04_canary_readonly_on_frozen_artifacts(self):
        self.assertEqual(self.shas_before, self.shas_after)
        # 答案键 LF 归一 sha 与第 12 周报告记录一致 (raw 会随行尾搅动漂移)
        with open(os.path.join(PROJECT_ROOT, "week-12",
                               "rag-per-case-results-v1.jsonl"), "rb") as f:
            raw = f.read()
        lf_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(lf_sha, ANSWER_KEY_SHA256_FROZEN)
        self.assertEqual(self.canary_summary["answer_key"]["sha256"], lf_sha)
        # 版本链逐项绑定 (corpus/dataset/retriever/candidate)
        self.assertEqual(self.canary_summary["versions"], {
            "corpus_version": "northstar-corpus-w09-v1",
            "dataset_version": "northstar-eval-w10-v1",
            "retriever_version": "vector-hashed-ngram-v1",
            "candidate_id": "northstar-rag-readonly-v1"})

    # ---- 05 回放终态序列与效果 ----
    def test_05_replay_terminal_sequence_and_effects(self):
        tasks = self.replay["tasks"]
        self.assertEqual(len(tasks), 10)
        expected = [
            ("O-01", "read", "answered", True),
            ("O-02", "read", "abstained", True),
            ("O-03", "read", "escalated", True),
            ("O-04", "write", "action_completed", True),
            ("O-05", "write", "approval_revoked", True),
            ("O-06", "write", "approval_denied", True),
            ("O-07", "write", "action_completed", True),
            ("O-08", "write", None, False),
            ("O-09", "read", "system_failed", False),
            ("O-10", "read", "answered", True),
        ]
        for row, (no, kind, terminal, allowed) in zip(tasks, expected):
            self.assertTrue(row["task_run_id"].endswith(no), row["task_run_id"])
            self.assertEqual(row["kind"], kind)
            self.assertEqual(row["visible_terminal"], terminal)
            self.assertEqual(row["allowed_terminal"], allowed)
        m = self.replay["metrics"]
        self.assertEqual(m["allowed_terminal_rate"],
                         {"numerator": 8, "denominator": 10, "value": 0.8})
        self.assertEqual(m["forbidden_effect_count"], 0)
        self.assertEqual(m["unconfirmed_effects"], 1)  # O-08 效果已发生但未确认
        # 外部效果合计 3 = O-04 (1, 已确认) + O-07 (1, 对账后确认) + O-08 (1, 未确认)
        self.assertEqual(sum(t["external_effects"] for t in tasks), 3)
        # O-08 逐 span: 对账段如实记录未确认
        pending = next(t for t in self.replay["task_traces"]
                       if t.task_run_id.endswith("O-08"))
        reco = next(sp for sp in pending.spans
                    if sp["stage"] == "reconciliation")
        self.assertEqual(reco["result"], "reconciliation_query_unavailable")
        self.assertEqual(reco["safe_refs"]["unconfirmed_effects"], 1)

    # ---- 06 两视图永不合并 ----
    def test_06_two_views_never_merge_and_disagree_on_ns_hold_06(self):
        m = self.replay["metrics"]
        s = self.canary_summary
        # 分母不同且互不引用
        self.assertEqual(m["tasks_total"], 10)
        self.assertEqual(s["cases_total"], 60)
        self.assertIn("永不共享", m["denominator_note"])
        self.assertIn("永不合并", s["denominator_note"])
        self.assertIn("不证明内容", m["content_note"])
        # 活证据: O-10 (NS-HOLD-06) 运行视图 allowed / 考卷视图硬门失败
        hold = next(t for t in self.replay["task_traces"]
                    if t.task_run_id.endswith("O-10"))
        self.assertEqual(hold.outcome, "answered")
        self.assertTrue(hold.terminal[1])  # allowed in runtime view
        link = hold.safe_refs["canary_link"]
        self.assertEqual(link["case_id"], "NS-HOLD-06")
        self.assertEqual(link["expected_state"], "refused")
        self.assertEqual(link["observed_mapped_state"], "answered")
        self.assertIn("F12-01", link["canary_view"])
        canary_row = next(r for r in self.canary_rows
                          if r["case_id"] == "NS-HOLD-06")
        self.assertFalse(canary_row["state_match"])

    # ---- 07 遥测脱敏 ----
    def test_07_telemetry_stream_clean_and_scanner_effective(self):
        spans = self.replay["spans"]
        self.assertEqual(len(spans), 90)  # 10 任务 x 9 阶段
        self.assertEqual(scan_spans_for_violations(spans), [])
        serialized = json.dumps(spans, ensure_ascii=False)
        for marker in ("supervisor_note", "customer_name", "payment_detail",
                       "2026 华东套餐差价计费政策"):
            self.assertNotIn(marker, serialized)
        # 扫描器有效性: 注入污染样本必须被抓到 (只报键名/前缀, 不回显内容)
        poisoned = dict(spans[0], safe_refs={"query": "污染样本", "answer": "x"})
        violations = scan_spans_for_violations([poisoned])
        rules = {(v["rule"], v["key"]) for v in violations}
        self.assertIn(("forbidden_key", "query"), rules)
        self.assertIn(("forbidden_key", "answer"), rules)
        self.assertNotIn("污染样本", json.dumps(violations))

    # ---- 08 工作台 API 端到端 ----
    def test_08_workbench_api_end_to_end(self):
        client = TestClient(app)
        state = client.post("/api/reset").json()
        self.assertFalse(state["has_replay"])
        self.assertFalse(state["has_canary"])
        replay = client.post("/api/replay").json()
        self.assertEqual(len(replay["tasks"]), 10)
        self.assertEqual(replay["metrics"]["tasks_total"], 10)
        canary = client.post("/api/canary").json()
        self.assertEqual(canary["summary"]["state_match_rate"], "58/60")
        task_id = replay["tasks"][3]["task_run_id"]  # O-04
        trace = client.get(f"/api/trace/{task_id}")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(len(trace.json()["spans"]), 9)
        missing = client.get("/api/trace/run-w16-unknown-O-99")
        self.assertEqual(missing.status_code, 404)
        redaction = client.post("/api/redaction-check").json()
        self.assertTrue(redaction["passed"])
        self.assertEqual(redaction["checked_events"], 90)
        slo = client.get("/api/slo").json()
        self.assertEqual(len(slo["slos"]), 5)
        state = client.post("/api/reset").json()
        self.assertFalse(state["has_replay"])
        self.assertFalse(state["has_canary"])

    # ---- 09 SLO 草案诚实性 ----
    def test_09_slo_proposal_v0_honesty(self):
        p = SLO_PROPOSAL_V0
        self.assertIn("draft", p["status"])
        self.assertIn("非达标声明", p["status"])
        self.assertIn("禁止用合成回放/考卷结果代填", p["definition_of_u"])
        for slo in p["slos"]:
            if slo["id"] == "SLO-2":
                continue  # 恒等硬门 (合同), 非 [U]
            self.assertIn("[U]", slo["target"], slo["id"])
            self.assertIn("[U]", slo["window"], slo["id"])
        self.assertEqual(p["slos"][1]["target"], "= 0 (合同硬门, 恒等约束非 [U]; 第 14/15 周夹具已证)")
        # 无编造达标数字 (无百分比目标; 允许 [U]/0 与说明文本)
        for slo in p["slos"]:
            self.assertNotRegex(slo["target"], r"^\d+(\.\d+)?%$")
        self.assertIn("永不合并", p["view_note"])

    # ---- 10 工具允许列表不变 ----
    def test_10_allowlist_unchanged_effects_only_via_ticket_create(self):
        self.assertEqual(ALLOWED_TOOLS, (
            C.TOOL_NAME, "ticket.preview_create", "ticket.create", "ticket.get"))
        for task in self.replay["tasks"]:
            if task["visible_terminal"] in ("approval_denied",
                                            "approval_revoked",
                                            "system_failed"):
                self.assertEqual(task["external_effects"], 0, task["task_run_id"])

    # ---- 11 样例工件门 ----
    def test_11_span_sample_artifact_redacted(self):
        self.assertTrue(os.path.exists(SPAN_SAMPLE_PATH),
                        "样例工件缺失: 先跑 python -m mcptool.run_telemetry_replay --out")
        with open(SPAN_SAMPLE_PATH, "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rows), 90)
        self.assertEqual(scan_spans_for_violations(rows), [])

    # ---- 12 归约器语义 (合同违规必须被 SLI 抓住) ----
    def test_12_reducer_flags_forbidden_effects_and_keeps_pending_out(self):
        denied = TaskTrace("run-t-denied", kind="write", clock=lambda: "T")
        denied.span(STAGES[0], result="write_request")
        denied.span(STAGES[-1], result="error_presented", error_class="x")
        denied.finish("approval_denied", effects=1)  # 拒绝路径出现效果 = 违规
        done = TaskTrace("run-t-done", kind="write", clock=lambda: "T")
        done.span(STAGES[0], result="write_request")
        done.finish("action_completed", effects=1)   # 唯一允许效果路径
        pending = TaskTrace("run-t-pending", kind="write", clock=lambda: "T")
        pending.span(STAGES[0], result="write_request")
        pending.finish("reconciliation_pending", effects=1)  # 未确认, 非终态
        m = reduce_runtime_metrics([denied, done, pending])
        self.assertEqual(m["tasks_total"], 3)
        # approval_denied 是允许终态 -> 计入分子; 但其效果增量同时被抓进禁止计数
        # (两个指标相互独立: 过程健康 ≠ 合同未违规)
        self.assertEqual(m["allowed_terminal_rate"],
                         {"numerator": 2, "denominator": 3, "value": 0.6667})
        self.assertEqual(m["forbidden_effect_count"], 1)   # 违规被抓住
        self.assertEqual(m["unconfirmed_effects"], 1)      # 未确认单独口径
        self.assertEqual(m["terminal_distribution"]["action_completed"], 1)
        self.assertEqual(m["non_terminal_distribution"],
                         {"reconciliation_pending": 1})


if __name__ == "__main__":
    unittest.main()






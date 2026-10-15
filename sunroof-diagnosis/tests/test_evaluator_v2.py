"""
评测器 v2 测试 (阶段 E 批次 1) —— 回链 specs/phase-e-brief-draft-v1.md §E1 验收口径

  01 修正语义①专属触发即终局: 上级 NS-HOLD-06 型等价构造 (触发判否后不得向后扫描兜底)
  02 修正语义②空真修正: fact 无版本锚点不触发版本规则; 数字锚点剥离版本号
  03 修正语义③判定矩阵留痕: 多触发事实记录全部触发规则, 终局 = 首个可判定规则
  04 unmapped 不折叠成 match (对齐遥测 non_terminal 诚实语义)
  05 状态门 v2 真判定: 60 例 -> 58/60, miss = {SR-HOLD-07, SR-HOLD-12} (预注册映射)
  06 v1 缺陷登记: 恒真差异逐例登记; v1 结果文件零改动 (内容 hash 前后一致)
  07 事实层诚实性: 67/81 hit 冻结; DEV-11 数字锚点 miss 如实; HOLD-06 拒答得体被
     正确奖励 (真实判定, 非虚判); forbidden 0 违规
  08 确定性守卫: 重算逐字节一致; --write 对已存在产物只读对账 (不重写)
  09 输出结构: 60 行逐例 + 逐条 fact 判定矩阵字段完备
  10/11 forbidden 两分法 (doc_guard 抓违规 / structural 如实豁免)

运行: python -m unittest tests.test_evaluator_v2 -v
"""
import contextlib
import hashlib
import io
import unittest

from evaluation.evaluator_v2 import (  # noqa: E402
    FACTCHECK_V2_PATH,
    V1_RESULTS_PATH,
    _render,
    check_forbidden_fact_v2,
    check_required_fact_v2,
    evaluate_factcheck,
    main,
    map_state,
    rule_triggers,
)


def _answered_like(answer_text: str, citation_supported: bool = True) -> dict:
    """构造与 v1 落盘行同形的 record (上级 NS-HOLD-06 型病灶的等价构造底座)。"""
    return {
        "state": "answered",
        "answer_text": answer_text,
        "user_next_step": "按引用口径答复并在工单登记引用来源",
        "citations": [{"document_id": "torque-east-v5", "chunk_id": "torque-east-v5#s03",
                       "source_version": "5", "effective_status": "accepted"}],
        "restricted_candidate_count": 0,
        "citation_supported": citation_supported,
    }


class TestEvaluatorV2Corrections(unittest.TestCase):
    """三条预注册修正语义的单元级验证。"""

    def test_01_exclusive_trigger_is_terminal(self):
        # 上级病灶等价构造: 自信作答 + 语料外事实「明确说明无现行来源支撑该定损流程」。
        # 上级评测器: no_current_source 规则触发但未通过 -> 继续向后扫描 -> 兜底虚 hit。
        # v2: 触发即终局 (判否 = miss), 绝不因 substance_referral 可判 True 而兜底。
        rec = _answered_like("该定损流程按保修凭证条款执行 (自信作答于无来源域)")
        out = check_required_fact_v2("明确说明无现行来源支撑该定损流程", rec)
        self.assertEqual(out["rule_id"], "no_current_source")
        self.assertIs(out["hit"], False)
        # 矩阵留痕: 兜底规则 (substance_referral) 的触发与判定被记录, 但未成为终局
        self.assertIn("substance_referral", out["all_triggers"])
        self.assertIs(out["all_triggers"]["no_current_source"], False)

    def test_02_empty_truth_correction(self):
        # 无版本锚点的事实: 版本规则触发但不产生判定 (上级 _r_version_source
        # all([])=True 空真在 v2 被修正为 None —— 触发≠命中, 留痕如实记录)。
        rec = _answered_like("历史工单按当时口径追溯, 不作当前解释依据")
        out = check_required_fact_v2("历史工单按当时口径追溯但不作当前解释依据", rec)
        self.assertIn("version_attribution", out["all_triggers"])
        self.assertIsNone(out["all_triggers"]["version_attribution"])  # 空真 -> 不判定
        self.assertNotEqual(out["rule_id"], "version_attribution")     # 不成为终局
        # 数字锚点剥离版本号: v3 的 3 不是数量锚点 (不得触发 numeric_anchor)
        self.assertFalse(bool(rule_triggers("指出现行口径为 v3 首响步骤")["numeric_anchor"]))

    def test_03_judgment_matrix_trace(self):
        # 多触发事实: 全部触发规则留痕, 终局 = 首个可判定规则 (预注册顺序)。
        rec = {"state": "abstained", "answer_text": "",
               "user_next_step": "同批文件口径不一致已登记冲突, 暂停对客答复, 转质量主管等待所有者裁决",
               "citations": [], "restricted_candidate_count": 0, "citation_supported": None}
        out = check_required_fact_v2("登记冲突并暂停对客答复", rec)
        self.assertEqual(out["rule_id"], "conflict_register")
        self.assertIs(out["hit"], True)
        self.assertIn("record_keeping", out["all_triggers"])   # 后续触发留痕
        self.assertGreaterEqual(len(out["all_triggers"]), 2)

    def test_04_unmapped_never_folds_into_match(self):
        # 未登记组合如实返回 unmapped (对齐遥测 non_terminal 诚实语义)。
        self.assertEqual(map_state("abstained", "mystery_reason"), "unmapped")
        self.assertEqual(map_state("quantum_state", None), "unmapped")


class TestEvaluatorV2Reconciliation(unittest.TestCase):
    """对 60 例落盘候选的全量对账 (零候选重跑, 纯函数)。"""

    @classmethod
    def setUpClass(cls):
        with open(V1_RESULTS_PATH, "rb") as f:
            cls.v1_sha_before = hashlib.sha256(f.read()).hexdigest()
        cls.result = evaluate_factcheck()
        cls.by_case = {r["case_id"]: r for r in cls.result["rows"]}

    def test_05_state_gate_v2_true_judgment(self):
        summary = self.result["summary"]
        self.assertEqual(summary["state_gate_v2"], "58/60")
        miss_ids = {m["case_id"] for m in summary["state_miss_cases_v2"]}
        self.assertEqual(miss_ids, {"SR-HOLD-07", "SR-HOLD-12"})

    def test_06_v1_defect_registered_and_v1_untouched(self):
        summary = self.result["summary"]
        # 恒真差异逐例登记 (v1 match=True 恒真 vs v2 真判定)
        disc_ids = {d["case_id"] for d in summary["v1_v2_state_discrepancies"]}
        self.assertEqual(disc_ids, {"SR-HOLD-07", "SR-HOLD-12"})
        self.assertIn("自比较恒真", summary["v1_judgment_defect_register"])
        self.assertIn("NS-HOLD-06", summary["upstream_fallthrough_register"])
        # v1 结果文件零改动 (评测器是纯函数)
        with open(V1_RESULTS_PATH, "rb") as f:
            v1_sha_after = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(self.v1_sha_before, v1_sha_after)

    def test_07_fact_layer_honest_presentation(self):
        fact = self.result["summary"]["fact_layer"]
        self.assertEqual((fact["required_facts_total"],
                          fact["required_facts_hit"],
                          fact["required_facts_miss"]), (81, 67, 14))
        self.assertEqual(fact["forbidden_checks_violations"], 0)
        # DEV-11: 合并登记口径未覆盖 -> 数字锚点如实 miss (v1 从未呈现的缺口)
        dev11 = [c for r in self.result["rows"] if r["case_id"] == "SR-DEV-11"
                 for c in r["required_fact_checks"]]
        self.assertEqual(len(dev11), 1)
        self.assertIs(dev11[0]["hit"], False)
        self.assertEqual(dev11[0]["rule_id"], "numeric_anchor")
        # HOLD-06: 拒答得体被「真实判定」奖励 (no_current_source 真命中, 非兜底虚判)
        hold06 = [c for r in self.result["rows"] if r["case_id"] == "SR-HOLD-06"
                  for c in r["required_fact_checks"]]
        self.assertTrue(hold06)
        self.assertTrue(all(c["hit"] for c in hold06))
        self.assertTrue(all(c["rule_id"] != "substance_referral" for c in hold06))

    def test_08_deterministic_and_write_guard(self):
        first = _render(evaluate_factcheck())
        second = _render(evaluate_factcheck())
        self.assertEqual(first, second)  # 纯函数确定性: 重算逐字节一致
        # --write 对已存在产物只读对账 (不重写): 捕获 stdout 断言
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--write"])
        self.assertEqual(code, 0)
        self.assertIn("逐字节一致", buf.getvalue())
        with open(FACTCHECK_V2_PATH, "r", encoding="utf-8") as f:
            on_disk = f.read().replace("\r\n", "\n")  # 行尾归一 (CRLF 存储层细节)
        self.assertEqual(on_disk, first)

    def test_09_output_structure_complete(self):
        rows = self.result["rows"]
        self.assertEqual(len(rows), 60)
        for row in rows:
            self.assertIn("state_match_v2", row)
            self.assertIn("state_match_v1_recorded", row)
            for check in row["required_fact_checks"]:
                self.assertIn(check["rule_id"], check["all_triggers"])
                self.assertIn("hit", check)
                self.assertIn("basis", check)
            for check in row["forbidden_fact_checks"]:
                self.assertIn(check["mode"], ("doc_guard", "structural"))
                self.assertIn("ok", check)


class TestForbiddenFactV2(unittest.TestCase):
    """forbidden 两分法 (doc_guard / structural)。"""

    def test_10_doc_guard_catches_violation(self):
        rec = _answered_like("按 retrofit-notice-v4 口径单边答复")
        out = check_forbidden_fact_v2("单边引用 retrofit-notice-v4 口径", rec)
        self.assertEqual(out["mode"], "doc_guard")
        self.assertIs(out["ok"], False)  # 答案提及禁引文档 -> 抓住
        clean = check_forbidden_fact_v2("单边引用 retrofit-notice-v4 口径",
                                        _answered_like("按现行口径答复"))
        self.assertIs(clean["ok"], True)

    def test_11_structural_guarantee(self):
        abstained = {"state": "abstained", "answer_text": "", "citations": [],
                     "restricted_candidate_count": 0}
        self.assertIs(check_forbidden_fact_v2("编造金额", abstained)["ok"], True)


if __name__ == "__main__":
    unittest.main()


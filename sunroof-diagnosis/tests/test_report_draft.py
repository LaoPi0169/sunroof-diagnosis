"""
阶段 C 节点 ⑳ 根因分析报告草稿只读聚合 MCP 工具 —— 自动化集成测试

回链 api-contract/report-draft-v1.json + mcptool/report_contract.py
+ mcptool/report_aggregate.py + mcptool/report_server.py + application/report.py
+ application/main.py 节点 ⑳ 追加端点 (discovery-brief-v1.md In-Scope 第 3 条的
阶段 C 兑现 —— 结构化、带出处引用的《天窗总成异常根因分析报告草稿》):
  01 合法全链路: 三源聚合 findings/citations/conclusion + 零效果
  02 偏差 100% 可复算: +1.26% conforming / +15.58% 超硬限 needs_review (阶段 A 同值原因码)
  03 P-1 零存在性泄漏: 跨项目 SN/件号、未知 SN/件号 四路封套逐字节一致
  04 主管跨项目只读: OEM-B 全链路 + rail 偏差可复算
  05 角色默认拒绝 (ops ✗)
  06 身份参数走私 + 可信上下文形状不符: 一律 invalid_argument
  07 参数封闭/语法冻结: sn/part_no/process_step 非法与未知键全拒
  08 P-2 贯通: 无现行 SOP -> gap + needs_review/NO_CURRENT_SOP
  09 P-3/R-2 贯通: 无对接标准 gap / 波形绝不嵌入 (OMITTED gap, 响应零点列)
  10 只读证明: 三源 store 指纹前后逐字节一致 + nested 审计三源分型 + 零效果
  11 合同互锁: 代码常量与 api-contract/report-draft-v1.json 同值
  12 确定性: 同输入两次调用 result 逐字节一致 (零随机零时钟零 LLM)
  13 HTTP 面 + 控制台第七面板: 身份矩阵 / 401 不落审计 / 422 封闭契约 / 零令牌

运行: python -m unittest tests.test_report_draft -v
"""
import json
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application import report as report_gateway  # noqa: E402
from application.main import app  # noqa: E402
from mcptool import mes_store  # noqa: E402
from mcptool import plm_store  # noqa: E402
from mcptool import qms_store  # noqa: E402
from mcptool import report_contract as M  # noqa: E402
from mcptool import report_server  # noqa: E402

PQE_A = {"tenant": "sunroof-training", "user": "pqe_chen_oema", "role": "pqe",
         "allowed_projects": ["OEM-A"]}
SUPERVISOR = {"tenant": "sunroof-training", "user": "supervisor_li",
              "role": "quality_supervisor", "allowed_projects": ["*"]}
OPS = {"tenant": "sunroof-training", "user": "ops_wang", "role": "ops",
       "allowed_projects": []}

ARGS_A = {"sn": "ROOF-10001", "part_no": "SR-FRAME-A01",
          "process_step": "torque_tightening"}


def call(ctx, args, sink=None, run_id="run-test"):
    """工具服务端直调 (HTTP 之下的那一层)。"""
    return report_server.draft_root_cause(ctx, args, audit_sink=sink,
                                          run_id=run_id)


def report_of(resp):
    return resp["result"]["report"]


def findings_of(resp, topic):
    return [f for f in report_of(resp)["findings"] if f["topic"] == topic]


class ReportToolCoreTests(unittest.TestCase):
    """mcptool 层: 聚合确定性 / 偏差可复算 / P-1/P-2/P-3 / R-2 / 只读性质。"""

    def test_01_valid_full_chain(self):
        sink = []
        resp = call(PQE_A, dict(ARGS_A), sink)
        self.assertTrue(resp["ok"])
        rep = report_of(resp)
        self.assertEqual(rep["case"], {"sn": "ROOF-10001",
                                       "part_no": "SR-FRAME-A01",
                                       "process_step": "torque_tightening",
                                       "oem_project": "OEM-A"})
        # 结论: draft_ready (依据充分可出具草稿, 非合格判定)
        self.assertEqual(rep["conclusion"]["status"], "draft_ready")
        # findings: 扭矩偏差 + ECU + 现行 SOP + 检验要点 + BOM + ECO
        topics = {f["topic"] for f in rep["findings"]}
        self.assertEqual(topics, {"assembly_torque_deviation", "ecu_flash",
                                  "current_sop", "inspection_checklist",
                                  "bom_revision", "engineering_change"})
        # citations 覆盖三源 (MES/QMS/PLM)
        joined = json.dumps(rep["citations"], ensure_ascii=False)
        for frag in ("MES:ROOF-10001", "QMS:QS-SR-FRM-TQ-01@v2",
                     "QMS:QMS-SOP-SR-04@v5", "PLM:SR-FRAME-A01#bom_snapshot",
                     "PLM:SR-FRAME-A01#eco_history"):
            self.assertIn(frag, joined)
        # 免责声明静态存在
        self.assertEqual(rep["disclaimer"], M.DISCLAIMER)
        # 只读零效果 (主审计 + 三源 nested 审计全部零效果) + 主审计 ok
        self.assertEqual([e["external_effects"] for e in sink],
                         [0] * len(sink))
        self.assertGreaterEqual(len(sink), 4)  # 主条目 + mes/qms/plm nested
        main = [e for e in sink if e["tool"] == M.TOOL_NAME]
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0]["reason_typed"], "ok")

    def test_02_deviation_recomputable(self):
        # ROOF-10001 frame_bolt: 9.62 vs 9.5 -> +0.12 N·m -> +1.26% -> 公差带内 conforming
        resp = call(PQE_A, dict(ARGS_A))
        f = findings_of(resp, "assembly_torque_deviation")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["step"], "frame_bolt_tightening")
        self.assertEqual(f[0]["measured_nm"], 9.62)
        self.assertEqual(f[0]["target_nm"], 9.5)
        self.assertEqual(f[0]["deviation_nm"], 0.12)
        self.assertEqual(f[0]["deviation_pct"], 1.26)
        self.assertEqual(f[0]["conformance"], "conforming")
        self.assertEqual(f[0]["formula_rule"], "TORQUE_DEVIATION_PCT")
        self.assertIn("QMS-SOP-SR-04-v5#Section-3.2", f[0]["citation"])
        # ROOF-10003 frame_bolt: 10.98 vs 9.5 -> +1.48 N·m -> +15.58% ->
        # 超公差带 nonconforming 且超 15% 硬限 -> needs_review (阶段 A 同值原因码)
        resp2 = call(PQE_A, {"sn": "ROOF-10003", "part_no": "SR-FRAME-A01",
                             "process_step": "torque_tightening"})
        f2 = findings_of(resp2, "assembly_torque_deviation")
        self.assertEqual(f2[0]["deviation_nm"], 1.48)
        self.assertEqual(f2[0]["deviation_pct"], 15.58)
        self.assertEqual(f2[0]["conformance"], "nonconforming")
        self.assertEqual(report_of(resp2)["conclusion"]["status"],
                         M.CONCLUSION_NEEDS_REVIEW)
        self.assertEqual(report_of(resp2)["conclusion"]["reason_code"],
                         M.REASON_HARD_LIMIT)

    def test_03_p1_zero_existence_leak(self):
        # 四路失败: 跨项目 SN / 跨项目件号 / 未知 SN / 未知件号 -> 封套逐字节一致
        cross_sn = call(PQE_A, {"sn": "ROOF-20001", "part_no": "SR-FRAME-A01",
                                "process_step": "torque_tightening"})
        cross_part = call(PQE_A, {"sn": "ROOF-10001", "part_no": "SR-RAIL-B01",
                                  "process_step": "torque_tightening"})
        unknown_sn = call(PQE_A, {"sn": "ROOF-99999", "part_no": "SR-FRAME-A01",
                                  "process_step": "torque_tightening"})
        unknown_part = call(PQE_A, {"sn": "ROOF-10001", "part_no": "SR-DUMMY-A99",
                                    "process_step": "torque_tightening"})
        for resp in (cross_sn, cross_part, unknown_sn, unknown_part):
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"]["code"], "not_available")
        blobs = [json.dumps(r, sort_keys=True) for r in
                 (cross_sn, cross_part, unknown_sn, unknown_part)]
        self.assertEqual(len(set(blobs)), 1, "四路失败封套必须逐字节一致")
        self.assertNotIn("OEM-B", blobs[0])

    def test_04_supervisor_cross_project_readonly(self):
        # 主管跨项目: OEM-B SN + OEM-B 件号全链路; rail 偏差可复算
        resp = call(SUPERVISOR, {"sn": "ROOF-20002", "part_no": "SR-RAIL-B01",
                                 "process_step": "torque_tightening"})
        self.assertTrue(resp["ok"])
        rep = report_of(resp)
        self.assertEqual(rep["case"]["oem_project"], "OEM-B")
        f = findings_of(resp, "assembly_torque_deviation")
        self.assertEqual(f[0]["measured_nm"], 6.03)
        self.assertEqual(f[0]["target_nm"], 6.0)
        self.assertEqual(f[0]["deviation_pct"], 0.5)
        self.assertEqual(f[0]["conformance"], "conforming")
        self.assertEqual(rep["conclusion"]["status"], "draft_ready")

    def test_05_role_default_deny(self):
        denied = call(OPS, dict(ARGS_A))
        unknown = call(OPS, {"sn": "ROOF-99999", "part_no": "SR-DUMMY-A99",
                             "process_step": "gap_check"})
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "not_available")
        self.assertEqual(json.dumps(denied, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        sink = []
        call(OPS, dict(ARGS_A), sink)
        self.assertEqual(sink[0]["reason_typed"], "role_denied")

    def test_06_identity_smuggling_and_context_shape(self):
        # 可信会话键走私进调用参数 -> invalid_argument
        for key in M.TRUSTED_CONTEXT_KEYS:
            resp = call(PQE_A, {**ARGS_A, key: "x"})
            self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 枚举外未知键
        resp = call(PQE_A, {**ARGS_A, "extra_key": 1})
        self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 可信上下文形状不符 (缺失/多余) -> invalid_argument
        bad_ctx = dict(PQE_A)
        bad_ctx.pop("role")
        self.assertEqual(call(bad_ctx, dict(ARGS_A))["error"]["code"],
                         "invalid_argument")
        bad_ctx2 = dict(PQE_A)
        bad_ctx2["extra"] = 1
        self.assertEqual(call(bad_ctx2, dict(ARGS_A))["error"]["code"],
                         "invalid_argument")

    def test_07_closed_arguments_and_syntax(self):
        # 三键缺一 / 语法非法 / 类型错 全部 invalid_argument
        for args in ({"sn": "ROOF-10001", "part_no": "SR-FRAME-A01"},
                     {"part_no": "SR-FRAME-A01", "process_step": "gap_check"},
                     {"sn": "ROOF-10001", "process_step": "gap_check"},
                     {**ARGS_A, "sn": "roof-10001"},
                     {**ARGS_A, "sn": "ROOF-1001"},
                     {**ARGS_A, "sn": 10001},
                     {**ARGS_A, "part_no": "sr-frame-a01"},
                     {**ARGS_A, "part_no": "SR-FRAME-C01"},
                     {**ARGS_A, "process_step": "Torque_Tightening"},
                     {**ARGS_A, "process_step": "welding"},
                     {**ARGS_A, "fields": ["assembly_torque"]},
                     {**ARGS_A, "segment": 0}):
            resp = call(PQE_A, args)
            self.assertEqual(resp["error"]["code"], "invalid_argument",
                             f"args={args!r} 应拒绝")

    def test_08_p2_no_current_sop_passthrough(self):
        # ROOF-10001 + SR-MOTOR-A02: QMS 仅存被替代行 -> gap + 结论转人工
        resp = call(PQE_A, {"sn": "ROOF-10001", "part_no": "SR-MOTOR-A02",
                            "process_step": "torque_tightening"})
        self.assertTrue(resp["ok"])
        rep = report_of(resp)
        gap_topics = {g["topic"]: g for g in rep["gaps"]}
        self.assertEqual(gap_topics["sop_current"]["reason_code"],
                         M.NO_CURRENT_SOP)
        self.assertEqual(rep["conclusion"]["status"], M.CONCLUSION_NEEDS_REVIEW)
        self.assertEqual(rep["conclusion"]["reason_code"], M.NO_CURRENT_SOP)
        # 被替代 SOP 标识零出现 (QMS-SOP-SR-11 v2 与待发布 v3 都不出)
        blob = json.dumps(resp, ensure_ascii=False)
        self.assertNotIn("QMS-SOP-SR-11", blob)
        self.assertNotIn("v3", blob)
        # 无现行标准可对账 -> 拧紧记录逐条结构化 gap, 绝不编造偏差
        step_gaps = [g for g in rep["gaps"]
                     if g["topic"] == "assembly_torque_step"]
        self.assertEqual({g["step"] for g in step_gaps},
                         {"frame_bolt_tightening", "rail_bolt_tightening"})
        self.assertEqual(findings_of(resp, "assembly_torque_deviation"), [])

    def test_09_p3_and_r2_gaps(self):
        # rail_bolt 无现行质量标准对接 -> NO_STANDARD_FOR_STEP (绝不脑补)
        resp = call(PQE_A, dict(ARGS_A))
        step_gaps = [g for g in report_of(resp)["gaps"]
                     if g["topic"] == "assembly_torque_step"]
        self.assertEqual(step_gaps, [{"topic": "assembly_torque_step",
                                      "step": "rail_bolt_tightening",
                                      "reason_code": M.NO_STANDARD_FOR_STEP}])
        # 波形可用但绝不嵌入 (R-2 载料纪律): OMITTED gap + 响应零波形载荷
        self.assertIsNotNone(mes_store.waveform_points("ROOF-10001"))
        wgap = [g for g in report_of(resp)["gaps"]
                if g["topic"] == "sensor_waveform"]
        self.assertEqual(wgap[0]["reason_code"], M.WAVEFORM_OMITTED_REASON)
        blob = json.dumps(resp, ensure_ascii=False)
        self.assertNotIn("points", blob)
        # 波形载荷元数据键 (通道/采样率/生成器) 零出现 —— 只有结构化 gap 登记
        for banned in ("sample_rate_hz", "vib_sensor_ch1",
                       "mes-waveform-int-v1"):
            self.assertNotIn(banned, blob)
        # ROOF-10003 波形通道缺失 -> 结构化 FIELD_NOT_AVAILABLE (不编造)
        resp2 = call(PQE_A, {"sn": "ROOF-10003", "part_no": "SR-FRAME-A01",
                             "process_step": "torque_tightening"})
        wgap2 = [g for g in report_of(resp2)["gaps"]
                 if g["topic"] == "sensor_waveform"]
        self.assertEqual(wgap2[0]["reason_code"], M.FIELD_NOT_AVAILABLE)

    def test_10_readonly_fingerprints_and_nested_audit(self):
        """只读性质: 三源 store 指纹前后逐字节一致; nested 审计三源分型。"""
        fps = (mes_store.store_fingerprint(), qms_store.store_fingerprint(),
               plm_store.store_fingerprint())
        sink = []
        call(PQE_A, dict(ARGS_A), sink, "run-ok")
        call(PQE_A, {"sn": "ROOF-20001", "part_no": "SR-FRAME-A01",
                     "process_step": "torque_tightening"}, sink, "run-cross")
        call(PQE_A, {"sn": "ROOF-99999", "part_no": "SR-FRAME-A01",
                     "process_step": "torque_tightening"}, sink, "run-unknown")
        call(SUPERVISOR, {"sn": "ROOF-20001", "part_no": "SR-RAIL-B01",
                          "process_step": "torque_tightening"}, sink,
             "run-sup")
        call(PQE_A, {"sn": "ROOF-10001", "part_no": "SR-MOTOR-A02",
                     "process_step": "torque_tightening"}, sink, "run-p2")
        self.assertEqual((mes_store.store_fingerprint(),
                          qms_store.store_fingerprint(),
                          plm_store.store_fingerprint()), fps,
                         "只读性质被破坏: 三源 store 指纹变化")
        # 全部条目零效果; 主审计分型对账
        self.assertEqual([e["external_effects"] for e in sink if
                          e["tool"] == M.TOOL_NAME and e["run_id"] in
                          ("run-ok", "run-cross", "run-unknown", "run-sup",
                           "run-p2")],
                         [0, 0, 0, 0, 0])
        main_reasons = [e["reason_typed"] for e in sink
                        if e["run_id"] in ("run-ok", "run-cross", "run-unknown",
                                           "run-sup", "run-p2")
                        and not e["run_id"].endswith((":mes", ":qms", ":plm"))]
        self.assertEqual(main_reasons,
                         ["ok", "source_unavailable:mes",
                          "source_unavailable:mes", "ok", "ok"])
        # nested 审计覆盖三源工具 (mes/qms/plm 各有明细条目)
        tools = {e["tool"] for e in sink}
        self.assertTrue(any(t.startswith("mes.") for t in tools))
        self.assertTrue(any(t.startswith("qms.") for t in tools))
        self.assertTrue(any(t.startswith("plm.") for t in tools))

    def test_11_contract_json_interlock(self):
        """代码常量与 api-contract/report-draft-v1.json 同值互锁。"""
        with open(os.path.join(PROJECT_ROOT, "api-contract",
                               "report-draft-v1.json"),
                  "r", encoding="utf-8") as f:
            contract = json.load(f)
        binding = contract["tool_binding"]
        self.assertEqual(binding["tool_name"], M.TOOL_NAME)
        self.assertEqual(binding["version"], M.TOOL_VERSION)
        self.assertEqual(binding["effect"], M.EFFECT)
        self.assertEqual(binding["sn_pattern"], M.SN_PATTERN)
        self.assertEqual(binding["part_no_pattern"], M.PART_NO_PATTERN)
        self.assertEqual(binding["process_step_enum"],
                         list(M.PROCESS_STEP_ENUM))
        self.assertEqual(binding["hard_limit_pct"], M.HARD_LIMIT_PCT)
        self.assertEqual(binding["hard_limit_reason_code"], M.REASON_HARD_LIMIT)
        self.assertEqual(binding["conclusion_enum"],
                         [M.CONCLUSION_DRAFT_READY, M.CONCLUSION_NEEDS_REVIEW])
        self.assertIn(M.WAVEFORM_OMITTED_REASON,
                      binding["gap_reason_codes"])
        self.assertIn(M.NO_CURRENT_SOP, binding["gap_reason_codes"])
        self.assertEqual(sorted(binding["error_taxonomy"]),
                         sorted(M.ERROR_CODES))
        self.assertEqual(binding["allowed_roles"], list(M.ALLOWED_ROLES))
        self.assertEqual(binding["aggregates"],
                         ["mes.lookup_production_record@1",
                          "qms.lookup_inspection_standard@1",
                          "plm.lookup_engineering_record@1"])
        face = contract["http_face"]
        self.assertEqual(face["properties"]["sn"]["pattern"], M.SN_PATTERN)
        self.assertEqual(face["properties"]["part_no"]["pattern"],
                         M.PART_NO_PATTERN)
        self.assertEqual(face["properties"]["process_step"]["enum"],
                         list(M.PROCESS_STEP_ENUM))

    def test_12_determinism(self):
        """同输入两次调用 result 逐字节一致 (零随机零时钟零 LLM)。"""
        a = call(PQE_A, dict(ARGS_A))
        b = call(PQE_A, dict(ARGS_A))
        self.assertEqual(json.dumps(a["result"], sort_keys=True),
                         json.dumps(b["result"], sort_keys=True))
        c = call(SUPERVISOR, {"sn": "ROOF-20002", "part_no": "SR-RAIL-B01",
                              "process_step": "torque_tightening"})
        d = call(SUPERVISOR, {"sn": "ROOF-20002", "part_no": "SR-RAIL-B01",
                              "process_step": "torque_tightening"})
        self.assertEqual(json.dumps(c["result"], sort_keys=True),
                         json.dumps(d["result"], sort_keys=True))


class ReportHttpFaceTests(unittest.TestCase):
    """application/main.py 节点 ⑳ 追加端点 + 控制台第七面板 + 身份矩阵。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-rep-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_report_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每用例独立初始化, 保证确定性
        self.client = TestClient(app)
        report_gateway.reset_audit()

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)
        report_gateway.reset_audit()

    def _post(self, sn="ROOF-10001", part_no="SR-FRAME-A01",
              step="torque_tightening", token=None, **extra):
        headers = {"X-Auth-Token": token} if token else {}
        body = {"sn": sn, "part_no": part_no, "process_step": step, **extra}
        return self.client.post("/api/v1/report/draft", json=body,
                                headers=headers)

    def test_13_http_face_and_console_panel(self):
        # 默认 PQE 培训身份 (免令牌, OEM-A): ok + 只读零效果 + 审计关联键
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["business_effects"], 0)
        self.assertEqual(body["effect"], "read_only")
        self.assertEqual(body["tool"], M.TOOL_NAME)
        self.assertTrue(body["run_id"].startswith("rep-"))
        rep = body["result"]["report"]
        self.assertEqual(rep["conclusion"]["status"], "draft_ready")
        # 显式令牌身份矩阵: OEM-B PQE / 主管跨项目
        self.assertTrue(self._post(sn="ROOF-20001", part_no="SR-RAIL-B01",
                                   token="token_pqe_oem_b").json()["ok"])
        self.assertTrue(self._post(sn="ROOF-20001", part_no="SR-RAIL-B01",
                                   token="token_qa_supervisor_secret")
                        .json()["ok"])
        # 跨项目: 200 + ok=false 结构化拒绝, 受限审计主条目落 source_unavailable
        audit_before = len([e for e in report_gateway.AUDIT
                            if not e["run_id"].endswith((":mes", ":qms", ":plm"))])
        resp = self._post(sn="ROOF-20001")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        main_audits = [e for e in report_gateway.AUDIT
                       if not e["run_id"].endswith((":mes", ":qms", ":plm"))]
        self.assertEqual(len(main_audits), audit_before + 1)
        self.assertEqual(main_audits[-1]["reason_typed"],
                         "source_unavailable:mes")
        # 未知令牌 401: 查询不执行, 不落受限审计 (主条目与 nested 均零新增)
        audit_all_before = len(report_gateway.AUDIT)
        resp = self._post(token="token_invalid_x")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(report_gateway.AUDIT), audit_all_before)
        # 封闭契约: 自报身份 / 未知字段 / 缺键 / 语法错 -> 422
        self.assertEqual(self._post(role="quality_supervisor").status_code, 422)
        self.assertEqual(self._post(extra=1).status_code, 422)
        resp = self.client.post("/api/v1/report/draft",
                                json={"sn": "ROOF-10001"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._post(sn="ROOF-1").status_code, 422)
        self.assertEqual(self._post(part_no="SR-FRAME-C01").status_code, 422)
        # 波形绝不嵌入 (R-2): 成功响应零波形点列
        ok_body = self._post().json()
        self.assertNotIn("points", json.dumps(ok_body, ensure_ascii=False))
        # 控制台第七面板可达且零令牌字面量
        page = self.client.get("/ui")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn('data-tab="report"', html)
        self.assertIn("/api/v1/report/draft", html)
        for secret in ("token_pqe_oem_a", "token_pqe_oem_b",
                       "token_qa_supervisor_secret"):
            self.assertNotIn(secret, html, "控制台页面不得内嵌令牌")


if __name__ == "__main__":
    unittest.main()
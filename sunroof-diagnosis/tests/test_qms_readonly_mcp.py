"""
阶段 C 节点 ⑲ QMS 检验标准只读 MCP 工具 —— 自动化集成测试

回链 api-contract/qms-lookup-v1.json + mcptool/qms_contract.py + mcptool/qms_store.py
+ mcptool/qms_server.py + application/qms.py + application/main.py 节点 ⑲ 追加端点
(discovery-brief-v1.md In-Scope 第 2 条 QMS 半边的阶段 C 兑现):
  01 合法 PQE 三字段: 现行 SOP/质量标准/检验要点 + 零效果
  02 P-2 现行过滤: 被替代版本标识零出现 (store 行存在但取数层排除)
  03 P-1 零存在性泄漏: 跨项目件号与未知件号用户面封套逐字节一致
  04 主管跨项目只读 (矩阵 §2 ✓ 只读)
  05 角色默认拒绝 (ops ✗): 封套与未知件号逐字节一致, 审计 role_denied
  06 身份参数走私 + 可信上下文形状不符: 一律 invalid_argument
  07 字段封闭枚举: 空/未知/超量/重复 fields 全部 invalid_argument
  08 语法冻结: part_no / process_step 非法一律 invalid_argument (不归一化)
  09 P-3 缺失字段结构化: FIELD_NOT_AVAILABLE, 绝不编造数值
  10 P-2 结构化拒引: 仅存被替代行 -> NO_CURRENT_SOP; 工序无行 -> FIELD_NOT_AVAILABLE
  11 只读证明: store 指纹前后逐字节一致 + 外部效果恒 0 + 审计分型对账
  12 合同互锁: 代码常量与 api-contract/qms-lookup-v1.json 同值
  13 确定性 + HTTP 面: 两次调用一致 + 身份矩阵 + 401 不落审计 + 422 封闭契约
      + 控制台第六面板零令牌

运行: python -m unittest tests.test_qms_readonly_mcp -v
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
from application import qms as qms_gateway  # noqa: E402
from application.main import app  # noqa: E402
from mcptool import qms_contract as M  # noqa: E402
from mcptool import qms_server, qms_store  # noqa: E402

PQE_A = {"tenant": "sunroof-training", "user": "pqe_chen_oema", "role": "pqe",
         "allowed_projects": ["OEM-A"]}
SUPERVISOR = {"tenant": "sunroof-training", "user": "supervisor_li",
              "role": "quality_supervisor", "allowed_projects": ["*"]}
OPS = {"tenant": "sunroof-training", "user": "ops_wang", "role": "ops",
       "allowed_projects": []}

ALL_FIELDS = list(M.FIELD_ENUM)


def call(ctx, args, sink=None, run_id="run-test"):
    """工具服务端直调 (HTTP 之下的那一层)。"""
    return qms_server.lookup_inspection_standard(ctx, args, audit_sink=sink,
                                                 run_id=run_id)


class QmsToolCoreTests(unittest.TestCase):
    """mcptool 层: 合同封闭性 / ACL / P-1 / P-2 / P-3 / 只读性质。"""

    def test_01_valid_pqe_three_fields(self):
        sink = []
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                            "process_step": "torque_tightening",
                            "fields": ALL_FIELDS}, sink)
        self.assertTrue(resp["ok"])
        result = resp["result"]
        self.assertEqual(result["part_no"], "SR-FRAME-A01")
        self.assertEqual(result["oem_project"], "OEM-A")
        self.assertEqual(result["process_step"], "torque_tightening")
        self.assertEqual(result["record_version"], 1)
        fields = result["fields"]
        sop = fields["sop_current"]
        self.assertEqual(sop["status"], "available")
        self.assertEqual(sop["payload"]["sop_id"], "QMS-SOP-SR-04")
        self.assertEqual(sop["payload"]["version"], "v5")
        self.assertEqual(sop["payload"]["exclusion_policy"], M.CURRENT_ONLY_POLICY)
        # 用户面 payload 白名单: 内部治理键 (is_current/superseded_by/supersedes) 不出
        self.assertEqual(
            set(sop["payload"]),
            {"sop_id", "version", "title", "effective_at", "owner_id",
             "process_step", "mes_step", "exclusion_policy"})
        qs = fields["quality_standard"]
        self.assertEqual(qs["status"], "available")
        self.assertEqual(qs["payload"]["spec_id"], "QS-SR-FRM-TQ-01")
        self.assertEqual(qs["payload"]["version"], "v2")
        self.assertEqual(qs["payload"]["target_value"], 9.5)
        self.assertEqual(qs["payload"]["tolerance"], "+0.5/-0.5")
        chk = fields["inspection_checklist"]
        self.assertEqual(chk["status"], "available")
        self.assertEqual(chk["payload"]["count"], 3)
        # 只读零效果 + 审计 ok
        self.assertEqual([e["external_effects"] for e in sink], [0])
        self.assertEqual(sink[0]["reason_typed"], "ok")

    def test_02_p2_current_only_filter(self):
        # store 中确实存在被替代行 (夹具显式保留, 供过滤证明)
        rows = qms_store.field_rows("SR-FRAME-A01", "sop_current")
        currents = [r for r in rows if r["is_current"] == 1]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(currents), 1)
        self.assertEqual(currents[0]["version"], "v5")
        std_rows = qms_store.field_rows("SR-FRAME-A01", "quality_standard")
        self.assertEqual([r["is_current"] for r in std_rows], [1, 0])
        # 响应中被替代/治理标识零出现 (P-2: 绝不引用过期版本)
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                            "process_step": "torque_tightening",
                            "fields": ALL_FIELDS})
        blob = json.dumps(resp, ensure_ascii=False)
        for banned in ("v4", "supersedes", "superseded_by", "is_current",
                       "9.0"):
            self.assertNotIn(banned, blob, f"被替代/治理标识泄漏: {banned}")

    def test_03_p1_zero_existence_leak(self):
        cross = call(PQE_A, {"part_no": "SR-RAIL-B01",
                             "process_step": "torque_tightening",
                             "fields": ALL_FIELDS})
        unknown = call(PQE_A, {"part_no": "SR-DUMMY-A99",
                               "process_step": "torque_tightening",
                               "fields": ALL_FIELDS})
        self.assertFalse(cross["ok"])
        self.assertEqual(cross["error"]["code"], "not_available")
        # 跨项目与未知件号用户面封套逐字节一致 (零存在性泄漏)
        self.assertEqual(json.dumps(cross, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        self.assertNotIn("OEM-B", json.dumps(cross))

    def test_04_supervisor_cross_project_readonly(self):
        resp = call(SUPERVISOR, {"part_no": "SR-RAIL-B01",
                                 "process_step": "torque_tightening",
                                 "fields": ["sop_current"]})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["oem_project"], "OEM-B")
        self.assertEqual(resp["result"]["fields"]["sop_current"]["payload"]
                         ["sop_id"], "QMS-SOP-SR-15")

    def test_05_role_default_deny(self):
        denied = call(OPS, {"part_no": "SR-FRAME-A01",
                            "process_step": "torque_tightening",
                            "fields": ALL_FIELDS})
        unknown = call(OPS, {"part_no": "SR-DUMMY-A99",
                             "process_step": "torque_tightening",
                             "fields": ALL_FIELDS})
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "not_available")
        # 角色不足与未知件号用户面封套逐字节一致
        self.assertEqual(json.dumps(denied, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        sink = []
        call(OPS, {"part_no": "SR-FRAME-A01", "process_step": "torque_tightening",
                   "fields": ALL_FIELDS}, sink)
        self.assertEqual(sink[0]["reason_typed"], "role_denied")

    def test_06_identity_smuggling_and_context_shape(self):
        # 可信会话键走私进调用参数 -> invalid_argument (服务端不执行)
        for key in M.TRUSTED_CONTEXT_KEYS:
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                                "process_step": "torque_tightening",
                                "fields": ALL_FIELDS, key: "x"})
            self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 枚举外未知键
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                            "process_step": "torque_tightening",
                            "fields": ALL_FIELDS, "extra_key": 1})
        self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 可信上下文形状不符 (缺失/多余) -> invalid_argument
        bad_ctx = dict(PQE_A)
        bad_ctx.pop("role")
        self.assertEqual(call(bad_ctx, {"part_no": "SR-FRAME-A01",
                                        "process_step": "torque_tightening",
                                        "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")
        bad_ctx2 = dict(PQE_A)
        bad_ctx2["extra"] = 1
        self.assertEqual(call(bad_ctx2, {"part_no": "SR-FRAME-A01",
                                         "process_step": "torque_tightening",
                                         "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")

    def test_07_field_enum_closed(self):
        base = {"part_no": "SR-FRAME-A01", "process_step": "torque_tightening"}
        for fields in ([], ["sop_current", "sop_current"],
                       list(ALL_FIELDS) + ["quality_standard"],
                       ["not_a_field"], ["bom_snapshot"], "sop_current"):
            resp = call(PQE_A, {**base, "fields": fields})
            self.assertEqual(resp["error"]["code"], "invalid_argument",
                             f"fields={fields!r} 应拒绝")

    def test_08_frozen_syntax(self):
        base_fields = ["sop_current"]
        for part_no in ("sr-frame-a01", "SR-FRAME-a01", "SR-FRAME-A1",
                        "SR-FRAME-C01", "XX-FRAME-A01", "SR-FRAME-A011", 1, None):
            resp = call(PQE_A, {"part_no": part_no,
                                "process_step": "torque_tightening",
                                "fields": base_fields})
            self.assertEqual(resp["error"]["code"], "invalid_argument",
                             f"part_no={part_no!r} 应拒绝")
        for step in ("Torque_Tightening", "gap", "torque", "", 1, None):
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                                "process_step": step, "fields": base_fields})
            self.assertEqual(resp["error"]["code"], "invalid_argument",
                             f"process_step={step!r} 应拒绝")

    def test_09_p3_structured_missing(self):
        # SR-MOTOR-A02 无 checklist_rows -> 字段级结构化缺失, 绝不编造
        resp = call(PQE_A, {"part_no": "SR-MOTOR-A02",
                            "process_step": "torque_tightening",
                            "fields": ["inspection_checklist"]})
        self.assertTrue(resp["ok"])
        entry = resp["result"]["fields"]["inspection_checklist"]
        self.assertEqual(entry["status"], "not_available")
        self.assertEqual(entry["reason_code"], M.FIELD_NOT_AVAILABLE)
        self.assertNotIn("payload", entry)
        # gap_check 工序对 SR-FRAME-A01 无任何行 -> FIELD_NOT_AVAILABLE (非 P-2)
        resp2 = call(PQE_A, {"part_no": "SR-FRAME-A01",
                             "process_step": "gap_check",
                             "fields": ["sop_current"]})
        self.assertTrue(resp2["ok"])
        entry2 = resp2["result"]["fields"]["sop_current"]
        self.assertEqual(entry2["reason_code"], M.FIELD_NOT_AVAILABLE)

    def test_10_p2_no_current_sop(self):
        # SR-MOTOR-A02 仅存被替代行 -> 结构化拒引 NO_CURRENT_SOP
        for field in ("sop_current", "quality_standard"):
            resp = call(PQE_A, {"part_no": "SR-MOTOR-A02",
                                "process_step": "torque_tightening",
                                "fields": [field]})
            self.assertTrue(resp["ok"])
            entry = resp["result"]["fields"][field]
            self.assertEqual(entry["status"], "not_available")
            self.assertEqual(entry["reason_code"], M.NO_CURRENT_SOP)
            self.assertNotIn("payload", entry)
            blob = json.dumps(resp, ensure_ascii=False)
            self.assertNotIn("v3", blob)  # 待发布的现行版本标识也不出
            self.assertNotIn("QMS-SOP-SR-11", blob)  # 被替代 SOP 标识零出现

    def test_11_readonly_fingerprint_and_audit(self):
        """只读性质: store 指纹前后逐字节一致 (§9.3 教训的标准回归检查)。"""
        fp_before = qms_store.store_fingerprint()
        sink = []
        call(PQE_A, {"part_no": "SR-FRAME-A01", "process_step": "torque_tightening",
                     "fields": ALL_FIELDS}, sink, "run-ok")
        call(PQE_A, {"part_no": "SR-RAIL-B01", "process_step": "torque_tightening",
                     "fields": ALL_FIELDS}, sink, "run-cross")
        call(PQE_A, {"part_no": "SR-DUMMY-A99", "process_step": "gap_check",
                     "fields": ALL_FIELDS}, sink, "run-unknown")
        call(SUPERVISOR, {"part_no": "SR-RAIL-B01", "process_step": "gap_check",
                          "fields": ["sop_current"]}, sink, "run-sup")
        call(PQE_A, {"part_no": "SR-MOTOR-A02", "process_step": "torque_tightening",
                     "fields": ["sop_current"]}, sink, "run-p2")
        self.assertEqual(qms_store.store_fingerprint(), fp_before,
                         "只读性质被破坏: store 指纹变化")
        self.assertEqual([e["external_effects"] for e in sink], [0] * 5)
        self.assertEqual([e["reason_typed"] for e in sink],
                         ["ok", "cross_project_denied", "unknown_part", "ok",
                          "ok"])
        # 每次调用独立 run_id (审计关联键)
        self.assertEqual(len({e["run_id"] for e in sink}), 5)

    def test_12_contract_json_interlock(self):
        """代码常量与 api-contract/qms-lookup-v1.json 同值互锁 (改合同必须同步改码)。"""
        with open(os.path.join(PROJECT_ROOT, "api-contract", "qms-lookup-v1.json"),
                  "r", encoding="utf-8") as f:
            contract = json.load(f)
        binding = contract["tool_binding"]
        self.assertEqual(binding["tool_name"], M.TOOL_NAME)
        self.assertEqual(binding["version"], M.TOOL_VERSION)
        self.assertEqual(binding["effect"], M.EFFECT)
        self.assertEqual(binding["fields_enum"], list(M.FIELD_ENUM))
        self.assertEqual(binding["fields_min"], M.FIELDS_MIN)
        self.assertEqual(binding["fields_max"], M.FIELDS_MAX)
        self.assertEqual(binding["part_no_pattern"], M.PART_NO_PATTERN)
        self.assertEqual(binding["process_step_enum"], list(M.PROCESS_STEP_ENUM))
        self.assertEqual(binding["p2"]["no_current_reason_code"], M.NO_CURRENT_SOP)
        self.assertEqual(binding["p2"]["current_only_policy"],
                         M.CURRENT_ONLY_POLICY)
        self.assertEqual(sorted(binding["error_taxonomy"]), sorted(M.ERROR_CODES))
        self.assertEqual(binding["allowed_roles"], list(M.ALLOWED_ROLES))
        self.assertEqual(tuple(binding["trusted_context_keys"]),
                         M.TRUSTED_CONTEXT_KEYS)
        face = contract["http_face"]
        self.assertEqual(face["properties"]["part_no"]["pattern"],
                         M.PART_NO_PATTERN)
        self.assertEqual(face["properties"]["process_step"]["enum"],
                         list(M.PROCESS_STEP_ENUM))
        self.assertEqual(face["properties"]["fields"]["maxItems"], M.FIELDS_MAX)


class QmsHttpFaceTests(unittest.TestCase):
    """application/main.py 节点 ⑲ 追加端点 + 控制台第六面板 + 确定性。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-qms-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_qms_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每用例独立初始化, 保证确定性
        self.client = TestClient(app)
        qms_gateway.reset_audit()

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)
        qms_gateway.reset_audit()

    def _post(self, part_no="SR-FRAME-A01", step="torque_tightening",
              fields=None, token=None, **extra):
        headers = {"X-Auth-Token": token} if token else {}
        body = {"part_no": part_no, "process_step": step,
                "fields": fields or ["sop_current"], **extra}
        return self.client.post("/api/v1/qms/inspection-standard",
                                json=body, headers=headers)

    def test_13_determinism_http_face_and_console_panel(self):
        # 确定性: 同参数两次调用 result 逐字节一致 (零随机零时钟)
        args = {"part_no": "SR-FRAME-A01", "process_step": "torque_tightening",
                "fields": ALL_FIELDS}
        a = call(PQE_A, dict(args))
        b = call(PQE_A, dict(args))
        self.assertEqual(json.dumps(a["result"], sort_keys=True),
                         json.dumps(b["result"], sort_keys=True))
        # 默认 PQE 培训身份 (免令牌, OEM-A): ok + 只读零效果 + 审计关联键
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["business_effects"], 0)
        self.assertEqual(body["effect"], "read_only")
        self.assertEqual(body["tool"], M.TOOL_NAME)
        self.assertTrue(body["run_id"].startswith("qms-"))
        # 显式令牌身份矩阵: OEM-B PQE 查本项目组 / 主管跨项目只读
        self.assertTrue(self._post("SR-RAIL-B01",
                                   token="token_pqe_oem_b").json()["ok"])
        self.assertTrue(self._post(
            "SR-RAIL-B01", token="token_qa_supervisor_secret").json()["ok"])
        # 跨项目: 200 + ok=false 结构化拒绝, 受限审计落 cross_project_denied
        audit_before = len(qms_gateway.AUDIT)
        resp = self._post("SR-RAIL-B01")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(len(qms_gateway.AUDIT), audit_before + 1)
        self.assertEqual(qms_gateway.AUDIT[-1]["reason_typed"],
                         "cross_project_denied")
        # 未知令牌 401: 查询不执行, 不落受限审计
        audit_before = len(qms_gateway.AUDIT)
        resp = self._post(token="token_invalid_x")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(qms_gateway.AUDIT), audit_before)
        # 封闭契约: 自报身份 / 未知字段 / 缺字段 / 语法错 -> 422
        self.assertEqual(self._post(role="quality_supervisor").status_code, 422)
        self.assertEqual(self._post(extra=1).status_code, 422)
        resp = self.client.post("/api/v1/qms/inspection-standard",
                                json={"part_no": "SR-FRAME-A01",
                                      "process_step": "torque_tightening"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._post(part_no="SR-FRAME-C01").status_code, 422)
        self.assertEqual(self._post(step="gap").status_code, 422)
        # P-2 经 HTTP: MOTOR sop 字段 NO_CURRENT_SOP + 被替代标识零出现
        resp = self._post("SR-MOTOR-A02")
        entry = resp.json()["result"]["fields"]["sop_current"]
        self.assertEqual(entry["reason_code"], M.NO_CURRENT_SOP)
        self.assertNotIn("v3", json.dumps(resp.json(), ensure_ascii=False))
        # 控制台第六面板可达且零令牌字面量
        page = self.client.get("/ui")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn('data-tab="qms"', html)
        self.assertIn("/api/v1/qms/inspection-standard", html)
        for secret in ("token_pqe_oem_a", "token_pqe_oem_b",
                       "token_qa_supervisor_secret"):
            self.assertNotIn(secret, html, "控制台页面不得内嵌令牌")


if __name__ == "__main__":
    unittest.main()
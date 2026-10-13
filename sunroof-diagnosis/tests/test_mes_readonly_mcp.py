"""
阶段 C 节点 ⑰ MES 只读 MCP 工具 —— 自动化集成测试

回链 api-contract/mes-lookup-v1.json + mcptool/mes_contract.py + mcptool/mes_store.py
+ mcptool/mes_server.py + application/mes.py + application/main.py 节点 ⑰ 追加端点
(discovery-brief-v1.md In-Scope 第 1 条的阶段 C 兑现):
  01 合法 PQE 三字段: torque/ecu available + 波形分页 32 点 + 零效果
  02 R-2 波形分页: 4 段走查拼接 = 全量 128 点 + 越界/类型错误拒绝
  03 P-1 零存在性泄漏: 跨项目 SN 与未知 SN 用户面封套逐字节一致, 响应零 "OEM-B"
  04 主管跨项目只读 (矩阵 §1 特批例外)
  05 角色默认拒绝 (ops ✗): 封套与未知 SN 逐字节一致, 审计 reason_typed=role_denied
  06 身份参数走私 + 可信上下文形状不符: 一律 invalid_argument
  07 字段封闭枚举: 空/未知/超量/重复 fields 全部 invalid_argument
  08 SN 语法冻结: 非法格式一律 invalid_argument (不归一化)
  09 P-3 缺失字段结构化: FIELD_NOT_AVAILABLE, 绝不编造数值
  10 只读证明: store 指纹前后逐字节一致 + 外部效果恒 0 + 审计分型对账
  11 合同互锁: 代码常量与 api-contract/mes-lookup-v1.json 同值
  12 确定性: 同参数两次调用 result 逐字节一致
  13 HTTP 面: 身份矩阵 + 401 不落审计 + 自报身份 422 + 控制台第四面板零令牌

运行: python -m unittest tests.test_mes_readonly_mcp -v
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
from application import mes as mes_gateway  # noqa: E402
from application.main import app  # noqa: E402
from mcptool import mes_contract as M  # noqa: E402
from mcptool import mes_server, mes_store  # noqa: E402

PQE_A = {"tenant": "sunroof-training", "user": "pqe_chen_oema", "role": "pqe",
         "allowed_projects": ["OEM-A"]}
SUPERVISOR = {"tenant": "sunroof-training", "user": "supervisor_li",
              "role": "quality_supervisor", "allowed_projects": ["*"]}
OPS = {"tenant": "sunroof-training", "user": "ops_wang", "role": "ops",
       "allowed_projects": []}

ALL_FIELDS = list(M.FIELD_ENUM)


def call(ctx, args, sink=None, run_id="run-test"):
    """工具服务端直调 (HTTP 之下的那一层)。"""
    return mes_server.lookup_production_record(ctx, args, audit_sink=sink, run_id=run_id)


class MesToolCoreTests(unittest.TestCase):
    """mcptool 层: 合同封闭性 / ACL / P-1 / P-3 / R-2 / 只读性质。"""

    def test_01_valid_pqe_three_fields(self):
        sink = []
        resp = call(PQE_A, {"sn": "ROOF-10001", "fields": ALL_FIELDS}, sink)
        self.assertTrue(resp["ok"])
        result = resp["result"]
        self.assertEqual(result["sn"], "ROOF-10001")
        self.assertEqual(result["oem_project"], "OEM-A")
        self.assertEqual(result["record_version"], 1)
        fields = result["fields"]
        self.assertEqual(fields["assembly_torque"]["status"], "available")
        self.assertEqual(fields["assembly_torque"]["payload"]["count"], 2)
        self.assertEqual(fields["ecu_flash"]["status"], "available")
        self.assertEqual(fields["ecu_flash"]["payload"]["sw_version"], "SW-2.3.1")
        payload = fields["sensor_waveform"]["payload"]
        self.assertEqual(len(payload["points"]), M.WAVEFORM_SEGMENT_POINTS)
        self.assertEqual(payload["points_returned"], 32)
        self.assertEqual(payload["total_points"], 128)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["next_segment"], 1)
        # 只读零效果 + 审计 ok
        self.assertEqual([e["external_effects"] for e in sink], [0])
        self.assertEqual(sink[0]["reason_typed"], "ok")

    def test_02_waveform_paging_r2(self):
        segments = []
        for seg in range(M.WAVEFORM_SEGMENTS):
            resp = call(PQE_A, {"sn": "ROOF-10001",
                                "fields": ["sensor_waveform"], "segment": seg})
            self.assertTrue(resp["ok"])
            payload = resp["result"]["fields"]["sensor_waveform"]["payload"]
            self.assertEqual(payload["segment"], seg)
            self.assertEqual(len(payload["points"]), M.WAVEFORM_SEGMENT_POINTS)
            segments.append(payload)
        # 四段拼接 == 直接生成的全量 128 点 (逐字节一致)
        full = []
        for payload in segments:
            full.extend(payload["points"])
        self.assertEqual(full, mes_store.waveform_points("ROOF-10001"))
        self.assertTrue(segments[0]["truncated"])
        self.assertTrue(segments[2]["truncated"])
        self.assertFalse(segments[3]["truncated"])
        self.assertIsNone(segments[3]["next_segment"])
        # 越界 / 非整数 segment 一律 invalid_argument
        for bad in ({"segment": 4}, {"segment": -1}, {"segment": "0"},
                    {"segment": True}, {"segment": 1.5}):
            resp = call(PQE_A, {"sn": "ROOF-10001",
                                "fields": ["sensor_waveform"], **bad})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_03_p1_zero_existence_leak(self):
        cross = call(PQE_A, {"sn": "ROOF-20001", "fields": ALL_FIELDS})
        unknown = call(PQE_A, {"sn": "ROOF-99999", "fields": ALL_FIELDS})
        self.assertFalse(cross["ok"])
        self.assertEqual(cross["error"]["code"], "not_available")
        # 跨项目与未知 SN 用户面封套逐字节一致 (零存在性泄漏)
        self.assertEqual(json.dumps(cross, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        self.assertNotIn("OEM-B", json.dumps(cross))

    def test_04_supervisor_cross_project_readonly(self):
        resp = call(SUPERVISOR, {"sn": "ROOF-20001", "fields": ["ecu_flash"]})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["oem_project"], "OEM-B")

    def test_05_role_default_deny(self):
        denied = call(OPS, {"sn": "ROOF-10001", "fields": ALL_FIELDS})
        unknown = call(OPS, {"sn": "ROOF-99999", "fields": ALL_FIELDS})
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "not_available")
        # 角色不足与未知 SN 用户面封套逐字节一致
        self.assertEqual(json.dumps(denied, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        sink = []
        call(OPS, {"sn": "ROOF-10001", "fields": ALL_FIELDS}, sink)
        self.assertEqual(sink[0]["reason_typed"], "role_denied")

    def test_06_identity_smuggling_and_context_shape(self):
        # 可信会话键走私进调用参数 -> invalid_argument (服务端不执行)
        for key in M.TRUSTED_CONTEXT_KEYS:
            resp = call(PQE_A, {"sn": "ROOF-10001", "fields": ALL_FIELDS, key: "x"})
            self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 枚举外未知键
        resp = call(PQE_A, {"sn": "ROOF-10001", "fields": ALL_FIELDS, "extra_key": 1})
        self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 可信上下文键形状不符 (缺失/多出) -> invalid_argument
        bad_ctx = dict(PQE_A)
        bad_ctx.pop("role")
        self.assertEqual(call(bad_ctx, {"sn": "ROOF-10001",
                                        "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")
        forged = dict(PQE_A)
        forged["role2"] = "quality_supervisor"
        self.assertEqual(call(forged, {"sn": "ROOF-10001",
                                       "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")

    def test_07_closed_field_enum(self):
        for fields in ([], ["note"], ALL_FIELDS + ["note"],
                       ["assembly_torque", "assembly_torque"]):
            resp = call(PQE_A, {"sn": "ROOF-10001", "fields": fields})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_08_sn_syntax_frozen(self):
        for sn in ("roof-10001", "ROOF-10001 ", "ROOF-100012", "ROOF-1000",
                   "SR-1042", "", "ROOF_10001", 10001):
            resp = call(PQE_A, {"sn": sn, "fields": ["ecu_flash"]})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_09_p3_missing_field_structured(self):
        resp = call(PQE_A, {"sn": "ROOF-10003",
                            "fields": ["sensor_waveform", "assembly_torque"]})
        self.assertTrue(resp["ok"])
        fields = resp["result"]["fields"]
        wave = fields["sensor_waveform"]
        self.assertEqual(wave["status"], "not_available")
        self.assertEqual(wave["reason_code"], "FIELD_NOT_AVAILABLE")
        self.assertNotIn("payload", wave)
        # 绝不编造: 缺失字段条目内零数值载荷
        self.assertNotIn("points", json.dumps(wave))
        self.assertEqual(fields["assembly_torque"]["status"], "available")

    def test_10_readonly_fingerprint_and_audit(self):
        """只读性质: store 指纹前后逐字节一致 (§9.3 教训的标准回归检查)。"""
        fp_before = mes_store.store_fingerprint()
        sink = []
        call(PQE_A, {"sn": "ROOF-10001", "fields": ALL_FIELDS}, sink, "run-ok")
        call(PQE_A, {"sn": "ROOF-20001", "fields": ALL_FIELDS}, sink, "run-cross")
        call(PQE_A, {"sn": "ROOF-99999", "fields": ALL_FIELDS}, sink, "run-unknown")
        call(SUPERVISOR, {"sn": "ROOF-20001", "fields": ALL_FIELDS}, sink, "run-sup")
        call(PQE_A, {"sn": "ROOF-10003",
                     "fields": ["sensor_waveform"]}, sink, "run-missing")
        self.assertEqual(mes_store.store_fingerprint(), fp_before,
                         "只读性质被破坏: store 指纹变化")
        self.assertEqual([e["external_effects"] for e in sink], [0] * 5)
        self.assertEqual([e["reason_typed"] for e in sink],
                         ["ok", "cross_project_denied", "unknown_sn", "ok", "ok"])
        # 每次调用独立 run_id (审计关联键)
        self.assertEqual(len({e["run_id"] for e in sink}), 5)

    def test_11_contract_json_interlock(self):
        """代码常量与 api-contract/mes-lookup-v1.json 同值互锁 (改合同必须同步改码)。"""
        with open(os.path.join(PROJECT_ROOT, "api-contract", "mes-lookup-v1.json"),
                  "r", encoding="utf-8") as f:
            contract = json.load(f)
        binding = contract["tool_binding"]
        self.assertEqual(binding["tool_name"], M.TOOL_NAME)
        self.assertEqual(binding["version"], M.TOOL_VERSION)
        self.assertEqual(binding["effect"], M.EFFECT)
        self.assertEqual(binding["fields_enum"], list(M.FIELD_ENUM))
        self.assertEqual(binding["fields_min"], M.FIELDS_MIN)
        self.assertEqual(binding["fields_max"], M.FIELDS_MAX)
        self.assertEqual(binding["sn_pattern"], M.SN_PATTERN)
        self.assertEqual(binding["waveform"]["total_points"], M.WAVEFORM_TOTAL_POINTS)
        self.assertEqual(binding["waveform"]["segment_points"],
                         M.WAVEFORM_SEGMENT_POINTS)
        self.assertEqual(binding["waveform"]["segments"], M.WAVEFORM_SEGMENTS)
        self.assertEqual(sorted(binding["error_taxonomy"]), sorted(M.ERROR_CODES))
        self.assertEqual(binding["allowed_roles"], list(M.ALLOWED_ROLES))
        self.assertEqual(tuple(binding["trusted_context_keys"]),
                         M.TRUSTED_CONTEXT_KEYS)
        face = contract["http_face"]
        self.assertEqual(face["properties"]["sn"]["pattern"], M.SN_PATTERN)
        self.assertEqual(face["properties"]["fields"]["maxItems"], M.FIELDS_MAX)

    def test_12_determinism(self):
        """同参数两次调用 result 逐字节一致 (零随机零时钟)。"""
        args = {"sn": "ROOF-10001", "fields": ALL_FIELDS, "segment": 1}
        a = call(PQE_A, dict(args))
        b = call(PQE_A, dict(args))
        self.assertEqual(json.dumps(a["result"], sort_keys=True),
                         json.dumps(b["result"], sort_keys=True))


class MesHttpFaceTests(unittest.TestCase):
    """application/main.py 节点 ⑰ 追加端点 + 控制台第四面板。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-mes-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_mes_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每用例独立初始化, 保证确定性
        self.client = TestClient(app)
        mes_gateway.reset_audit()

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)
        mes_gateway.reset_audit()

    def _post(self, sn="ROOF-10001", fields=None, token=None, **extra):
        headers = {"X-Auth-Token": token} if token else {}
        body = {"sn": sn, "fields": fields or ["ecu_flash"], **extra}
        return self.client.post("/api/v1/mes/production-record",
                                json=body, headers=headers)

    def test_13_http_face_and_console_panel(self):
        # 默认 PQE 培训身份 (免令牌, OEM-A): ok + 只读零效果 + 审计关联键
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["business_effects"], 0)
        self.assertEqual(body["effect"], "read_only")
        self.assertEqual(body["tool"], M.TOOL_NAME)
        self.assertTrue(body["run_id"].startswith("mes-"))
        # 显式令牌身份矩阵: OEM-B PQE 查本项目组 / 主管跨项目只读
        self.assertTrue(self._post("ROOF-20001",
                                   token="token_pqe_oem_b").json()["ok"])
        self.assertTrue(self._post(
            "ROOF-20001", token="token_qa_supervisor_secret").json()["ok"])
        # 跨项目: 200 + ok=false 结构化拒绝, 受限审计落 cross_project_denied
        audit_before = len(mes_gateway.AUDIT)
        resp = self._post("ROOF-20001")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(len(mes_gateway.AUDIT), audit_before + 1)
        self.assertEqual(mes_gateway.AUDIT[-1]["reason_typed"],
                         "cross_project_denied")
        # 未知令牌 401: 查询不执行, 不落受限审计
        audit_before = len(mes_gateway.AUDIT)
        resp = self._post(token="token_invalid_x")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(mes_gateway.AUDIT), audit_before)
        # 封闭契约: 自报身份 / 未知字段 / 缺 fields / SN 语法错 -> 422
        self.assertEqual(self._post(role="quality_supervisor").status_code, 422)
        self.assertEqual(self._post(extra=1).status_code, 422)
        resp = self.client.post("/api/v1/mes/production-record",
                                json={"sn": "ROOF-10001"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._post(sn="SR-1042").status_code, 422)
        # 波形分页经 HTTP (R-2): 末段 truncated=false
        resp = self._post(fields=["sensor_waveform"], segment=3)
        payload = resp.json()["result"]["fields"]["sensor_waveform"]["payload"]
        self.assertFalse(payload["truncated"])
        self.assertIsNone(payload["next_segment"])
        # 控制台第四面板可达且零令牌字面量
        page = self.client.get("/ui")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn('data-tab="mes"', html)
        self.assertIn("/api/v1/mes/production-record", html)
        for secret in ("token_pqe_oem_a", "token_pqe_oem_b",
                       "token_qa_supervisor_secret"):
            self.assertNotIn(secret, html, "控制台页面不得内嵌令牌")


if __name__ == "__main__":
    unittest.main()

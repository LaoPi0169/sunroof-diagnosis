"""
阶段 C 节点 ⑱ PLM 只读 MCP 工具 —— 自动化集成测试

回链 api-contract/plm-lookup-v1.json + mcptool/plm_contract.py + mcptool/plm_store.py
+ mcptool/plm_server.py + application/plm.py + application/main.py 节点 ⑱ 追加端点
(discovery-brief-v1.md In-Scope 第 2 条 PLM 半边的阶段 C 兑现):
  01 合法 PQE 三字段: bom/eco(分页)/drawing_registry(元数据) + 零效果
  02 ECO 分页: 偏移走查拼接 = 全量 6 条 + 越界/类型错误拒绝 (空窗合法)
  03 P-1 零存在性泄漏: 跨项目件号与未知件号用户面封套逐字节一致, 响应零 "OEM-B"
  04 主管跨项目只读 (矩阵 §2 ✓ 只读)
  05 角色默认拒绝 (ops ✗): 封套与未知件号逐字节一致, 审计 reason_typed=role_denied
  06 身份参数走私 + 可信上下文形状不符: 一律 invalid_argument
  07 字段封闭枚举: 空/未知/超量/重复 fields 全部 invalid_argument
  08 part_no 语法冻结: 非法格式一律 invalid_argument (不归一化)
  09 P-3 缺失字段结构化: FIELD_NOT_AVAILABLE, 绝不编造数值
  10 图纸硬门槛: 内容/URL/渲染数据永不出工具, content_disclosure=denied_by_policy
  11 只读证明: store 指纹前后逐字节一致 + 外部效果恒 0 + 审计分型对账
  12 合同互锁: 代码常量与 api-contract/plm-lookup-v1.json 同值
  13 确定性 + HTTP 面: 两次调用一致 + 身份矩阵 + 401 不落审计 + 自报身份 422
      + 控制台第五面板零令牌

运行: python -m unittest tests.test_plm_readonly_mcp -v
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
from application import plm as plm_gateway  # noqa: E402
from application.main import app  # noqa: E402
from mcptool import plm_contract as M  # noqa: E402
from mcptool import plm_server, plm_store  # noqa: E402

PQE_A = {"tenant": "sunroof-training", "user": "pqe_chen_oema", "role": "pqe",
         "allowed_projects": ["OEM-A"]}
SUPERVISOR = {"tenant": "sunroof-training", "user": "supervisor_li",
              "role": "quality_supervisor", "allowed_projects": ["*"]}
OPS = {"tenant": "sunroof-training", "user": "ops_wang", "role": "ops",
       "allowed_projects": []}

ALL_FIELDS = list(M.FIELD_ENUM)


def call(ctx, args, sink=None, run_id="run-test"):
    """工具服务端直调 (HTTP 之下的那一层)。"""
    return plm_server.lookup_engineering_record(ctx, args, audit_sink=sink, run_id=run_id)


class PlmToolCoreTests(unittest.TestCase):
    """mcptool 层: 合同封闭性 / ACL / P-1 / P-3 / 图纸硬门槛 / ECO 分页 / 只读性质。"""

    def test_01_valid_pqe_three_fields(self):
        sink = []
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01", "fields": ALL_FIELDS}, sink)
        self.assertTrue(resp["ok"])
        result = resp["result"]
        self.assertEqual(result["part_no"], "SR-FRAME-A01")
        self.assertEqual(result["oem_project"], "OEM-A")
        self.assertEqual(result["record_version"], 1)
        fields = result["fields"]
        self.assertEqual(fields["bom_snapshot"]["status"], "available")
        self.assertEqual(fields["bom_snapshot"]["payload"]["revision"], "R4")
        self.assertEqual(fields["bom_snapshot"]["payload"]["count"], 3)
        eco = fields["eco_history"]["payload"]
        self.assertEqual(eco["total_changes"], 6)
        self.assertEqual(eco["returned_changes"], M.ECO_MAX_RETURN)
        self.assertTrue(eco["truncated"])
        self.assertEqual(eco["next_offset"], 4)
        dwg = fields["drawing_registry"]["payload"]
        self.assertEqual(dwg["count"], 1)
        self.assertEqual(dwg["content_disclosure"], M.DRAWING_CONTENT_DISCLOSURE)
        # 只读零效果 + 审计 ok
        self.assertEqual([e["external_effects"] for e in sink], [0])
        self.assertEqual(sink[0]["reason_typed"], "ok")

    def test_02_eco_paging_walk(self):
        windows = []
        for offset in (0, 4):
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                                "fields": ["eco_history"], "eco_offset": offset})
            self.assertTrue(resp["ok"])
            payload = resp["result"]["fields"]["eco_history"]["payload"]
            self.assertEqual(payload["eco_offset"], offset)
            windows.append(payload)
        self.assertEqual(windows[0]["returned_changes"], M.ECO_MAX_RETURN)
        self.assertEqual(windows[1]["returned_changes"], 2)  # 末页剩余 2 条
        # 分页拼接 == 全量 6 条 (逐条一致)
        merged = windows[0]["changes"] + windows[1]["changes"]
        self.assertEqual(merged, plm_store.eco_changes("SR-FRAME-A01"))
        self.assertTrue(windows[0]["truncated"])
        self.assertFalse(windows[1]["truncated"])
        self.assertIsNone(windows[1]["next_offset"])
        # 空窗 (offset 8 >= total 6): 合法, 返回空列表零编造
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                            "fields": ["eco_history"], "eco_offset": 8})
        self.assertTrue(resp["ok"])
        empty = resp["result"]["fields"]["eco_history"]["payload"]
        self.assertEqual(empty["changes"], [])
        self.assertEqual(empty["returned_changes"], 0)
        self.assertFalse(empty["truncated"])
        self.assertIsNone(empty["next_offset"])
        # 越界 / 非整数 eco_offset 一律 invalid_argument
        for bad in ({"eco_offset": 13}, {"eco_offset": -1}, {"eco_offset": "0"},
                    {"eco_offset": True}, {"eco_offset": 1.5}):
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                                "fields": ["eco_history"], **bad})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_03_p1_zero_existence_leak(self):
        cross = call(PQE_A, {"part_no": "SR-RAIL-B01", "fields": ALL_FIELDS})
        unknown = call(PQE_A, {"part_no": "SR-DUMMY-A99", "fields": ALL_FIELDS})
        self.assertFalse(cross["ok"])
        self.assertEqual(cross["error"]["code"], "not_available")
        # 跨项目与未知件号用户面封套逐字节一致 (零存在性泄漏)
        self.assertEqual(json.dumps(cross, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        self.assertNotIn("OEM-B", json.dumps(cross))

    def test_04_supervisor_cross_project_readonly(self):
        resp = call(SUPERVISOR, {"part_no": "SR-RAIL-B01", "fields": ["bom_snapshot"]})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["oem_project"], "OEM-B")

    def test_05_role_default_deny(self):
        denied = call(OPS, {"part_no": "SR-FRAME-A01", "fields": ALL_FIELDS})
        unknown = call(OPS, {"part_no": "SR-DUMMY-A99", "fields": ALL_FIELDS})
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "not_available")
        # 角色不足与未知件号用户面封套逐字节一致
        self.assertEqual(json.dumps(denied, sort_keys=True),
                         json.dumps(unknown, sort_keys=True))
        sink = []
        call(OPS, {"part_no": "SR-FRAME-A01", "fields": ALL_FIELDS}, sink)
        self.assertEqual(sink[0]["reason_typed"], "role_denied")

    def test_06_identity_smuggling_and_context_shape(self):
        # 可信会话键走私进调用参数 -> invalid_argument (服务端不执行)
        for key in M.TRUSTED_CONTEXT_KEYS:
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                                "fields": ALL_FIELDS, key: "x"})
            self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 枚举外未知键
        resp = call(PQE_A, {"part_no": "SR-FRAME-A01",
                            "fields": ALL_FIELDS, "extra_key": 1})
        self.assertEqual(resp["error"]["code"], "invalid_argument")
        # 可信上下文键形状不符 (缺失/多出) -> invalid_argument
        bad_ctx = dict(PQE_A)
        bad_ctx.pop("role")
        self.assertEqual(call(bad_ctx, {"part_no": "SR-FRAME-A01",
                                        "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")
        forged = dict(PQE_A)
        forged["role2"] = "quality_supervisor"
        self.assertEqual(call(forged, {"part_no": "SR-FRAME-A01",
                                       "fields": ALL_FIELDS})["error"]["code"],
                         "invalid_argument")

    def test_07_closed_field_enum(self):
        for fields in ([], ["note"], ALL_FIELDS + ["note"],
                       ["bom_snapshot", "bom_snapshot"]):
            resp = call(PQE_A, {"part_no": "SR-FRAME-A01", "fields": fields})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_08_part_no_syntax_frozen(self):
        for part_no in ("sr-frame-a01", "SR-FRAME-A01 ", " SR-FRAME-A01",
                        "SR-FRAME-C01", "SR-FR-A01", "SR-FRAME-A1",
                        "SR-FRAME-A012", "ROOF-10001", "", "SR_FRAME_A01", 1001):
            resp = call(PQE_A, {"part_no": part_no, "fields": ["bom_snapshot"]})
            self.assertEqual(resp["error"]["code"], "invalid_argument")

    def test_09_p3_missing_field_structured(self):
        resp = call(PQE_A, {"part_no": "SR-MOTOR-A02",
                            "fields": ["drawing_registry", "bom_snapshot"]})
        self.assertTrue(resp["ok"])
        fields = resp["result"]["fields"]
        dwg = fields["drawing_registry"]
        self.assertEqual(dwg["status"], "not_available")
        self.assertEqual(dwg["reason_code"], "FIELD_NOT_AVAILABLE")
        self.assertNotIn("payload", dwg)
        # 绝不编造: 缺失字段条目内零图纸载荷
        self.assertNotIn("drawing_id", json.dumps(dwg))
        self.assertEqual(fields["bom_snapshot"]["status"], "available")

    def test_10_drawing_hard_gate(self):
        """P-1 图纸硬门槛: 图纸内容/文件/链接/渲染数据永不出工具 (主管亦不展示)。"""
        for ctx in (PQE_A, SUPERVISOR):
            resp = call(ctx, {"part_no": "SR-FRAME-A01",
                              "fields": ["drawing_registry"]})
            self.assertTrue(resp["ok"])
            payload = resp["result"]["fields"]["drawing_registry"]["payload"]
            self.assertEqual(payload["content_disclosure"],
                             M.DRAWING_CONTENT_DISCLOSURE)
            blob = json.dumps(payload, ensure_ascii=False).lower()
            # 元数据之外零内容载体: 无 URL / 无文件路径 / 无 base64 / 无渲染数据
            # (content_disclosure 为策略标记本身, 不在禁词之列)
            for banned in ("http", ".dwg", ".pdf", ".png", "base64",
                           "url", "path", "data:"):
                self.assertNotIn(banned, blob,
                                 f"图纸载荷出现被禁内容载体: {banned}")
            # 登记元数据字段闭合
            self.assertEqual(set(payload["drawings"][0]),
                             {"drawing_id", "revision", "title", "status"})

    def test_11_readonly_fingerprint_and_audit(self):
        """只读性质: store 指纹前后逐字节一致 (§9.3 教训的标准回归检查)。"""
        fp_before = plm_store.store_fingerprint()
        sink = []
        call(PQE_A, {"part_no": "SR-FRAME-A01", "fields": ALL_FIELDS}, sink, "run-ok")
        call(PQE_A, {"part_no": "SR-RAIL-B01", "fields": ALL_FIELDS}, sink, "run-cross")
        call(PQE_A, {"part_no": "SR-DUMMY-A99", "fields": ALL_FIELDS}, sink, "run-unknown")
        call(SUPERVISOR, {"part_no": "SR-RAIL-B01", "fields": ALL_FIELDS}, sink, "run-sup")
        call(PQE_A, {"part_no": "SR-MOTOR-A02",
                     "fields": ["drawing_registry"]}, sink, "run-missing")
        self.assertEqual(plm_store.store_fingerprint(), fp_before,
                         "只读性质被破坏: store 指纹变化")
        self.assertEqual([e["external_effects"] for e in sink], [0] * 5)
        self.assertEqual([e["reason_typed"] for e in sink],
                         ["ok", "cross_project_denied", "unknown_part", "ok", "ok"])
        # 每次调用独立 run_id (审计关联键)
        self.assertEqual(len({e["run_id"] for e in sink}), 5)

    def test_12_contract_json_interlock(self):
        """代码常量与 api-contract/plm-lookup-v1.json 同值互锁 (改合同必须同步改码)。"""
        with open(os.path.join(PROJECT_ROOT, "api-contract", "plm-lookup-v1.json"),
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
        self.assertEqual(binding["eco"]["max_return"], M.ECO_MAX_RETURN)
        self.assertEqual(binding["eco"]["offset_max"], M.ECO_OFFSET_MAX)
        self.assertEqual(sorted(binding["error_taxonomy"]), sorted(M.ERROR_CODES))
        self.assertEqual(binding["allowed_roles"], list(M.ALLOWED_ROLES))
        self.assertEqual(tuple(binding["trusted_context_keys"]),
                         M.TRUSTED_CONTEXT_KEYS)
        face = contract["http_face"]
        self.assertEqual(face["properties"]["part_no"]["pattern"], M.PART_NO_PATTERN)
        self.assertEqual(face["properties"]["fields"]["maxItems"], M.FIELDS_MAX)
        self.assertEqual(face["properties"]["eco_offset"]["maximum"],
                         M.ECO_OFFSET_MAX)


class PlmHttpFaceTests(unittest.TestCase):
    """application/main.py 节点 ⑱ 追加端点 + 控制台第五面板 + 确定性。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="sunroof-plm-test-")
        self.db_path = os.path.join(self._tmpdir.name, "sunroof_plm_test.db")
        os.environ["FDE_DB_PATH"] = self.db_path
        database.init_db(self.db_path)  # 每用例独立初始化, 保证确定性
        self.client = TestClient(app)
        plm_gateway.reset_audit()

    def tearDown(self):
        self._tmpdir.cleanup()
        os.environ.pop("FDE_DB_PATH", None)
        plm_gateway.reset_audit()

    def _post(self, part_no="SR-FRAME-A01", fields=None, token=None, **extra):
        headers = {"X-Auth-Token": token} if token else {}
        body = {"part_no": part_no, "fields": fields or ["bom_snapshot"], **extra}
        return self.client.post("/api/v1/plm/engineering-record",
                                json=body, headers=headers)

    def test_13_determinism_http_face_and_console_panel(self):
        # 确定性: 同参数两次调用 result 逐字节一致 (零随机零时钟)
        args = {"part_no": "SR-FRAME-A01", "fields": ALL_FIELDS, "eco_offset": 4}
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
        self.assertTrue(body["run_id"].startswith("plm-"))
        # 显式令牌身份矩阵: OEM-B PQE 查本项目组 / 主管跨项目只读
        self.assertTrue(self._post("SR-RAIL-B01",
                                   token="token_pqe_oem_b").json()["ok"])
        self.assertTrue(self._post(
            "SR-RAIL-B01", token="token_qa_supervisor_secret").json()["ok"])
        # 跨项目: 200 + ok=false 结构化拒绝, 受限审计落 cross_project_denied
        audit_before = len(plm_gateway.AUDIT)
        resp = self._post("SR-RAIL-B01")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertEqual(len(plm_gateway.AUDIT), audit_before + 1)
        self.assertEqual(plm_gateway.AUDIT[-1]["reason_typed"],
                         "cross_project_denied")
        # 未知令牌 401: 查询不执行, 不落受限审计
        audit_before = len(plm_gateway.AUDIT)
        resp = self._post(token="token_invalid_x")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(plm_gateway.AUDIT), audit_before)
        # 封闭契约: 自报身份 / 未知字段 / 缺 fields / part_no 语法错 -> 422
        self.assertEqual(self._post(role="quality_supervisor").status_code, 422)
        self.assertEqual(self._post(extra=1).status_code, 422)
        resp = self.client.post("/api/v1/plm/engineering-record",
                                json={"part_no": "SR-FRAME-A01"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self._post(part_no="SR-FRAME-C01").status_code, 422)
        # ECO 分页经 HTTP: offset=4 末页 truncated=false + 图纸硬门槛透传
        resp = self._post(fields=["eco_history", "drawing_registry"], eco_offset=4)
        fields = resp.json()["result"]["fields"]
        self.assertFalse(fields["eco_history"]["payload"]["truncated"])
        self.assertIsNone(fields["eco_history"]["payload"]["next_offset"])
        self.assertEqual(fields["drawing_registry"]["payload"]["content_disclosure"],
                         M.DRAWING_CONTENT_DISCLOSURE)
        # 控制台第五面板可达且零令牌字面量
        page = self.client.get("/ui")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn('data-tab="plm"', html)
        self.assertIn("/api/v1/plm/engineering-record", html)
        for secret in ("token_pqe_oem_a", "token_pqe_oem_b",
                       "token_qa_supervisor_secret"):
            self.assertNotIn(secret, html, "控制台页面不得内嵌令牌")


if __name__ == "__main__":
    unittest.main()
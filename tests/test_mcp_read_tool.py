"""
只读 MCP 工具测试 (Week 13) —— 回链 specs/mission-week-13.md 验收标准

覆盖十项:
  01 合同/常量/能力清单三方互锁 (tool/version/effect/错误五类/字段枚举/上限/deadline/映射表/sha256)
  02 R-01 正常路径: ok + 恰好请求字段 + 来源元数据; 响应无受限内容无会话键
  03 禁止字段与未知字段 schema 拒绝 (R-02/X-02 + customer_name/payment_detail)
  04 R-03 不可区分性: 不存在/跨租户/角色不可见用户面逐字节一致; 审计原因三类可区分
  05 R-04 超时: deadline_exceeded; 响应不含任何上下文字段
  06 R-05 超合同结果整体拒绝 + 安全事件入账
  07 X-01 身份走私 Host 层拒绝 (服务端零调用) + X-03 版本不匹配整体作废
  08 零写效果: 全夹具 external_write_effects=0 + 工单库快照逐字节一致 + 无写入口
  09 13 种子工具在环回归 (13/13 identical; 冻结映射对账全过; generation/* 零改动)
  10 受限审计结构 + 运行时输出脱敏 (凭据/政策正文标记零出现)

运行: python -m unittest tests.test_mcp_read_tool -v
边界: 全程只读; R-04 真实睡 0.65s 演练 deadline; 不触保留集; 不做 git 状态断言
(全量测试会重写 baseline jsonl 行尾, 仓库级清洁由人工 SOP 检查)。
"""
import hashlib
import json
import os
import unittest

from ci import redaction_scan as scan
from mcptool import case_store
from mcptool import contract as C
from mcptool import run_fixtures as fixtures
from mcptool import run_regression as regression
from mcptool import server as server_mod
from mcptool.client import McpHost, TrustedSession

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEK13_DIR = os.path.join(PROJECT_ROOT, "week-13")
CONTRACT_PATH = os.path.join(WEEK13_DIR, "mcp-read-contract-v1.json")
MANIFEST_PATH = os.path.join(WEEK13_DIR, "mcp-capability-manifest-v1.json")
FIXED_NOW = "2026-10-08T00:00:00Z"
AGENT_SESSION = TrustedSession(
    "northstar-training", "user-zhou-ning", "support_agent")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class McpReadToolTest(unittest.TestCase):
    """第 13 周只读工具验收测试 (十项)。"""

    # ---- 01 合同互锁 ----
    def test_01_contract_manifest_interlock(self):
        contract = _load_json(CONTRACT_PATH)
        self.assertEqual(contract["tool"], C.TOOL_NAME)
        self.assertEqual(contract["version"], C.TOOL_VERSION)
        self.assertEqual(contract["effect"], C.EFFECT)
        self.assertEqual(contract["error_taxonomy"], list(C.ERROR_CODES))
        self.assertEqual(
            contract["arguments_schema"]["properties"]["fields"]["items"]["enum"],
            list(C.FIELD_ENUM))
        self.assertEqual(contract["arguments_schema"]["additional_properties"],
                         "reject")
        self.assertEqual(contract["trusted_context"]["keys"],
                         list(C.TRUSTED_CONTEXT_KEYS))
        self.assertEqual(contract["result_limit"],
                         {"records": C.RESULT_LIMIT_RECORDS,
                          "fields": C.RESULT_LIMIT_FIELDS})
        self.assertEqual(contract["deadline_ms"], C.DEADLINE_MS)
        self.assertEqual(contract["user_safe_messages"], C.SAFE_MESSAGES)
        mapping_in_contract = {
            key: value for key, value in contract["task_type_category_map"].items()
            if key != "note"}
        self.assertEqual(mapping_in_contract, dict(C.TASK_TYPE_TO_CATEGORY))
        manifest = _load_json(MANIFEST_PATH)
        cap = manifest["capability"]
        self.assertEqual(cap["tool"], C.TOOL_NAME)
        self.assertEqual(cap["version"], C.TOOL_VERSION)
        self.assertEqual(cap["effect"], C.EFFECT)
        self.assertEqual(cap["field_enum"], list(C.FIELD_ENUM))
        self.assertEqual(cap["forbidden_fields"], list(C.FORBIDDEN_FIELDS))
        self.assertEqual(cap["allowed_roles"], list(C.ALLOWED_ROLES))
        self.assertEqual(cap["trusted_context_keys"], list(C.TRUSTED_CONTEXT_KEYS))
        self.assertEqual(cap["result_limit"],
                         {"records": C.RESULT_LIMIT_RECORDS,
                          "fields": C.RESULT_LIMIT_FIELDS})
        self.assertEqual(cap["deadline_ms"], C.DEADLINE_MS)
        self.assertEqual(cap["error_taxonomy"], list(C.ERROR_CODES))
        self.assertEqual(cap["external_write_effects"], 0)
        with open(CONTRACT_PATH, "rb") as f:
            self.assertEqual(cap["contract_sha256"],
                             hashlib.sha256(f.read()).hexdigest())

    # ---- 02 R-01 正常路径 ----
    def test_02_r01_happy_path_exactly_three_fields(self):
        result = fixtures.run_r01()
        self.assertTrue(result["pass"], result)
        observed = result["observed"]
        self.assertEqual(observed["status"], "ok")
        self.assertEqual(sorted(observed["context"]), sorted(C.FIELD_ENUM))
        self.assertEqual(observed["record_version"], "case-v7")
        self.assertTrue(observed["read_at"])

    # ---- 03 禁止/未知字段 schema 拒绝 ----
    def test_03_forbidden_and_unknown_fields_rejected(self):
        host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
        for bad_fields in (["task_type", "supervisor_note"],
                           ["customer_name"], ["payment_detail"], ["nickname"]):
            envelope = host.call({"case_id": "NS-1042", "fields": bad_fields})
            self.assertEqual(envelope["error"]["code"], C.ERROR_INVALID_ARGUMENT)
            self.assertEqual(envelope["error"]["message"],
                             C.SAFE_MESSAGES[C.ERROR_INVALID_ARGUMENT])
            self.assertNotIn("records", envelope)
        # 拒绝细节只进受限审计, 用户面只有安全文案
        self.assertTrue(host.audit[0]["reason_typed"].startswith("schema:"))
        # fixtures 层复验 (R-02/X-02)
        self.assertTrue(fixtures.run_r02()["pass"])
        self.assertTrue(fixtures.run_x02()["pass"])

    # ---- 04 R-03 不可区分性 ----
    def test_04_not_available_indistinguishable(self):
        result = fixtures.run_r03()
        self.assertTrue(result["pass"], result)
        observed = result["observed"]
        self.assertEqual(observed["checks"]["user_facing_identical"], True)
        self.assertEqual(len(observed["typed_reasons_restricted"]), 3)
        self.assertEqual(
            len(set(observed["typed_reasons_restricted"].values())), 3)

    # ---- 05 R-04 超时 ----
    def test_05_deadline_exceeded_no_context(self):
        result = fixtures.run_r04()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["observed"]["error"]["code"],
                         C.ERROR_DEADLINE_EXCEEDED)
        self.assertTrue(result["observed"]["elapsed_typed"].startswith("elapsed_ms:"))

    # ---- 06 R-05 超合同结果 ----
    def test_06_contract_violation_whole_reject(self):
        result = fixtures.run_r05()
        self.assertTrue(result["pass"], result)
        observed = result["observed"]
        self.assertEqual(observed["error"]["code"], C.ERROR_CONTRACT_VIOLATION)
        self.assertEqual(observed["security_events_restricted"][0]["violation"],
                         "records_exceed_limit:2>1")

    # ---- 07 X-01 走私 + X-03 版本 ----
    def test_07_identity_smuggling_and_version_mismatch(self):
        result = fixtures.run_x01()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["observed"]["server_calls"], 0)
        self.assertEqual(result["observed"]["error"]["code"],
                         C.ERROR_INVALID_ARGUMENT)
        result = fixtures.run_x03()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["observed"]["error"]["code"],
                         C.ERROR_VERSION_MISMATCH)

    # ---- 08 零写效果 ----
    def test_08_zero_write_effects_and_store_immutable(self):
        before = case_store.snapshot()
        results, summary = fixtures.run_all()
        self.assertEqual(summary["fixtures_total"], 8)
        self.assertEqual(summary["passed"], 8)
        self.assertEqual(summary["total_external_write_effects"], 0)
        self.assertTrue(summary["store_immutable"])
        self.assertEqual(case_store.snapshot(), before)
        for result in results:
            self.assertTrue(result["pass"], result["fixture_id"])
            self.assertEqual(result["external_write_effects"], 0)
        # 设计级断言: 服务端/案例库模块不存在任何写入口
        for module in (server_mod, case_store):
            for name in dir(module):
                if callable(getattr(module, name, None)):
                    for verb in ("write", "create", "update", "delete", "mutate"):
                        self.assertNotIn(verb, name)

    # ---- 09 13 种子工具在环回归 ----
    def test_09_seed_regression_tool_in_loop(self):
        rows, summary = regression.run_all()
        self.assertEqual(summary["seeds_total"], 13)
        self.assertEqual(summary["identical"], 13)
        self.assertEqual(summary["mismatches"], [])
        self.assertEqual(summary["external_write_effects"], 0)
        for row in rows:
            self.assertTrue(row["context_consistent"],
                            f"{row['seed_id']} 上下文映射对账失败: {row}")
            self.assertTrue(row["match"],
                            f"{row['seed_id']} 锁定行为不一致: {row}")

    # ---- 10 受限审计 + 运行时输出脱敏 ----
    def test_10_audit_structure_and_redaction(self):
        host = McpHost(AGENT_SESSION, clock=lambda: FIXED_NOW)
        host.call({"case_id": "NS-1042", "fields": ["task_type"]})
        entry = host.audit[0]
        for key in ("run_id", "tool", "caller", "outcome", "reason_typed",
                    "external_write_effects"):
            self.assertIn(key, entry)
        self.assertEqual(entry["external_write_effects"], 0)
        self.assertEqual(entry["caller"]["role"], "support_agent")
        self.assertEqual(entry["tool"], C.TOOL_NAME)
        # 脱敏: 全部夹具输出的序列化文本不含凭据/政策正文标记 (CI 阶段 3 同源清单)
        results, _ = fixtures.run_all()
        emitted = json.dumps(results, ensure_ascii=False)
        for token in scan.SECRET_MARKERS_IN_EMISSION:
            self.assertNotIn(token, emitted)
        for token in scan.BODY_MARKERS_IN_EMISSION:
            self.assertNotIn(token, emitted)


if __name__ == "__main__":
    unittest.main()


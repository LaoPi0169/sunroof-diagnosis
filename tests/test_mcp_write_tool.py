"""
审批写工具测试 (Week 14) —— 回链 specs/mission-week-14.md 验收标准

覆盖十二项:
  01 合同/常量/策略三方互锁 (工具三元组/版本/效果/错误十类/队列枚举/绑定字段/审批策略值/v2 合同 sha256)
  02 无副作用预览: preview 后模拟服务计数 0 + 只读库快照不变 + 失控预览非零计数被 Host 硬门拦截
  03 schema 封闭: 走私身份键/未知字段/approval_id 走私/非法原因码/未知队列/空引用 全拒 (未到服务端)
  04 W-01 无审批 -> approval_required, 0 效果
  05 W-02 审批过期 -> approval_expired, 0 效果
  06 W-03 改队列 -> approval_mismatch, 0 效果; 同集合乱序引用经规范化不构成不匹配
  07 W-04 相同幂等键重放 x10 -> 同一 SIM-5001, 模拟服务总数恒 1
  08 W-05 用户拒绝 -> approval_denied 正常终态 + 角色不符/终态缺失策略拒绝, 0 效果
  09 用尽审批 -> approval_required; action_completed 封套独立核验字段完备
  10 ticket.get 独立核验 + 不存在/跨租户 not_available 用户面不可区分
  11 写夹具 5/5 (效果增量恰 1) + 只读夹具 8/8 不受扰 + 13 种子回归 13/13
  12 脱敏: 运行时输出无凭据/正文标记 + 用户面封套无批准载荷 + 受限审计含细分原因

运行: python -m unittest tests.test_mcp_write_tool -v
边界: 全程隔离模拟服务 (每用例独立栈); 只读 case_store 零搅动; 不触保留集;
不做 git 状态断言 (全量测试后 baseline jsonl 行尾由人工 SOP 恢复)。
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
from mcptool import write_contract as W
from mcptool.approval import ApprovalService
from mcptool.client import McpHost, TrustedSession
from mcptool.policy import WritePolicy
from mcptool.ticket_service import TicketService

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEEK15_DIR = os.path.join(PROJECT_ROOT, "week-15")
# 第 15 周: 互锁基准演进为 v2 合同 (撤销/能力清单门/断连对账; 错误八类 -> 十类);
# week-14 v1 冻结物一字节不动, 其摘要由 tests/test_mcp_adversarial.py 锁定。
CONTRACT_PATH = os.path.join(WEEK15_DIR, "ticket-write-contract-v2.json")
FIXED_NOW = "2026-10-08T00:00:00Z"
AGENT_SESSION = TrustedSession(
    "northstar-training", "user-zhou-ning", "support_agent")
OTHER_TENANT_SESSION = TrustedSession(
    "partner-tenant-co", "user-other-tenant", "support_agent")
ARGS = {
    "case_id": "NS-1042",
    "reason_code": "policy_conflict",             # 小写输入: 规范化演示
    "source_refs": ["upgrade-notice-v4", " upgrade-east-v2"],
    "target_queue": "Billing-Supervisor-Review",   # 大小写输入: 规范化演示
}
RUN_ID = "run-w14-001"
APPROVAL_ID = "approval-w14-001-1"


class _FakeCountService:
    """失控预览夹具: 只会报一个错误的外部计数 (预览后仍有 5 张工单)。"""

    def ticket_count(self):
        return 5

    def get_ticket(self, _ticket_id):
        return None

    def find_id_by_idempotency_key(self, _key):
        return None


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fresh_stack(session=AGENT_SESSION):
    tickets = TicketService()
    approvals = ApprovalService()
    policy = WritePolicy()
    host = McpHost(session, clock=lambda: FIXED_NOW, ticket_service=tickets,
                   approval_service=approvals, policy=policy)
    return host, tickets, approvals, policy


def _approve_exact(approvals, policy, *, maximum_uses=None):
    canonical, err = W.validate_write_arguments(ARGS)
    assert err is None, err
    kwargs = {} if maximum_uses is None else {"maximum_uses": maximum_uses}
    return approvals.issue(
        approval_id=APPROVAL_ID, requester=AGENT_SESSION.user_id,
        tenant_id=AGENT_SESSION.tenant_id, run_id=RUN_ID, tool=W.TOOL_CREATE,
        tool_version=W.TOOL_VERSION, approved_parameters=canonical,
        resource_scope=W.resource_scope("NS-1042"),
        policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW, **kwargs)


def _approve_and_execute(host, tickets, approvals, policy):
    host.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID, rag_terminal="escalate")
    _approve_exact(approvals, policy)
    return host.call_write(W.TOOL_CREATE, ARGS, run_id=RUN_ID,
                           approval_id=APPROVAL_ID, rag_terminal="escalate")


class McpWriteToolTest(unittest.TestCase):
    """第 14 周审批写工具验收测试 (十二项)。"""

    # ---- 01 合同互锁 ----
    def test_01_contract_constants_policy_interlock(self):
        contract = _load_json(CONTRACT_PATH)
        self.assertEqual(contract["tools"]["preview"], W.TOOL_PREVIEW)
        self.assertEqual(contract["tools"]["create"], W.TOOL_CREATE)
        self.assertEqual(contract["tools"]["verify"], W.TOOL_GET)
        self.assertEqual(contract["version"], W.TOOL_VERSION)
        self.assertEqual(contract["effect"], W.EFFECT)
        self.assertEqual(contract["effect_class"], W.EFFECT_CLASS)
        self.assertEqual(contract["error_taxonomy"], list(W.ERROR_CODES))
        self.assertEqual(contract["user_safe_messages"], W.SAFE_MESSAGES)
        schema = contract["arguments_schema"]
        self.assertEqual(schema["additional_properties"], "reject")
        self.assertEqual(schema["properties"]["reason_code"]["enum"],
                         list(W.REASON_CODES))
        self.assertEqual(schema["properties"]["target_queue"]["enum"],
                         list(W.QUEUE_SYNTAX_ENUM))
        self.assertEqual(schema["properties"]["source_refs"]["min_items"],
                         W.SOURCE_REFS_MIN)
        self.assertEqual(schema["properties"]["source_refs"]["max_items"],
                         W.SOURCE_REFS_MAX)
        self.assertEqual(contract["trusted_context"]["keys"],
                         list(C.TRUSTED_CONTEXT_KEYS))
        self.assertEqual(contract["deadline_ms"], W.DEADLINE_MS)
        self.assertEqual(contract["result_limit"]["tickets_per_approval"],
                         W.RESULT_LIMIT_TICKETS_PER_APPROVAL)
        self.assertEqual(contract["execution"]["idempotency_key_format"],
                         "{run_id}:ticket.create:{tool_version}")
        self.assertEqual(W.idempotency_key("run-x"), "run-x:ticket.create:1")
        self.assertEqual(W.resource_scope("NS-1042"), "case:NS-1042")
        # 审批策略值三方互锁 (合同 JSON <-> 常量 <-> policy 模块)
        policy_cfg = contract["approval_policy"]
        self.assertEqual(policy_cfg["policy_version"], WritePolicy.POLICY_VERSION)
        self.assertEqual(policy_cfg["ttl_seconds"],
                         WritePolicy.APPROVAL_TTL_SECONDS)
        self.assertEqual(policy_cfg["maximum_uses"],
                         WritePolicy.MAX_USES_PER_APPROVAL)
        self.assertEqual(policy_cfg["allowed_queues"],
                         list(WritePolicy.ALLOWED_QUEUES))
        self.assertEqual(policy_cfg["allowed_roles"],
                         list(WritePolicy.ALLOWED_ROLES))
        self.assertEqual(policy_cfg["required_rag_terminal"],
                         WritePolicy.REQUIRED_RAG_TERMINAL)
        self.assertEqual(policy_cfg["min_source_refs_for_conflict"],
                         WritePolicy.MIN_REFS_FOR_CONFLICT)
        self.assertEqual(policy_cfg["binding_fields"],
                         list(W.APPROVAL_BINDING_FIELDS)
                         + ["maximum_uses", "expires_at"])
        # 冻结互锁: 合同文件字节级 sha256 与代码常量一致
        with open(CONTRACT_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(digest, W.CONTRACT_SHA256)

    # ---- 02 无副作用预览 ----
    def test_02_preview_zero_effect_and_rogue_counter_rejected(self):
        before = case_store.snapshot()
        host, tickets, _approvals, _policy = _fresh_stack()
        preview = host.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID,
                                  rag_terminal="escalate")
        self.assertEqual(preview["status"], W.STATUS_PREVIEW)
        self.assertEqual(preview["effect"], W.EFFECT)
        self.assertEqual(preview["external_ticket_count"], 0)
        self.assertEqual(tickets.ticket_count(), 0)
        canonical = preview["canonical_parameters"]
        self.assertEqual(canonical["reason_code"], "POLICY_CONFLICT")
        self.assertEqual(canonical["source_refs"],
                         ["upgrade-east-v2", "upgrade-notice-v4"])
        self.assertEqual(canonical["target_queue"], "billing-supervisor-review")
        self.assertEqual(case_store.snapshot(), before)
        # 失控服务端: 预览封套声称外部计数 5 -> Host 硬门整体拒绝 + 安全事件
        rogue_host = McpHost(
            AGENT_SESSION, clock=lambda: FIXED_NOW,
            ticket_service=_FakeCountService(),
            approval_service=ApprovalService(), policy=WritePolicy())
        envelope = rogue_host.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID,
                                         rag_terminal="escalate")
        self.assertEqual(envelope["error"]["code"], W.ERROR_CONTRACT_VIOLATION)
        self.assertEqual(rogue_host.security_events[0]["violation"],
                         "preview_nonzero_effects")

    # ---- 03 schema 封闭 ----
    def test_03_schema_rejections_never_reach_server(self):
        bad_cases = {
            "smuggled_tenant": dict(ARGS, tenant_id="northstar-training"),
            "smuggled_role": dict(ARGS, role="admin"),
            "extra_metadata": dict(ARGS, metadata={"note": "urgent"}),
            "smuggled_approval_id": dict(ARGS, approval_id="approval-x"),
            "bad_reason": dict(ARGS, reason_code="REFUND_APPROVAL"),
            "unknown_queue": dict(ARGS, target_queue="ceo-queue"),
            "empty_refs": dict(ARGS, source_refs=[]),
            "blank_ref": dict(ARGS, source_refs=["upgrade-east-v2", "  "]),
            "dup_refs": dict(ARGS, source_refs=["upgrade-east-v2",
                                                "upgrade-east-v2"]),
            "too_many_refs": dict(ARGS, source_refs=["a", "b", "c"]),
        }
        for label, arguments in bad_cases.items():
            with self.subTest(case=label):
                host, _tickets, _approvals, _policy = _fresh_stack()
                calls_before = host.server_calls
                envelope = host.call_write(W.TOOL_PREVIEW, arguments,
                                           run_id=RUN_ID,
                                           rag_terminal="escalate")
                self.assertEqual(envelope["error"]["code"],
                                 W.ERROR_INVALID_ARGUMENT, label)
                self.assertEqual(host.server_calls, calls_before, label)
                self.assertTrue(
                    host.audit[-1]["reason_typed"].startswith("schema:"), label)

    # ---- 04 W-01 无审批 ----
    def test_04_w01_execute_without_approval(self):
        result = fixtures.run_w01()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["external_write_effects"], 0)
        self.assertEqual(result["observed"]["error"]["code"],
                         W.ERROR_APPROVAL_REQUIRED)

    # ---- 05 W-02 审批过期 ----
    def test_05_w02_expired_approval(self):
        result = fixtures.run_w02()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["external_write_effects"], 0)
        self.assertEqual(result["observed"]["error"]["code"],
                         W.ERROR_APPROVAL_EXPIRED)
        # 细分原因只进受限审计, 用户面封套不含有效期细节
        self.assertNotIn("expires_at", json.dumps(result["observed"]))

    # ---- 06 W-03 参数不匹配 ----
    def test_06_w03_binding_mismatch_and_canonical_equivalence(self):
        result = fixtures.run_w03()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["external_write_effects"], 0)
        self.assertEqual(result["observed"]["error"]["code"],
                         W.ERROR_APPROVAL_MISMATCH)
        self.assertEqual(result["observed"]["checks"]["binding_reason"], True)
        # 规范化等价: 同集合不同顺序/大小写 -> 同一规范参数, 不构成不匹配
        host, tickets, approvals, policy = _fresh_stack()
        args_a = {"case_id": "NS-1042", "reason_code": "POLICY_CONFLICT",
                  "source_refs": ["upgrade-notice-v4", "upgrade-east-v2"],
                  "target_queue": "billing-supervisor-review"}
        args_b = {"case_id": " NS-1042 ", "reason_code": "policy_conflict",
                  "source_refs": ["upgrade-east-v2", "upgrade-notice-v4"],
                  "target_queue": "Billing-Supervisor-Review"}
        host.call_write(W.TOOL_PREVIEW, args_a, run_id=RUN_ID,
                        rag_terminal="escalate")
        canonical, _ = W.validate_write_arguments(args_a)
        approvals.issue(
            approval_id=APPROVAL_ID, requester=AGENT_SESSION.user_id,
            tenant_id=AGENT_SESSION.tenant_id, run_id=RUN_ID,
            tool=W.TOOL_CREATE, tool_version=W.TOOL_VERSION,
            approved_parameters=canonical,
            resource_scope=W.resource_scope("NS-1042"),
            policy_version=policy.POLICY_VERSION, issued_at=FIXED_NOW)
        envelope = host.call_write(W.TOOL_CREATE, args_b, run_id=RUN_ID,
                                   approval_id=APPROVAL_ID,
                                   rag_terminal="escalate")
        self.assertEqual(envelope["status"], W.STATUS_COMPLETED)
        self.assertEqual(tickets.ticket_count(), 1)

    # ---- 07 W-04 幂等重放 ----
    def test_07_w04_idempotent_replay_ten_times(self):
        result = fixtures.run_w04()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["external_write_effects"], 1)
        first = result["observed"]["first"]
        self.assertEqual(first["status"], W.STATUS_COMPLETED)
        self.assertEqual(first["ticket_id"], "SIM-5001")
        self.assertEqual(first["idempotency_key"],
                         "run-w14-001:ticket.create:1")
        self.assertEqual(first["verified_by"], "ticket.get")
        self.assertEqual(first["ticket_state"], "open")

    # ---- 08 W-05 用户拒绝 + 策略拒绝 ----
    def test_08_w05_denial_terminal_and_policy_denial(self):
        result = fixtures.run_w05()
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["external_write_effects"], 0)
        self.assertEqual(result["observed"]["denial_decision"], "denied")
        self.assertEqual(result["observed"]["error"]["code"],
                         W.ERROR_APPROVAL_REQUIRED)
        # 策略拒绝 (角色不符) 也是 approval_denied 正常终态, 0 效果
        supervisor = TrustedSession("northstar-training",
                                    "user-gao-supervisor", "supervisor")
        host, tickets, _approvals, _policy = _fresh_stack(supervisor)
        envelope = host.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID,
                                   rag_terminal="escalate")
        self.assertEqual(envelope["status"], W.STATUS_DENIED)
        self.assertEqual(tickets.ticket_count(), 0)
        self.assertTrue(host.audit[-1]["reason_typed"].startswith(
            "policy_denied:policy_role_not_allowed"))
        # RAG 终态缺失 (未升级) 同样策略拒绝 —— 模型自述不算升级依据
        host2, _tickets2, _approvals2, _policy2 = _fresh_stack()
        envelope = host2.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID,
                                    rag_terminal="answered")
        self.assertEqual(envelope["status"], W.STATUS_DENIED)
        self.assertIn("policy_rag_terminal_not_escalate",
                      host2.audit[-1]["reason_typed"])

    # ---- 09 用尽审批 + 完成封套核验 ----
    def test_09_exhausted_approval_and_completed_envelope(self):
        host, tickets, approvals, policy = _fresh_stack()
        host.call_write(W.TOOL_PREVIEW, ARGS, run_id=RUN_ID,
                        rag_terminal="escalate")
        _approve_exact(approvals, policy, maximum_uses=0)
        envelope = host.call_write(W.TOOL_CREATE, ARGS, run_id=RUN_ID,
                                   approval_id=APPROVAL_ID,
                                   rag_terminal="escalate")
        self.assertEqual(envelope["error"]["code"], W.ERROR_APPROVAL_REQUIRED)
        self.assertEqual(host.audit[-1]["reason_typed"], "approval_exhausted")
        self.assertEqual(tickets.ticket_count(), 0)
        # 成功路径的完成封套字段完备 (独立核验字段由合同约定)
        host2, tickets2, approvals2, policy2 = _fresh_stack()
        completed = _approve_and_execute(host2, tickets2, approvals2, policy2)
        for key in ("status", "tool_version", "effect", "ticket_id",
                    "idempotency_key", "ticket_state", "verified_by",
                    "external_ticket_count"):
            self.assertIn(key, completed)
        self.assertEqual(completed["verified_by"], "ticket.get")
        self.assertEqual(completed["external_ticket_count"], 1)
        self.assertEqual(completed["effect"], W.EFFECT)

    # ---- 10 ticket.get 独立核验 ----
    def test_10_ticket_get_verification_indistinguishable_denials(self):
        host, tickets, approvals, policy = _fresh_stack()
        completed = _approve_and_execute(host, tickets, approvals, policy)
        ticket_id = completed["ticket_id"]
        verify = host.call_write(W.TOOL_GET, {"ticket_id": ticket_id},
                                 run_id=RUN_ID)
        self.assertEqual(verify["status"], "ok")
        record = verify["records"][0]
        self.assertEqual(record["ticket_id"], ticket_id)
        self.assertEqual(record["state"], "open")
        self.assertEqual(record["case_id"], "NS-1042")
        self.assertEqual(record["reason_code"], "POLICY_CONFLICT")
        self.assertEqual(record["target_queue"], "billing-supervisor-review")
        self.assertEqual(verify["external_ticket_count"], 1)
        # 不存在 vs 跨租户: 用户面逐字节一致 (不暴露工单存在性)
        missing = host.call_write(W.TOOL_GET, {"ticket_id": "SIM-9999"},
                                  run_id=RUN_ID)
        other = McpHost(OTHER_TENANT_SESSION, clock=lambda: FIXED_NOW,
                        ticket_service=tickets,
                        approval_service=ApprovalService(),
                        policy=WritePolicy())
        cross = other.call_write(W.TOOL_GET, {"ticket_id": ticket_id},
                                 run_id=RUN_ID)
        self.assertEqual(missing["error"]["code"], W.ERROR_NOT_AVAILABLE)
        self.assertEqual(json.dumps(missing, sort_keys=True),
                         json.dumps(cross, sort_keys=True))
        # 封闭 schema: 多键/非字符串拒绝
        bad = host.call_write(W.TOOL_GET, {"ticket_id": ticket_id, "x": 1},
                              run_id=RUN_ID)
        self.assertEqual(bad["error"]["code"], W.ERROR_INVALID_ARGUMENT)

    # ---- 11 夹具与回归总对账 ----
    def test_11_write_fixtures_read_fixtures_and_regression(self):
        before = case_store.snapshot()
        write_results, write_summary = fixtures.run_write_all()
        self.assertEqual(write_summary["fixtures_total"], 5)
        self.assertEqual(write_summary["passed"], 5)
        self.assertEqual(write_summary["expected_new_external_effects"], 1)
        self.assertEqual(write_summary["observed_new_external_effects"], 1)
        self.assertTrue(write_summary["read_store_immutable"])
        self.assertEqual([r["external_write_effects"] for r in write_results],
                         [0, 0, 0, 1, 0])
        _read_results, read_summary = fixtures.run_all()
        self.assertEqual(read_summary["passed"], 8)
        self.assertEqual(read_summary["total_external_write_effects"], 0)
        self.assertTrue(read_summary["store_immutable"])
        self.assertEqual(case_store.snapshot(), before)
        _rows, reg_summary = regression.run_all()
        self.assertEqual(reg_summary["seeds_total"], 13)
        self.assertEqual(reg_summary["identical"], 13)
        self.assertEqual(reg_summary["external_write_effects"], 0)

    # ---- 12 脱敏 + 受限审计 ----
    def test_12_redaction_and_restricted_audit(self):
        write_results, _summary = fixtures.run_write_all()
        emitted = json.dumps(write_results, ensure_ascii=False)
        for token in scan.SECRET_MARKERS_IN_EMISSION:
            self.assertNotIn(token, emitted)
        for token in scan.BODY_MARKERS_IN_EMISSION:
            self.assertNotIn(token, emitted)
        # 用户面封套不含批准载荷/审批内部细节 (载荷只存审批记录, 记录属受限面)
        self.assertNotIn("approved_parameters", emitted)
        self.assertNotIn("policy_version", emitted)
        # CI STAGE-3 同源扫描: 39 文件全绿 (第 15 周能力清单模块 + 对抗工作台入列)
        report = scan.scan()
        self.assertTrue(report["passed"], report["violations"])
        self.assertGreaterEqual(report["scanned_files"], 39)
        # 受限审计含细分原因与效果增量 (审计不等于普通日志; 引用须标注 restricted)
        host, tickets, approvals, policy = _fresh_stack()
        _approve_and_execute(host, tickets, approvals, policy)
        entry = host.audit[-1]
        for key in ("run_id", "tool", "caller", "approval_id", "outcome",
                    "reason_typed", "external_write_effects"):
            self.assertIn(key, entry)
        self.assertEqual(entry["external_write_effects"], 1)
        self.assertEqual(entry["reason_typed"], "ok:write_verified")


if __name__ == "__main__":
    unittest.main()
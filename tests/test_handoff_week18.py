"""Week 18 值守交接工作台测试 —— 角色隔离 × 封存故障 F-18.1/F-18.2 × 完整版本包回滚 × 四+1 恢复复验。

回链: week-18/sealed-fault-pack-v1.md + week-18/ops-handoff-pack-v1.md +
week-18/full-release-manifest-v2.json + specs/business-background.md 4006-4148。

核心断言 (对应交接验收十条的自动化部分):
  1) 业务用户 (免登录) 调任何管理接口一律 403 admin_required —— 服务端强制权限;
  2) 管理员必须登录; 口令只比对, 绝不进任何状态/审计/响应 (动态断言零交叉);
  3) F-18.1 (MCP 写通道不可达): 写停 action_preview 效果 0 审批不消耗, 读不受阻;
  4) F-18.2 (配置回归): 正常问答引用废止口径版本 -> 回滚完整版本包 -> 引用恢复
     现行版本 —— 关开关修不了配置回归;
  5) 恢复 = 四+1 条任务路径复验 (正常/拒答/升级/写入/幂等对账), 只看健康接口不算;
  6) 清单 v2 六类绑定齐全, 不含任何秘密。
教学模拟边界: 单进程单会话; 每用例先重置回交接初始态 (rc-18.1, 已埋 F-18.2)。
"""
import json
import os
import unittest

from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_V2 = os.path.join(PROJECT_ROOT, "week-18", "full-release-manifest-v2.json")

OPERATOR = "值守-甲"
PASSCODE = "oncall-w18"  # 教学模拟值守口令 (与 workbench_handoff/app.py 一致)
REASON_ISOLATE = "演练: 观察到写通道全失败, 按手册隔离 MCP 写通道"
REASON_ROLLBACK = "演练: 现行口径引用了废止版本, 按手册回滚完整版本包"


class HandoffBase(unittest.TestCase):
    """每用例: 登录 -> 重置回交接初始态 -> 再登录拿新 token。"""

    def setUp(self):
        from workbench_handoff.app import app
        self.client = TestClient(app)
        first = self.client.post("/api/admin/login", json={
            "operator": OPERATOR, "passcode": PASSCODE}).json()["token"]
        self.client.post("/api/admin/reset", headers={"X-Session-Token": first})
        self.token = self.client.post("/api/admin/login", json={
            "operator": OPERATOR, "passcode": PASSCODE}).json()["token"]
        self.admin = {"X-Session-Token": self.token}

    # ---- 常用动作 ----
    def _walk_write(self):
        self.client.post("/api/user/preview")
        self.client.post("/api/user/decide", json={"decision": "approve"})
        return self.client.post("/api/user/execute").json()

    def _qa(self, qid):
        return self.client.post("/api/user/ask", json={"question_id": qid}).json()


class TestRoleIsolation(HandoffBase):
    """角色隔离: 配置只属于登录后的管理员; 业务用户只能做权限内操作。"""

    def test_01_business_cannot_reach_admin_apis(self):
        for method, url in (("GET", "/api/admin/state"),
                            ("POST", "/api/admin/fault"),
                            ("POST", "/api/admin/switch"),
                            ("POST", "/api/admin/package"),
                            ("POST", "/api/admin/recovery-check"),
                            ("POST", "/api/admin/reset"),
                            ("GET", "/api/admin/trace/some-id")):
            resp = getattr(self.client, method.lower())(url)
            self.assertEqual(resp.status_code, 403, url)
            self.assertEqual(resp.json()["detail"], "admin_required")
        # 伪 token 同样 403
        bad = {"X-Session-Token": "sess-forged-by-attacker"}
        self.assertEqual(
            self.client.get("/api/admin/state", headers=bad).status_code, 403)

    def test_02_admin_login_wrong_passcode_rejected(self):
        resp = self.client.post("/api/admin/login",
                                json={"operator": OPERATOR, "passcode": "wrong"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "bad_credentials")
        ok = self.client.post("/api/admin/login",
                              json={"operator": OPERATOR, "passcode": PASSCODE})
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertEqual(body["role"], "admin")
        self.assertTrue(body["token"].startswith("sess-"))

    def test_03_passcode_and_token_never_leak(self):
        self.client.post("/api/admin/login",
                         json={"operator": OPERATOR, "passcode": "wrong"})
        user_state = json.dumps(self.client.get("/api/user/state").json(),
                                ensure_ascii=False)
        admin_state = json.dumps(
            self.client.get("/api/admin/state", headers=self.admin).json(),
            ensure_ascii=False)
        for blob in (user_state, admin_state):
            self.assertNotIn(PASSCODE, blob)          # 口令零出现
            self.assertNotIn(self.token, blob)        # token 不回显不传播
            self.assertNotIn("token_agent_valid", blob)
            self.assertNotIn("token_supervisor_secret", blob)
        # 登录审计只有代号与结果
        audit = self.client.get("/api/admin/state",
                                headers=self.admin).json()["login_audit"]
        self.assertTrue(all("passcode" not in json.dumps(e) for e in audit))
        self.assertTrue(any(e["event"] == "login_failed" for e in audit))
        self.assertTrue(any(e["event"] == "login_ok" for e in audit))

    def test_04_user_state_exposes_no_config_fields(self):
        blob = json.dumps(self.client.get("/api/user/state").json(),
                          ensure_ascii=False)
        for forbidden_key in ("switch_status", "switch_audit", "package_audit",
                              "fault_audit", "login_audit", "qa_binding",
                              "release_manifest", "recovery", "sessions"):
            self.assertNotIn(forbidden_key, blob)

    def test_05_business_scope_states_config_is_admin_only(self):
        scope = self.client.get("/api/user/state").json()["service_scope"]
        self.assertTrue(any("仅管理员登录后可用" in item
                            for item in scope["forbidden"]))
        self.assertTrue(any("人工复核" in item or "升级" in item
                            for item in scope["conditional"]))
        self.assertTrue(any("预览" in item for item in scope["allowed"]))

class TestSealedFaultF181(HandoffBase):
    """F-18.1: MCP 写通道不可达 -> 开关不回滚; 写降级, 读不受阻, 效果 0。"""

    def test_06_injection_degrades_write_only(self):
        self.client.post("/api/admin/fault", json={"action": "inject"},
                         headers=self.admin)
        result = self._walk_write()
        envelope = result["envelope"]
        self.assertEqual(envelope["status"], "action_preview")
        self.assertTrue(envelope["degraded"])
        self.assertEqual(envelope["reason_code"], "mcp_unavailable")
        self.assertEqual(result["state"]["effects_total"], 0)
        # 读路径不受阻 (手册 4034: 预览/查询不受阻)
        qa = self._qa("q-billing-east")
        self.assertEqual(qa["status"], "answered")
        # 一次用户动作 = 恰一次探测, 编排绝不循环重试
        from workbench_handoff import app as appmod
        self.assertEqual(appmod._STATE["probe"].attempts, 1)

    def test_07_degraded_blocks_new_preview_until_retry(self):
        self.client.post("/api/admin/fault", json={"action": "inject"},
                         headers=self.admin)
        self._walk_write()
        resp = self.client.post("/api/user/preview")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "degraded_task_pending")

    def test_08_clear_fault_then_retry_restores_exactly_one_effect(self):
        self.client.post("/api/admin/fault", json={"action": "inject"},
                         headers=self.admin)
        self._walk_write()
        self.client.post("/api/admin/fault", json={"action": "clear"},
                         headers=self.admin)
        retry = self.client.post("/api/user/retry").json()
        self.assertEqual(retry["envelope"]["status"], "action_completed")
        self.assertFalse(retry["envelope"].get("degraded", False))
        self.assertEqual(retry["state"]["effects_total"], 1)
        replay = self.client.post("/api/user/replay").json()
        self.assertTrue(replay["effect_count_unchanged"])
        self.assertEqual(retry["state"]["effects_total"], 1)

    def test_09_switch_mcp_off_same_terminal_with_audit(self):
        resp = self.client.post("/api/admin/switch",
                                json={"action": "set", "reason": REASON_ISOLATE},
                                headers=self.admin)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["degraded"]["mcp_off_active"])
        result = self._walk_write()
        self.assertEqual(result["envelope"]["status"], "action_preview")
        self.assertEqual(result["envelope"]["reason_code"], "mcp_unavailable")
        audit = resp.json()["state"]["switch_audit"]
        self.assertTrue(any(e["event"] == "opened" and e["operator"] == OPERATOR
                            and e["reason"] == REASON_ISOLATE for e in audit))
        clear = self.client.post("/api/admin/switch",
                                 json={"action": "clear", "reason": "按批准流程复位"},
                                 headers=self.admin)
        self.assertEqual(clear.status_code, 200)

    def test_10_switch_without_reason_rejected(self):
        resp = self.client.post("/api/admin/switch",
                                json={"action": "set", "reason": "  "},
                                headers=self.admin)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "reason_required")

class TestSealedFaultF182(HandoffBase):
    """F-18.2: 配置回归 -> 回滚完整版本包 (不是关开关); 恢复 = 四+1 复验。"""

    def test_11_initial_state_is_regression_package(self):
        st = self.client.get("/api/admin/state", headers=self.admin).json()
        self.assertEqual(st["qa_binding"]["active_package"], "rc-18.1")
        self.assertFalse(st["qa_binding"]["known_good"])

    def test_12_regression_symptom_visible_in_normal_qa(self):
        qa = self._qa("q-billing-east")
        self.assertEqual(qa["status"], "answered")
        self.assertEqual(qa["citation"]["document_id"], "billing-general-v1")
        self.assertFalse(qa["citation"]["current"])

    def test_13_refusal_and_escalation_immune_to_regression(self):
        self.assertEqual(self._qa("q-out-of-scope")["status"], "abstained")
        self.assertEqual(self._qa("q-restricted")["status"], "escalated")

    def test_14_recovery_check_fails_under_regression_and_fault(self):
        self.client.post("/api/admin/fault", json={"action": "inject"},
                         headers=self.admin)
        rec = self.client.post("/api/admin/recovery-check",
                               headers=self.admin).json()["recovery"]
        self.assertEqual(rec["overall"], "fail")
        self.assertFalse(rec["rows"][0]["pass"])   # 正常查询引用废止版本
        self.assertTrue(rec["rows"][1]["pass"])    # 拒答不受影响
        self.assertTrue(rec["rows"][2]["pass"])    # 升级不受影响
        self.assertFalse(rec["rows"][3]["pass"])   # 写入停在待办预览
        self.assertTrue("action_preview" in rec["rows"][3]["observed"])

    def test_15_rollback_requires_reason_then_restores_current_binding(self):
        no_reason = self.client.post("/api/admin/package",
                                     json={"action": "rollback", "reason": " "},
                                     headers=self.admin)
        self.assertEqual(no_reason.status_code, 400)
        self.assertEqual(no_reason.json()["error"], "reason_required")
        rolled = self.client.post(
            "/api/admin/package",
            json={"action": "rollback", "reason": REASON_ROLLBACK},
            headers=self.admin).json()
        self.assertEqual(rolled["event"]["event"], "rolled_back")
        self.assertEqual(rolled["event"]["to"], "rc-18.0")
        self.assertEqual(rolled["qa_binding"]["policy_active_binding"],
                         "billing-general-v3")
        qa = self._qa("q-billing-east")
        self.assertEqual(qa["citation"]["document_id"], "billing-general-v3")
        self.assertTrue(qa["citation"]["current"])
        audit = rolled["state"]["package_audit"]
        self.assertTrue(any(e["event"] == "rolled_back"
                            and e["reason"] == REASON_ROLLBACK for e in audit))

    def test_16_recovery_check_passes_after_rollback(self):
        self.client.post("/api/admin/package",
                         json={"action": "rollback", "reason": REASON_ROLLBACK},
                         headers=self.admin)
        rec = self.client.post("/api/admin/recovery-check",
                               headers=self.admin).json()["recovery"]
        self.assertEqual(rec["overall"], "pass")
        self.assertEqual(len(rec["rows"]), 5)
        self.assertTrue(all(r["pass"] for r in rec["rows"]))

    def test_17_reseed_regression_for_next_round(self):
        self.client.post("/api/admin/package",
                         json={"action": "rollback", "reason": REASON_ROLLBACK},
                         headers=self.admin)
        again = self.client.post(
            "/api/admin/package",
            json={"action": "activate", "reason": "演练重埋: 下一轮值守复训"},
            headers=self.admin).json()
        self.assertEqual(again["event"]["event"], "regression_seeded")
        self.assertEqual(again["qa_binding"]["active_package"], "rc-18.1")

class TestWritePathAndContract(HandoffBase):
    """写路径正常语义 (受控确认/幂等) 在 8018 上不被交接改动稀释。"""

    def test_18_write_happy_path_and_replay_no_double_effect(self):
        result = self._walk_write()
        self.assertEqual(result["envelope"]["status"], "action_completed")
        self.assertEqual(result["state"]["effects_total"], 1)
        replay = self.client.post("/api/user/replay").json()
        self.assertTrue(replay["effect_count_unchanged"])
        self.assertEqual(replay["ticket_total"], 1)
        self.assertEqual(replay["state"]["effects_total"], 1)

    def test_19_denial_is_terminal_zero_effect(self):
        self.client.post("/api/user/preview")
        decide = self.client.post("/api/user/decide",
                                  json={"decision": "deny"}).json()
        self.assertEqual(decide["decision_effective"], "denied")
        resp = self.client.post("/api/user/execute")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "not_ready")
        self.assertEqual(
            self.client.get("/api/user/state").json()["effects_total"], 0)

    def test_20_execute_requires_prior_approval(self):
        self.client.post("/api/user/preview")
        resp = self.client.post("/api/user/execute")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "not_ready")


class TestManifestAndRedaction(unittest.TestCase):
    """清单 v2 六类绑定 + 脱敏扫描 (静态范围 50 文件)。"""

    def test_21_manifest_v2_six_bindings(self):
        with open(MANIFEST_V2, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["manifest_version"], 2)
        self.assertEqual(
            set(manifest["bindings"].keys()),
            {"code", "schema", "corpus_index", "prompts_generation",
             "policy_tool_schema", "env_config"})
        env = manifest["bindings"]["env_config"]
        self.assertEqual(env["sha256"],
                         "625f1159104457abf77e656be20cce387887cbccc0736c07e65a57ec19ecb7d1")
        self.assertEqual(env["policy_active_mapping_known_good"].startswith(
            "billing-general-v3"), True)
        self.assertEqual(
            manifest["policy_active_mapping"]["known_good"],
            "billing-general-v3")
        self.assertFalse(manifest["packages"]["rc-18.1"]["known_good"])
        self.assertTrue(manifest["packages"]["rc-18.0"]["known_good"])
        self.assertNotIn("token_agent_valid", json.dumps(manifest))
        self.assertNotIn("token_supervisor_secret", json.dumps(manifest))
        self.assertNotIn("oncall-w18", json.dumps(manifest))

    def test_22_workbench_redaction_scan_pass(self):
        spans = []
        from workbench_handoff.app import _STATE
        for tid in _STATE["task_order"]:
            spans.extend(_STATE["traces"][tid].spans)
        from mcptool.telemetry import scan_spans_for_violations
        self.assertEqual(scan_spans_for_violations(spans), [])

    def test_23_static_redaction_scope_includes_workbench_handoff(self):
        from ci.redaction_scan import SCOPE_FILES, scan
        self.assertIn(os.path.join("workbench_handoff", "app.py"), SCOPE_FILES)
        self.assertIn(os.path.join("workbench_handoff", "__init__.py"),
                      SCOPE_FILES)
        report = scan()
        self.assertEqual(report["scanned_files"], 50)
        self.assertTrue(report["passed"], report["violations"])


if __name__ == "__main__":
    unittest.main()




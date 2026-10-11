"""
值守交接工作台测试 (Week 18) —— 回链 specs/mission-week-18.md 验收门。

  01 双角色隔离: 管理面未登录/伪口令一律 403; 口令/session 在任何响应零出现
  02 业务全链路: pqe 提交 -> approval_required -> (管理员批准) -> 执行 1 效果 -> 重放无第二效果
  03 主管直写被拒: supervisor 发起写 = 冻结核 unauthorized (forbidden, 0 效果)
  04 降级演练: MCP_OFF -> action_preview 0 效果 (审批不消耗) -> 恢复续跑 1 效果
  05 任务追踪: 九阶段 span 还原 + 终态分类 allowed
  06 工程门: 静态扫描范围含工作台文件; 索引页可达

运行: python -m unittest tests.test_handoff_week18 -v
"""
import json
import os
import unittest

from workbench_handoff import app as wb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_TOKEN = os.environ.get("SUNROOF_ADMIN_TOKEN", "sunroof-admin-w18")


def json_dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def wb_args():
    return {"case_id": "SR-1042", "queue": "billing-supervisor-review",
            "reason_code": "policy_conflict",
            "source_refs": ["retrofit-bulletin-v2", "retrofit-notice-v4"],
            "idempotency_key": "wb-test:1"}


def api(method, path, body=None, session=None):
    """进程内直调工作台路由 (session=None = 未登录)。"""
    admin = session is not None and session in wb.STATE.admin_sessions
    result = wb.handle_api(method, path, body or {}, admin)
    if result is None:
        result = wb.handle_api_admin(method, path, body or {}, admin)
    if result is None:
        result = wb.handle_api_execute(method, path, body or {}, admin)
    return result


class TestHandoffWeek18(unittest.TestCase):
    def setUp(self):
        wb.STATE.traces.clear()
        wb.STATE.audit.clear()

    def test_01_dual_role_isolation_403(self):
        status, body = api("POST", "/api/admin/switch",
                           {"switch": "MCP_OFF", "on": True, "operator": "x",
                            "reason": "y"})
        self.assertEqual(status, 403, "未登录访问管理面必须 403")
        status, body = api("POST", "/api/admin/login", {"token": "wrong"})
        self.assertEqual(status, 403)
        self.assertNotIn(ADMIN_TOKEN, json_dumps(body), "口令零回显")
        status, body = api("POST", "/api/admin/login", {"token": ADMIN_TOKEN})
        self.assertEqual(status, 200)
        session = body["session"]
        status, audit = api("GET", "/api/admin/audit", {}, session=session)
        self.assertEqual(status, 200)
        self.assertNotIn(session, json_dumps(audit), "session 不得出现在审计回显")
        status, _ = api("POST", "/api/admin/switch",
                        {"switch": "MCP_OFF", "on": True, "operator": "admin",
                         "reason": "演练"}, session=session)
        self.assertEqual(status, 200)
        api("POST", "/api/admin/switch",
            {"switch": "MCP_OFF", "on": False, "operator": "admin",
             "reason": "测试收尾恢复"}, session=session)
        self.assertFalse(wb.STATE.registry.is_off("MCP_OFF"))

    def test_02_business_flow_pqe(self):
        status, submit = api("POST", "/api/write/submit",
                             {"role": "pqe", "args": wb_args()})
        self.assertEqual(submit["state"], "approval_required")
        task_id = submit["task_id"]
        status, exec0 = api("POST", "/api/write/execute", {"task_id": task_id})
        self.assertFalse(exec0["ok"], "未批准执行 = 0 效果")
        status, login = api("POST", "/api/admin/login", {"token": ADMIN_TOKEN})
        session = login["session"]
        api("POST", "/api/write/approve", {"task_id": task_id}, session=session)
        status, exec1 = api("POST", "/api/write/execute", {"task_id": task_id})
        self.assertTrue(exec1["ok"])
        self.assertEqual(exec1["external_effects"], 1)
        self.assertTrue(exec1["result"]["verified"])
        status, replay = api("POST", "/api/write/execute", {"task_id": task_id})
        self.assertTrue(replay["result"]["replayed"])
        self.assertEqual(replay["external_effects"], 1, "重放无第二效果")

    def test_03_supervisor_write_rejected_by_frozen_kernel(self):
        """主管不能绕过一线发起受控写 (第 14 周冻结核: 写发起人恒为 pqe)。"""
        status, submit = api("POST", "/api/write/submit",
                             {"role": "quality_supervisor", "args": wb_args()})
        self.assertEqual(submit["state"], "approval_required",
                         "审批统一直接交管理员面板结构化签发, 无角色自动签发")
        status, exec1 = api("POST", "/api/write/execute",
                            {"task_id": submit["task_id"]})
        self.assertFalse(exec1["ok"], "主管直写必须被冻结核拒绝")
        self.assertEqual(exec1["terminal"], "forbidden")
        self.assertEqual(exec1["external_effects"], 0, "拒绝 = 0 效果")

    def test_04_degradation_via_workbench(self):
        status, submit = api("POST", "/api/write/submit",
                             {"role": "pqe", "args": wb_args()})
        status, login = api("POST", "/api/admin/login", {"token": ADMIN_TOKEN})
        session = login["session"]
        api("POST", "/api/write/approve", {"task_id": submit["task_id"]},
            session=session)
        api("POST", "/api/admin/switch",
            {"switch": "MCP_OFF", "on": True, "operator": "admin",
             "reason": "演练"}, session=session)
        status, degraded = api("POST", "/api/write/execute",
                               {"task_id": submit["task_id"]})
        self.assertEqual(degraded["terminal"], "action_preview")
        self.assertEqual(degraded["external_effects"], 0)
        api("POST", "/api/admin/switch",
            {"switch": "MCP_OFF", "on": False, "operator": "admin",
             "reason": "恢复复验通过"}, session=session)
        status, resumed = api("POST", "/api/write/execute",
                              {"task_id": submit["task_id"]})
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["external_effects"], 1)

    def test_05_task_trace_terminal_allowed(self):
        status, submit = api("POST", "/api/write/submit",
                             {"role": "pqe", "args": wb_args()})
        status, login = api("POST", "/api/admin/login", {"token": ADMIN_TOKEN})
        api("POST", "/api/write/approve", {"task_id": submit["task_id"]},
            session=login["session"])
        api("POST", "/api/write/execute", {"task_id": submit["task_id"]})
        status, trace = api("GET", "/api/task", {"task_id": submit["task_id"]})
        stages = {span["stage"] for span in trace["spans"]}
        self.assertTrue({"receive", "policy", "write"} <= stages)
        self.assertEqual(trace["terminal"][0]["classification"], "allowed")
        self.assertEqual(trace["metrics"]["forbidden_effect_count"], 0)

    def test_06_static_scope_and_index(self):
        from ci.redaction_scan import SCOPE_FILES, scan
        self.assertIn(os.path.join("workbench_handoff", "app.py"), SCOPE_FILES)
        report = scan()
        self.assertTrue(report["passed"], report["violations"])
        html = wb.build_index_html()
        self.assertIn("值守交接工作台", html)
        self.assertIn("诚实边界", html)
        self.assertIn("sunroof-diagnosis", html, "页面必须标明自研项目身份")

    def test_07_healthz_and_service_identity(self):
        """健康探针 + 服务身份: 防止把课程模板 week-018 工作台错当本工作台。"""
        import subprocess
        import sys
        import time
        import urllib.request
        env = {**os.environ, "SUNROOF_PORT": "8118", "SUNROOF_BIND": "127.0.0.1"}
        proc = subprocess.Popen(
            [sys.executable, "-m", "workbench_handoff.app"], cwd=PROJECT_ROOT,
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload = None
        try:
            for _ in range(60):
                try:
                    with urllib.request.urlopen(
                            "http://127.0.0.1:8118/healthz", timeout=1) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                        break
                except Exception:
                    time.sleep(0.1)
            self.assertIsNotNone(payload, "/healthz 未响应")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["service"], "sunroof-diagnosis-workbench")
            self.assertFalse(payload["template"], "身份必须是自研非模板")
            with urllib.request.urlopen("http://127.0.0.1:8118/", timeout=2) as r:
                html = r.read().decode("utf-8")
            self.assertIn("sunroof-diagnosis", html)
        finally:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

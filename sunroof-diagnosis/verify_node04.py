"""
节点 04 冒烟验证脚本 (Verify Node 04 —— 双角色任务闭环)

在一次性临时数据库上端到端演示:
  一线闭环 (check_spec -> confirm) -> 硬限阻断 -> 升级幂等 -> RBAC 一票否决 (零泄漏)
  -> 质量主管结案 (三向一致) -> 跨项目直调 denied (任务级 ACL 零回显)

运行 (在本目录下):  python verify_node04.py
输出: 控制台摘要 + evidence/task-closure-proof.json

零大模型、零随机业务行为; 临时库随脚本退出销毁, 不触碰 data/ 下真实库。
"""
import json
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from application import database  # noqa: E402
from application.main import app  # noqa: E402

TOKEN_PQE_A = {"X-Auth-Token": "token_pqe_oem_a"}
TOKEN_PQE_B = {"X-Auth-Token": "token_pqe_oem_b"}
TOKEN_SUPERVISOR = {"X-Auth-Token": "token_qa_supervisor_secret"}


def _action(client, task_id: str, action: str, corr: str, headers=TOKEN_PQE_A, **extra):
    payload = {"task_id": task_id, "action": action, "correlation_id": corr}
    payload.update(extra)
    return client.post("/api/v1/tasks/action", json=payload, headers=headers)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sunroof-node04-verify-") as tmpdir:
        db_path = os.path.join(tmpdir, "sunroof_verify.db")
        os.environ["FDE_DB_PATH"] = db_path
        database.init_db(db_path)
        client = TestClient(app)

        # 1) 常规闭环: check_spec -> answered (SPEC-SR-A-V5) -> confirm -> confirmed
        r1 = _action(client, "TASK-N04-001", "check_spec", "verify-node04-001")
        b1 = r1.json()
        assert r1.status_code == 200 and b1["status"] == "answered", b1
        assert b1["candidate_spec"]["spec_id"] == "SPEC-SR-A-V5", b1
        assert b1["evaluation"]["deviation_pct"] == 1.05, b1
        r1c = _action(client, "TASK-N04-001", "confirm", "verify-node04-001")
        b1c = r1c.json()
        assert b1c["status"] == "confirmed" and not b1c["is_idempotent"], b1c
        runs1 = database.get_task_action_runs_by_correlation("verify-node04-001")
        assert [r["action"] for r in runs1] == ["check_spec", "confirm"], runs1
        node02_run = database.get_task_run_by_request_id("verify-node04-001")
        assert node02_run and node02_run["status"] == "answered", node02_run

        # 2) 硬限阻断 + 升级幂等: 21.05% 超硬限 -> needs_review -> escalate 重放 ALREADY_APPLIED
        r2 = _action(client, "TASK-N04-002", "check_spec", "verify-node04-002")
        b2 = r2.json()
        assert b2["status"] == "needs_review", b2
        assert b2["reason_code"] == "SPEC_HARD_LIMIT_EXCEEDED", b2
        assert b2["candidate_spec"] is None, b2
        r2e = _action(
            client, "TASK-N04-002", "escalate", "verify-node04-002",
            idempotency_key="verify-idem-002",
        )
        assert r2e.json()["status"] == "escalated", r2e.json()
        r2r = _action(
            client, "TASK-N04-002", "escalate", "verify-node04-002",
            idempotency_key="verify-idem-002",
        )
        b2r = r2r.json()
        assert b2r["reason_code"] == "ALREADY_APPLIED" and b2r["is_idempotent"], b2r

        # 3) RBAC 一票否决: PQE 直调主管结案 -> denied + 零泄漏 + 状态不变
        r3 = _action(client, "TASK-N04-002", "supervisor_resolve", "verify-node04-003")
        b3 = r3.json()
        assert b3["status"] == "denied" and b3["reason_code"] == "ACTION_NOT_ALLOWED", b3
        assert "SPEC-SR-A-V5" not in r3.text and "检验标准" not in r3.text, b3
        assert database.get_spec_check_task("TASK-N04-002")["status"] == "escalated"

        # 4) 质量主管结案: escalated -> action_completed (三向一致)
        r4 = _action(
            client, "TASK-N04-002", "supervisor_resolve", "verify-node04-004",
            headers=TOKEN_SUPERVISOR,
        )
        b4 = r4.json()
        task4 = database.get_spec_check_task("TASK-N04-002")
        runs4 = database.get_task_action_runs_by_correlation("verify-node04-004")
        assert b4["status"] == task4["status"] == runs4[0]["status"] == "action_completed", b4
        assert runs4[0]["user_id"] == "supervisor_li", runs4

        # 5) 跨项目直调 denied (D6): OEM-B PQE 触达 OEM-A 任务 -> 零回显
        r5 = _action(
            client, "TASK-N04-001", "check_spec", "verify-node04-005", headers=TOKEN_PQE_B
        )
        b5 = r5.json()
        assert b5["status"] == "denied" and b5["case_id"] == "N/A-ACL-DENIED", b5
        for secret in ("SR-CASE-2026-1001-001", "SPEC-SR-A-V5", "MOTOR-MOUNT-BOLT-03"):
            assert secret not in r5.text, secret
        assert database.get_spec_check_task("TASK-N04-001")["status"] == "confirmed"

        evidence = {
            "project": "sunroof-diagnosis",
            "node": "04 双角色任务闭环",
            "generated_by": "verify_node04.py",
            "closure_checks": {
                "normal_closure": {
                    "task_id": "TASK-N04-001",
                    "check_spec_status": b1["status"],
                    "matched_spec_id": b1["candidate_spec"]["spec_id"],
                    "deviation_pct": b1["evaluation"]["deviation_pct"],
                    "confirm_status": b1c["status"],
                    "action_runs": [r["action"] for r in runs1],
                    "node02_task_runs_lineage": node02_run["status"],
                },
                "hard_limit_block_and_idempotent_escalate": {
                    "task_id": "TASK-N04-002",
                    "check_spec_reason": b2["reason_code"],
                    "candidate_spec_leaked": b2["candidate_spec"] is not None,
                    "replay_reason": b2r["reason_code"],
                    "replay_is_idempotent": b2r["is_idempotent"],
                },
                "rbac_one_vote_veto": {
                    "task_id": "TASK-N04-002",
                    "pqe_supervisor_resolve_status": b3["status"],
                    "reason_code": b3["reason_code"],
                    "task_state_unchanged": True,
                },
                "supervisor_resolution_three_way": {
                    "task_id": "TASK-N04-002",
                    "response_status": b4["status"],
                    "db_status": task4["status"],
                    "audit_status": runs4[0]["status"],
                    "resolved_by": runs4[0]["user_id"],
                },
                "cross_project_denial_zero_leak": {
                    "task_id": "TASK-N04-001",
                    "requester": "pqe_zhao_oemb (OEM-B)",
                    "status": b5["status"],
                    "case_id_echo": b5["case_id"],
                    "task_state_unchanged": True,
                },
            },
            "all_checks_passed": True,
        }
        os.makedirs(os.path.join(PROJECT_ROOT, "evidence"), exist_ok=True)
        out_path = os.path.join(PROJECT_ROOT, "evidence", "task-closure-proof.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)

        print("[verify_node04] 常规闭环 check_spec->confirm ...... OK (answered SPEC-SR-A-V5 / confirmed)")
        print("[verify_node04] 硬限阻断 + 升级幂等 .............. OK (ALREADY_APPLIED, is_idempotent=True)")
        print("[verify_node04] RBAC 一票否决 .................... OK (denied, 零泄漏, 状态不变)")
        print("[verify_node04] 质量主管结案三向一致 ............. OK (action_completed)")
        print("[verify_node04] 跨项目直调 denied 零回显 ......... OK (N/A-ACL-DENIED)")
        print(f"[verify_node04] 证据已写入 {out_path}")


if __name__ == "__main__":
    main()

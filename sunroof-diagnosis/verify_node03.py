"""
节点 03 冒烟验证脚本 (Verify Node 03 —— 可靠数据入口)

在一次性临时数据库上端到端演示:
  批次摄取 (accepted / duplicate / quarantined 三态分流)
  -> 三处一致对账 -> 批次幂等重跑 -> 定向重放 (隔离区修复) -> 回接确定性 spec-check 引擎

运行 (在本目录下):  python verify_node03.py
输出: 控制台摘要 + evidence/batch-reconciliation-proof.json

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
from ingestion import service as ingestion_service  # noqa: E402
from ingestion.repository import get_three_way_reconciliation  # noqa: E402
from ingestion.schema import BatchIngestionRequest, ReplayRequest  # noqa: E402

SOURCE_ID = "qms-export-w40-2026"
TOKEN_PQE_A = {"X-Auth-Token": "token_pqe_oem_a"}

ACCEPTED_ROW = {
    "spec_id": "SPEC-GSK-A-V1", "title": "OEM-A 密封条压装间隙检验标准", "version": "v1.0",
    "part_no": "GASKET-STRIP-A9", "process_step": "gap_check", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-07-01", "expires_at": None,
    "nominal_value": 3.0, "tol_plus": 0.3, "tol_minus": 0.3, "unit": "mm",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-GSK-A9-v1#Section-2.1",
    "source_id": "QMS-SOP-SR-GSK-A9", "source_version": "v1.0",
    "owner_id": "owner-qms-gsk", "allowed_oem_projects": ["OEM-A"],
}
V6_MISSING_OWNER = {
    "spec_id": "SPEC-SR-A-V6", "title": "OEM-A 天窗电机固定螺栓拧紧扭矩检验标准 v6", "version": "v6.0",
    "part_no": "MOTOR-MOUNT-BOLT-03", "process_step": "torque_tightening", "oem_project": "OEM-A",
    "is_current": 1, "effective_at": "2026-08-01", "expires_at": None,
    "nominal_value": 9.6, "tol_plus": 0.5, "tol_minus": 0.5, "unit": "N·m",
    "formula_rule": "lower = nominal - tol_minus; upper = nominal + tol_plus",
    "source_ref": "QMS-SOP-SR-04-v6#Section-3.2",
    "source_id": "QMS-SOP-SR-04", "source_version": "v6.0",
    "owner_id": None, "allowed_oem_projects": ["OEM-A"],  # owner 缺失 -> MISSING_OWNER
}


def _payload(request_id: str) -> dict:
    return {
        "request_id": request_id,
        "task_type": "spec_check",
        "sn": "SR-20261010-0001",
        "part_no": "MOTOR-MOUNT-BOLT-03",
        "process_step": "torque_tightening",
        "measured_at": "2026-08-15",
        "measured_value": 8.2,
    }
def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sunroof-node03-verify-") as tmpdir:
        db_path = os.path.join(tmpdir, "sunroof_verify.db")
        os.environ["FDE_DB_PATH"] = db_path
        database.init_db(db_path)
        client = TestClient(app)

        # 0) 引擎基线: 摄取前唯一现行版本 SPEC-SR-A-V5 -> answered
        resp = client.post(
            "/api/v1/spec-check", json=_payload("verify-node03-001"), headers=TOKEN_PQE_A
        )
        assert resp.status_code == 200 and resp.json()["status"] == "answered", resp.text
        baseline_spec_id = resp.json()["spec"]["spec_id"]
        assert baseline_spec_id == "SPEC-SR-A-V5", baseline_spec_id

        # 1) 批次摄取: 3 行 = 1 accepted + 1 duplicate + 1 quarantined(MISSING_OWNER)
        batch = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-node03-verify",
                source_id=SOURCE_ID,
                source_version="v1.0",
                correlation_id="ing-node03-verify-001",
                rows=[ACCEPTED_ROW, dict(ACCEPTED_ROW), V6_MISSING_OWNER],
            )
        )
        assert (batch.accepted_count, batch.duplicate_count, batch.quarantined_count) == (1, 1, 1), batch
        assert batch.status.value == "completed_with_quarantine", batch.status

        # 2) 三处一致对账
        recon = get_three_way_reconciliation("batch-node03-verify")
        assert recon["three_way_consistent"], recon

        # 3) 批次幂等重跑: 同 batch_id 返回既有对账结果, 零写副作用
        rerun = ingestion_service.process_batch_ingestion(
            BatchIngestionRequest(
                batch_id="batch-node03-verify",
                source_id=SOURCE_ID,
                source_version="v1.0",
                correlation_id="ing-node03-verify-002",
                rows=[],
            )
        )
        assert rerun.is_idempotent_replay and rerun.accepted_count == 1, rerun

        # 4) 定向重放: 补齐 owner_id -> SPEC-SR-A-V6 入主表 (隔离记录 resolved, 历史保留)
        conn = database.get_db_connection()
        try:
            quar_row = conn.execute(
                "SELECT quarantine_id FROM quarantine_records "
                "WHERE batch_id = ? AND reason_code = 'MISSING_OWNER'",
                ("batch-node03-verify",),
            ).fetchone()
            quarantine_id = quar_row["quarantine_id"]
        finally:
            conn.close()
        replay = ingestion_service.execute_targeted_replay(
            ReplayRequest(
                quarantine_id=quarantine_id,
                replayed_by="knowledge-admin-qms",
                fixed_payload={"owner_id": "owner-qms-sr"},
                notes="已由 QMS 知识管理员补齐规格所有者",
            )
        )
        assert replay.status == "success", replay

        # 5) 引擎回接: v5.0 + v6.0 两条现行并存 -> 确定性 SPEC_CONFLICT (无假成功)
        resp_after = client.post(
            "/api/v1/spec-check", json=_payload("verify-node03-002"), headers=TOKEN_PQE_A
        )
        body_after = resp_after.json()
        assert (
            body_after["status"] == "needs_review"
            and body_after["reason_code"] == "SPEC_CONFLICT"
        ), body_after

        evidence = {
            "project": "sunroof-diagnosis",
            "node": "03 可靠数据入口",
            "generated_by": "verify_node03.py",
            "batch_summary": {
                "batch_id": batch.batch_id,
                "source_id": batch.source_id,
                "total_rows": batch.total_rows,
                "accepted_count": batch.accepted_count,
                "duplicate_count": batch.duplicate_count,
                "quarantined_count": batch.quarantined_count,
                "status": batch.status.value,
                "is_idempotent_rerun": rerun.is_idempotent_replay,
                "quarantine_summary": batch.quarantine_summary,
            },
            "three_way_reconciliation": {
                "batch_id": recon["batch_id"],
                "total_input_rows": recon["total_input_rows"],
                "recorded_accepted": recon["recorded_accepted"],
                "recorded_duplicate": recon["recorded_duplicate"],
                "recorded_quarantined": recon["recorded_quarantined"],
                "accepted_in_specs_table": recon["accepted_in_specs_table"],
                "quarantined_in_quarantine_table": recon["quarantined_in_quarantine_table"],
                "three_way_consistent": recon["three_way_consistent"],
            },
            "targeted_replay_execution": {
                "replay_id": replay.replay_id,
                "quarantine_id": replay.quarantine_id,
                "status": replay.status,
                "accepted_spec_id": replay.accepted_spec_id,
            },
            "engine_regression_check": {
                "before": {
                    "request_id": "verify-node03-001",
                    "status": "answered",
                    "matched_spec_id": baseline_spec_id,
                },
                "after": {
                    "request_id": "verify-node03-002",
                    "status": "needs_review",
                    "reason_code": "SPEC_CONFLICT",
                },
            },
            "all_checks_passed": True,
        }
        os.makedirs(os.path.join(PROJECT_ROOT, "evidence"), exist_ok=True)
        out_path = os.path.join(PROJECT_ROOT, "evidence", "batch-reconciliation-proof.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)

        print("[verify_node03] 批次摄取三态分流 ...... OK (1 accepted / 1 duplicate / 1 quarantined)")
        print("[verify_node03] 三处一致对账 .......... OK (three_way_consistent=True)")
        print("[verify_node03] 批次幂等重跑 .......... OK (is_idempotent_replay=True)")
        print(f"[verify_node03] 定向重放 .............. OK (replay_id={replay.replay_id})")
        print("[verify_node03] 回接 spec-check 引擎 .. OK (answered -> SPEC_CONFLICT)")
        print(f"[verify_node03] 证据已写入 {out_path}")


if __name__ == "__main__":
    main()
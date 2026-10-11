"""只读工单上下文库 (Week 13) —— 隔离模拟工单库 (16 张教学工单, 零真实系统)。

物理隔离: 本模块零写入口 (测试断言); 工单与第 10-12 周评测集共享词表 (region=east)。
"""
from __future__ import annotations

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_PATH = os.path.join(PROJECT_ROOT, "mcptool", "case_store_data.json")

# 16 张合成工单 (task_type 与 TASK_TYPE_TO_CATEGORY 映射对齐; tenant 演示跨租户拒绝)
_TICKETS = [
    {"case_id": "SR-1042", "tenant": "sunroof-east", "task_type": "policy_conflict",
     "region": "east", "purchased_at": "2026-03-01", "note": "restricted"},
    {"case_id": "SR-1043", "tenant": "sunroof-east", "task_type": "torque_retorque",
     "region": "east", "purchased_at": "2026-06-15", "note": "general"},
    {"case_id": "SR-1044", "tenant": "sunroof-east", "task_type": "noise_response",
     "region": "east", "purchased_at": "2026-02-11", "note": "general"},
    {"case_id": "SR-1045", "tenant": "sunroof-east", "task_type": "drainage",
     "region": "east", "purchased_at": "2026-05-20", "note": "general"},
    {"case_id": "SR-1046", "tenant": "sunroof-east", "task_type": "warranty_voucher",
     "region": "east", "purchased_at": "2026-01-30", "note": "general"},
    {"case_id": "SR-1047", "tenant": "sunroof-east", "task_type": "claim_supervisor",
     "region": "east", "purchased_at": "2026-08-01", "note": "restricted"},
    {"case_id": "SR-1048", "tenant": "sunroof-east", "task_type": "glass_alignment",
     "region": "east", "purchased_at": "2026-04-02", "note": "general"},
    {"case_id": "SR-1049", "tenant": "sunroof-east", "task_type": "retrofit_reset",
     "region": "east", "purchased_at": "2026-04-20", "note": "conflict"},
    {"case_id": "SR-1050", "tenant": "sunroof-east", "task_type": "torque_retorque",
     "region": "east", "purchased_at": "2026-06-18", "note": "general"},
    {"case_id": "SR-1051", "tenant": "sunroof-east", "task_type": "noise_response",
     "region": "east", "purchased_at": "2026-03-03", "note": "general"},
    {"case_id": "SR-1052", "tenant": "sunroof-east", "task_type": "drainage",
     "region": "east", "purchased_at": "2026-06-01", "note": "general"},
    {"case_id": "SR-1053", "tenant": "sunroof-east", "task_type": "escalation_review",
     "region": "east", "purchased_at": "2026-07-01", "note": "general"},
    {"case_id": "SR-1054", "tenant": "sunroof-east", "task_type": "warranty_voucher",
     "region": "east", "purchased_at": "2026-02-25", "note": "general"},
    {"case_id": "SR-1055", "tenant": "sunroof-east", "task_type": "glass_alignment",
     "region": "east", "purchased_at": "2026-05-05", "note": "general"},
    {"case_id": "SR-9001", "tenant": "sunroof-north", "task_type": "torque_retorque",
     "region": "north", "purchased_at": "2026-06-10", "note": "cross-tenant"},
    {"case_id": "SR-9002", "tenant": "sunroof-north", "task_type": "claim_supervisor",
     "region": "north", "purchased_at": "2026-08-15", "note": "cross-tenant"},
]

TASK_TYPE_TO_CATEGORY = {
    "policy_conflict": "retrofit_conflict",
    "torque_retorque": "torque_retorque_east",
    "noise_response": "service_general_response",
    "drainage": "service_general_response",
    "warranty_voucher": "service_general_response",
    "claim_supervisor": "restricted_claim",
    "glass_alignment": "service_general_response",
    "retrofit_reset": "retrofit_conflict",
    "escalation_review": "service_general_response",
}


def _store() -> list:
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["tickets"]


def lookup_context(trusted_context: dict, arguments: dict) -> dict:
    """只读工具服务端实现: 四级鉴权 (租户×角色×对象×字段) -> 封闭字段结果。

    不可区分性: 不存在/跨租户/字段不可见 三因的用户面封套逐字节一致
    (错误封套不含 run_id/细节); 细分原因只进受限审计 (由调用方记录)。
    """
    if set(arguments) - {"case_id", "fields"}:
        return {"ok": False, "error": {"code": "invalid_argument"}}
    case_id = str(arguments.get("case_id", "")).strip().upper()
    fields = list(arguments.get("fields", []))
    if not case_id or not fields or len(fields) > len(READ_FIELDS_ALL):
        return {"ok": False, "error": {"code": "invalid_argument"}}
    if any(f not in READ_FIELDS_ALL for f in fields):
        return {"ok": False, "error": {"code": "invalid_argument"}}
    role = trusted_context.get("role")
    tenant = trusted_context.get("tenant")
    ticket = next((t for t in _store() if t["case_id"] == case_id), None)
    invisible = (ticket is None or ticket["tenant"] != tenant)
    if invisible:
        return {"ok": False, "error": {"code": "not_available"}}
    restricted = ticket["note"] == "restricted" and role != "quality_supervisor"
    if restricted:
        return {"ok": False, "error": {"code": "not_available"}}
    return {
        "ok": True,
        "result": {"case_id": case_id, **{f: ticket[f] for f in fields},
                   "record_version": 1},
    }


READ_FIELDS_ALL = ("task_type", "region", "purchased_at")

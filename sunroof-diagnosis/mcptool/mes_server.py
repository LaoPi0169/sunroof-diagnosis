"""MES 只读工具服务端（阶段 C 节点 ⑰）—— 校验链: schema → RBAC → ACL → 只读取数。

判定顺序与用户面语义 (沿用 Week-13 case.lookup_context 既定模式, 不改其语义):
  1) 参数封闭校验 (mes_contract.validate_arguments, 网关与服务端各跑一次纵深防御);
  2) RBAC: role 必须在 ALLOWED_ROLES (role-and-permission-matrix.md §2:
     『MES 测试日志.读取』对 系统/运维 ✗, Default-Deny);
  3) ACL: pqe 要求记录 oem_project ∈ allowed_projects; quality_supervisor
     ["*"] 跨项目只读 (矩阵 §1 特批例外);
  4) 未知 SN / 跨项目 / 角色不足 三因的用户面封套逐字节一致
     ({"ok": false, "error": {"code": "not_available"}}) —— 零存在性泄漏 (P-1);
     细分原因只进受限审计 (audit_sink 由调用方注入, 每条含 run_id / reason_typed /
     external_effects=0, 永不进用户面响应)。
成功封套字段级结构: available → {"status", "payload"};
缺失 → {"status": "not_available", "reason_code": "FIELD_NOT_AVAILABLE"} (P-3)。
外部写效果恒 0 (effect=read_only; 本模块与 mes_store 均无任何写入口)。
"""
from __future__ import annotations

from mcptool import mes_contract as M
from mcptool import mes_store as store


def lookup_production_record(trusted_context: dict, arguments: dict,
                             audit_sink=None, run_id: str = None) -> dict:
    """只读工具服务端入口: 返回用户面封套; 细分原因只进 audit_sink。"""
    if not isinstance(trusted_context, dict) or \
            set(trusted_context) != set(M.TRUSTED_CONTEXT_KEYS):
        _audit(audit_sink, run_id, None, "invalid_argument", 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    sn, fields, segment, err = M.validate_arguments(arguments)
    if err is not None:
        _audit(audit_sink, run_id, sn, M.ERROR_INVALID_ARGUMENT, 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    role = trusted_context.get("role")
    if role not in M.ALLOWED_ROLES:
        _audit(audit_sink, run_id, sn, "role_denied", 0)
        return _unavailable()
    record = store.get_record(sn)
    if record is None:
        _audit(audit_sink, run_id, sn, "unknown_sn", 0)
        return _unavailable()
    if role == "pqe":
        projects = trusted_context.get("allowed_projects")
        if not isinstance(projects, list) or record["oem_project"] not in projects:
            _audit(audit_sink, run_id, sn, "cross_project_denied", 0)
            return _unavailable()
    # quality_supervisor: ["*"] 跨项目只读 (矩阵 §1), 直接放行
    field_results = {}
    for field in fields:
        reason = store.missing_field_reason(sn, field)
        if reason is not None:
            field_results[field] = {"status": "not_available", "reason_code": reason}
            continue
        if field == "assembly_torque":
            payload = {"records": record["assembly_torque"],
                       "count": len(record["assembly_torque"])}
        elif field == "ecu_flash":
            payload = dict(record["ecu_flash"])
        else:  # sensor_waveform: R-2 分页载荷
            payload = store.waveform_segment(sn, segment)
        field_results[field] = {"status": "available", "payload": payload}
    _audit(audit_sink, run_id, sn, "ok", 0)
    return {"ok": True,
            "result": {"sn": sn, "oem_project": record["oem_project"],
                       "record_version": 1, "fields": field_results}}


def _unavailable() -> dict:
    """用户面失败封套 (三因逐字节一致; 不含 run_id/项目组标识/存在性信息)。"""
    return {"ok": False, "error": {"code": M.ERROR_NOT_AVAILABLE}}


def _audit(sink, run_id, sn, reason_typed: str, effects: int) -> None:
    """受限审计: 只追加, 不打印 (细分原因永不进用户面)。"""
    if sink is not None:
        sink.append({"run_id": run_id, "tool": M.TOOL_NAME, "sn": sn,
                     "reason_typed": reason_typed, "external_effects": effects})

"""PLM 只读工具服务端（阶段 C 节点 ⑱）—— 校验链: schema → RBAC → ACL → 只读取数。

判定顺序与用户面语义 (沿用节点 ⑰ mes.lookup_production_record 既定模式, 不改其语义):
  1) 参数封闭校验 (plm_contract.validate_arguments, 网关与服务端各跑一次纵深防御);
  2) RBAC: role 必须在 ALLOWED_ROLES (role-and-permission-matrix.md §2:
     『PLM BOM/ECO.读取』对 agent 检索层排除 / ops ✗, Default-Deny);
  3) ACL: pqe 要求记录 oem_project ∈ allowed_projects; quality_supervisor
     ["*"] 跨项目只读 (矩阵 §2 ✓ 只读);
  4) 未知件号 / 跨项目 / 角色不足 三因的用户面封套逐字节一致
     ({"ok": false, "error": {"code": "not_available"}}) —— 零存在性泄漏 (P-1);
     细分原因只进受限审计 (audit_sink 由调用方注入, 每条含 run_id / reason_typed /
     external_effects=0, 永不进用户面响应)。
成功封套字段级结构: available → {"status", "payload"};
缺失 → {"status": "not_available", "reason_code": "FIELD_NOT_AVAILABLE"} (P-3)。
图纸硬门槛: drawing_registry 只返回登记元数据 (content_disclosure=denied_by_policy),
图纸内容/文件/链接/渲染数据永不出工具 —— 主管亦不展示 (矩阵 §2)。
外部写效果恒 0 (effect=read_only; 本模块无任何写入口)。
取数经 mcptool/source_adapter.py 注入缝 (阶段 D 批次 2): 默认合成夹具逐字节等价;
可选注入 HTTP 适配器, 源不可达 -> 既有 not_available 封套 + 受限审计
source_unavailable:<源> 精细分型 (用户面三因一致语义不变)。
"""
from __future__ import annotations

from mcptool import plm_contract as M
from mcptool import source_adapter


def lookup_engineering_record(trusted_context: dict, arguments: dict,
                              audit_sink=None, run_id: str = None) -> dict:
    """只读工具服务端入口: 返回用户面封套; 细分原因只进 audit_sink。"""
    if not isinstance(trusted_context, dict) or \
            set(trusted_context) != set(M.TRUSTED_CONTEXT_KEYS):
        _audit(audit_sink, run_id, None, "invalid_argument", 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    part_no, fields, eco_offset, err = M.validate_arguments(arguments)
    if err is not None:
        _audit(audit_sink, run_id, part_no, M.ERROR_INVALID_ARGUMENT, 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    role = trusted_context.get("role")
    if role not in M.ALLOWED_ROLES:
        _audit(audit_sink, run_id, part_no, "role_denied", 0)
        return _unavailable()
    store = source_adapter.resolve("plm")
    try:
        record = store.get_record(part_no)
        if record is None:
            _audit(audit_sink, run_id, part_no, "unknown_part", 0)
            return _unavailable()
        if role == "pqe":
            projects = trusted_context.get("allowed_projects")
            if not isinstance(projects, list) or record["oem_project"] not in projects:
                _audit(audit_sink, run_id, part_no, "cross_project_denied", 0)
                return _unavailable()
        # quality_supervisor: ["*"] 跨项目只读 (矩阵 §2), 直接放行
        field_results = {}
        for field in fields:
            reason = store.missing_field_reason(part_no, field)
            if reason is not None:
                field_results[field] = {"status": "not_available", "reason_code": reason}
                continue
            if field == "bom_snapshot":
                payload = {"revision": record["bom_snapshot"]["revision"],
                           "items": list(record["bom_snapshot"]["items"]),
                           "count": len(record["bom_snapshot"]["items"])}
            elif field == "eco_history":
                payload = store.eco_window(part_no, eco_offset)
            else:  # drawing_registry: 图纸登记元数据 (内容永不出工具, 矩阵 §2)
                registry = record["drawing_registry"]
                payload = {"drawings": list(registry["drawings"]),
                           "count": len(registry["drawings"]),
                           "content_disclosure": registry["content_disclosure"]}
            field_results[field] = {"status": "available", "payload": payload}
    except source_adapter.SourceUnavailableError as exc:
        # D2-b: 源不可达 -> 既有「源不可用结构化拒答」口径 (绝不编造, 绝不回退合成数据)。
        _audit(audit_sink, run_id, part_no,
               source_adapter.SOURCE_UNAVAILABLE_PREFIX + exc.source, 0)
        return _unavailable()
    _audit(audit_sink, run_id, part_no, "ok", 0)
    return {"ok": True,
            "result": {"part_no": part_no, "oem_project": record["oem_project"],
                       "record_version": 1, "fields": field_results}}


def _unavailable() -> dict:
    """用户面失败封套 (三因逐字节一致; 不含 run_id/项目组标识/存在性信息)。"""
    return {"ok": False, "error": {"code": M.ERROR_NOT_AVAILABLE}}


def _audit(sink, run_id, part_no, reason_typed: str, effects: int) -> None:
    """受限审计: 只追加, 不打印 (细分原因永不进用户面)。"""
    if sink is not None:
        sink.append({"run_id": run_id, "tool": M.TOOL_NAME, "part_no": part_no,
                     "reason_typed": reason_typed, "external_effects": effects})
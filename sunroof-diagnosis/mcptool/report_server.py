"""报告草稿只读工具服务端（阶段 C 节点 ⑳）—— 校验链: schema → RBAC → 聚合。

判定顺序与用户面语义 (沿用节点 ⑰⑱⑲ 既定模式, 不改其语义):
  1) 参数封闭校验 (report_contract.validate_arguments, 网关与服务端各跑一次);
  2) RBAC: role 必须在 ALLOWED_ROLES (Default-Deny);
  3) 聚合三源: MES ⑰ / QMS ⑲ / PLM ⑱ 各自完整执行校验链 (纵深防御);
     任一源失败 -> 整体用户面封套 not_available (三因一致零存在性泄漏, P-1),
     nested 审计并入受限审计 (细分原因永不进用户面);
  4) 组装草稿: findings (带 citation) + gaps (结构化缺失, 绝不编造) +
     conclusion (draft_ready / needs_review, 确定性规则) + 静态 disclaimer;
  5) 外部写效果恒 0 (effect=read_only; 本模块与聚合引擎均零写入口)。
"""
from __future__ import annotations

from mcptool import report_aggregate as agg
from mcptool import report_contract as M
from mcptool import source_adapter


def draft_root_cause(trusted_context: dict, arguments: dict,
                     audit_sink=None, run_id: str = None) -> dict:
    """只读聚合服务端入口: 返回用户面封套; 细分原因只进 audit_sink。"""
    if not isinstance(trusted_context, dict) or \
            set(trusted_context) != set(M.TRUSTED_CONTEXT_KEYS):
        _audit(audit_sink, run_id, None, "invalid_argument", 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    sn, part_no, process_step, err = M.validate_arguments(arguments)
    if err is not None:
        _audit(audit_sink, run_id, part_no, M.ERROR_INVALID_ARGUMENT, 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    role = trusted_context.get("role")
    if role not in M.ALLOWED_ROLES:
        _audit(audit_sink, run_id, part_no, "role_denied", 0)
        return _unavailable()
    envelopes, nested = agg.collect_sources(trusted_context, sn, part_no,
                                            process_step, run_id or "run")
    failed = agg.failed_source(envelopes)
    if failed is not None:
        agg.merge_nested_audit(audit_sink, nested)
        _audit(audit_sink, run_id, part_no, "source_unavailable:" + failed, 0)
        return _unavailable()
    try:
        result = agg.assemble_result(envelopes, sn, part_no, process_step)
    except source_adapter.SourceUnavailableError as exc:
        # D2-b 兜底: 组装期取数缝瞬断 (R-2 波形 gap 复核) -> 既有结构化拒答口径
        agg.merge_nested_audit(audit_sink, nested)
        _audit(audit_sink, run_id, part_no,
               source_adapter.SOURCE_UNAVAILABLE_PREFIX + exc.source, 0)
        return _unavailable()
    agg.merge_nested_audit(audit_sink, nested)
    _audit(audit_sink, run_id, part_no, "ok", 0)
    return {"ok": True, "result": result}


def _unavailable() -> dict:
    """用户面失败封套 (三因逐字节一致; 不含 run_id/项目组标识/存在性信息)。"""
    return {"ok": False, "error": {"code": M.ERROR_NOT_AVAILABLE}}


def _audit(sink, run_id, part_no, reason_typed: str, effects: int) -> None:
    """受限审计: 只追加, 不打印 (细分原因永不进用户面)。"""
    if sink is not None:
        sink.append({"run_id": run_id, "tool": M.TOOL_NAME, "part_no": part_no,
                     "reason_typed": reason_typed, "external_effects": effects})
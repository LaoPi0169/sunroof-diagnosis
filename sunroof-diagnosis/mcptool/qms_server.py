"""QMS 只读工具服务端（阶段 C 节点 ⑲）—— 校验链: schema → RBAC → ACL → 只读取数。

判定顺序与用户面语义 (沿用节点 ⑰⑱ mes/plm 只读工具既定模式, 不改其语义):
  1) 参数封闭校验 (qms_contract.validate_arguments, 网关与服务端各跑一次纵深防御);
  2) RBAC: role 必须在 ALLOWED_ROLES (role-and-permission-matrix.md §2:
     『QMS 检验 SOP/质量标准.读取』对 agent 检索层排除 / ops ✗, Default-Deny);
  3) ACL: pqe 要求记录 oem_project ∈ allowed_projects; quality_supervisor
     ["*"] 跨项目只读 (矩阵 §2 ✓ 只读);
  4) 未知件号 / 跨项目 / 角色不足 三因的用户面封套逐字节一致
     ({"ok": false, "error": {"code": "not_available"}}) —— 零存在性泄漏 (P-1);
     细分原因只进受限审计 (audit_sink 由调用方注入, 每条含 run_id / reason_typed /
     external_effects=0, 永不进用户面响应)。
P-2 取数纪律: sop_rows/standard_rows 中 is_current=0 的被替代行在本层被排除
(SQL WHERE is_current=1 语义), 被替代版本标识永不出现在响应; 仅存在被替代行时
该字段返回 {"status": "not_available", "reason_code": "NO_CURRENT_SOP"}。
P-3: 字段行完全缺失 -> reason_code=FIELD_NOT_AVAILABLE, 绝不编造数据。
成功封套字段级结构: available → {"status", "payload"} (server 组装, 静态行裁剪为
用户面白名单字段 + count, 不透传 is_current/superseded_by 内部治理键)。
外部写效果恒 0 (effect=read_only; 本模块无任何写入口)。
取数经 mcptool/source_adapter.py 注入缝 (阶段 D 批次 2): 默认合成夹具逐字节等价;
可选注入 HTTP 适配器, 源不可达 -> 既有 not_available 封套 + 受限审计
source_unavailable:<源> 精细分型 (用户面三因一致语义不变)。
"""
from __future__ import annotations

from mcptool import qms_contract as M
from mcptool import source_adapter

# 用户面 payload 白名单 (is_current/superseded_by/supersedes 等内部治理键
# 永不出工具 —— 被替代版本标识绝不出现在响应, P-2 纪律)
_SOP_FIELDS = ("sop_id", "version", "title", "effective_at", "owner_id",
               "process_step", "mes_step")
_STANDARD_FIELDS = ("spec_id", "version", "unit", "target_value", "tolerance",
                    "formula_rule", "source_ref", "mes_step")


def lookup_inspection_standard(trusted_context: dict, arguments: dict,
                               audit_sink=None, run_id: str = None) -> dict:
    """只读工具服务端入口: 返回用户面封套; 细分原因只进 audit_sink。"""
    if not isinstance(trusted_context, dict) or \
            set(trusted_context) != set(M.TRUSTED_CONTEXT_KEYS):
        _audit(audit_sink, run_id, None, "invalid_argument", 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    part_no, process_step, fields, err = M.validate_arguments(arguments)
    if err is not None:
        _audit(audit_sink, run_id, part_no, M.ERROR_INVALID_ARGUMENT, 0)
        return {"ok": False, "error": {"code": M.ERROR_INVALID_ARGUMENT}}
    role = trusted_context.get("role")
    if role not in M.ALLOWED_ROLES:
        _audit(audit_sink, run_id, part_no, "role_denied", 0)
        return _unavailable()
    store = source_adapter.resolve("qms")
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
            field_results[field] = _field_result(part_no, field, process_step)
    except source_adapter.SourceUnavailableError as exc:
        # D2-b: 源不可达 -> 既有「源不可用结构化拒答」口径 (绝不编造, 绝不回退合成数据):
        # 用户面仍三因一致 not_available (P-1), 细分原因只进受限审计 (精确分型)。
        _audit(audit_sink, run_id, part_no,
               source_adapter.SOURCE_UNAVAILABLE_PREFIX + exc.source, 0)
        return _unavailable()
    _audit(audit_sink, run_id, part_no, "ok", 0)
    return {"ok": True,
            "result": {"part_no": part_no, "oem_project": record["oem_project"],
                       "process_step": process_step, "record_version": 1,
                       "fields": field_results}}


def _field_result(part_no: str, field: str, process_step: str) -> dict:
    """单字段取数 (P-2 现行过滤 + P-3 结构化缺失; 异常上抛由入口 try 统一接)。"""
    store = source_adapter.resolve("qms")
    reason = store.missing_field_reason(part_no, field)
    if reason is not None:
        return {"status": "not_available", "reason_code": reason}
    rows = store.field_rows(part_no, field)
    if rows is None:
        return {"status": "not_available", "reason_code": M.FIELD_NOT_AVAILABLE}
    step_rows = _rows_in_step(rows, process_step)
    current = store.current_rows(step_rows)
    if not current:
        if step_rows:  # 仅存在被替代行 -> P-2 结构化拒引, 被替代版本标识不出
            return {"status": "not_available", "reason_code": M.NO_CURRENT_SOP}
        return {"status": "not_available", "reason_code": M.FIELD_NOT_AVAILABLE}
    row = current[0]
    if field == "sop_current":
        payload = {k: row[k] for k in _SOP_FIELDS if k in row}
        payload["exclusion_policy"] = M.CURRENT_ONLY_POLICY
    elif field == "quality_standard":
        payload = {k: row[k] for k in _STANDARD_FIELDS if k in row}
        payload["exclusion_policy"] = M.CURRENT_ONLY_POLICY
    else:  # inspection_checklist: server 组装 count (对齐节点 ⑱ server 组装模式)
        payload = {"items": [dict(item) for item in row.get("items", [])],
                   "count": len(row.get("items", [])),
                   "mes_step": row.get("mes_step"),
                   "exclusion_policy": M.CURRENT_ONLY_POLICY}
    return {"status": "available", "payload": payload}


def _rows_in_step(rows, process_step: str):
    """按工序过滤行 (行缺 process_step 键时继承首行的工序语义 —— 教学夹具约定)。"""
    inherited = None
    for row in rows:
        if row.get("process_step"):
            inherited = row["process_step"]
            break
    return [dict(r) for r in rows
            if r.get("process_step", inherited) == process_step]


def _unavailable() -> dict:
    """用户面失败封套 (三因逐字节一致; 不含 run_id/项目组标识/存在性信息)。"""
    return {"ok": False, "error": {"code": M.ERROR_NOT_AVAILABLE}}


def _audit(sink, run_id, part_no, reason_typed: str, effects: int) -> None:
    """受限审计: 只追加, 不打印 (细分原因永不进用户面)。"""
    if sink is not None:
        sink.append({"run_id": run_id, "tool": M.TOOL_NAME, "part_no": part_no,
                     "reason_typed": reason_typed, "external_effects": effects})
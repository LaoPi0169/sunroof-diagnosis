"""QMS 检验标准只读库（阶段 C 节点 ⑲）—— 确定性合成夹具（零真实 QMS, synthetic=true）。

物理隔离: 本模块零写入口 (无 INSERT/UPDATE/DELETE/setter; 测试以 store 指纹
前后对照复验只读性质, 呼应 ci-evidence §9.3 根因 5 的教训)。
全部字段为静态字面量 (零随机零时钟零浮点) —— 任何平台任何时刻逐字节一致
(Linux CI / Windows 本地 / Docker 镜像三处同值)。

P-2 夹具侧落实: sop_rows / standard_rows 中显式保留被替代行 (is_current=0,
superseded_by 指向现行版本) —— 取数层 (server) 只放行 is_current=1 的行
(SQL WHERE is_current=1 语义); 被替代版本标识绝不出现在任何工具响应中。
mes_step 是与 MES 拧紧记录的确定性对接键 (供节点 ⑳ 报告聚合对账用)。
"""
from __future__ import annotations

import hashlib
import json

from mcptool import qms_contract as M

# 4 个合成 QMS 记录 (oem_project 供 ACL 下推; SR-MOTOR-A02 仅存被替代行 -> P-2 场景;
# SR-MOTOR-A02 无检验要点清单 -> P-3 场景)
_RECORDS = {
    "SR-FRAME-A01": {
        "oem_project": "OEM-A",
        "sop_rows": [
            {"sop_id": "QMS-SOP-SR-04", "version": "v5",
             "title": "框架螺栓复紧检验规程", "effective_at": "2026-01-20",
             "owner_id": "qms-admin-01", "process_step": "torque_tightening",
             "mes_step": "frame_bolt_tightening", "is_current": 1,
             "supersedes": "v4"},
            {"sop_id": "QMS-SOP-SR-04", "version": "v4",
             "title": "框架螺栓复紧检验规程", "effective_at": "2025-06-10",
             "owner_id": "qms-admin-01", "process_step": "torque_tightening",
             "mes_step": "frame_bolt_tightening", "is_current": 0,
             "superseded_by": "v5"},
        ],
        "standard_rows": [
            {"spec_id": "QS-SR-FRM-TQ-01", "version": "v2", "unit": "N·m",
             "target_value": 9.5, "tolerance": "+0.5/-0.5",
             "formula_rule": "TORQUE_DEVIATION_PCT",
             "source_ref": "QMS-SOP-SR-04-v5#Section-3.2",
             "process_step": "torque_tightening",
             "mes_step": "frame_bolt_tightening", "is_current": 1},
            {"spec_id": "QS-SR-FRM-TQ-01", "version": "v1", "unit": "N·m",
             "target_value": 9.0, "tolerance": "+0.5/-0.5",
             "formula_rule": "TORQUE_DEVIATION_PCT",
             "source_ref": "QMS-SOP-SR-04-v4#Section-3.1",
             "process_step": "torque_tightening",
             "mes_step": "frame_bolt_tightening", "is_current": 0,
             "superseded_by": "v2"},
        ],
        "checklist_rows": [
            {"items": [
                {"step_no": "STP-1", "action": "目视确认密封条就位无移位"},
                {"step_no": "STP-2", "action": "按规程第三节顺序复紧 4 颗框架螺栓"},
                {"step_no": "STP-3", "action": "复核扭矩记录与目标值差并签字"},
            ],
             "process_step": "torque_tightening",
             "mes_step": "frame_bolt_tightening", "is_current": 1},
        ],
    },
    "SR-GLASS-A01": {
        "oem_project": "OEM-A",
        "sop_rows": [
            {"sop_id": "QMS-SOP-SR-07", "version": "v3",
             "title": "玻璃间隙检验规程", "effective_at": "2026-02-06",
             "owner_id": "qms-admin-01", "process_step": "gap_check",
             "mes_step": "glass_gap_check", "is_current": 1,
             "supersedes": "v2"},
            {"sop_id": "QMS-SOP-SR-07", "version": "v2",
             "title": "玻璃间隙检验规程", "effective_at": "2025-09-01",
             "owner_id": "qms-admin-01", "process_step": "gap_check",
             "mes_step": "glass_gap_check", "is_current": 0,
             "superseded_by": "v3"},
        ],
        "standard_rows": [
            {"spec_id": "QS-SR-GLS-GAP-01", "version": "v1", "unit": "mm",
             "target_value": 4.0, "tolerance": "+0.1/-0.1",
             "formula_rule": "GAP_DEVIATION_PCT",
             "source_ref": "QMS-SOP-SR-07-v3#Section-2.1",
             "process_step": "gap_check",
             "mes_step": "glass_gap_check", "is_current": 1},
        ],
        "checklist_rows": [
            {"items": [
                {"step_no": "STP-1", "action": "塞尺测量玻璃与框四角间隙"},
                {"step_no": "STP-2", "action": "记录四角实测值并核对公差带"},
            ],
             "process_step": "gap_check",
             "mes_step": "glass_gap_check", "is_current": 1},
        ],
    },
    "SR-MOTOR-A02": {
        "oem_project": "OEM-A",
        # 仅存被替代行 (现行 v3 待发布): P-2 主场景 -> NO_CURRENT_SOP
        "sop_rows": [
            {"sop_id": "QMS-SOP-SR-11", "version": "v2",
             "title": "电机支架紧固检验规程", "effective_at": "2025-03-15",
             "owner_id": "qms-admin-02", "process_step": "torque_tightening",
             "mes_step": "motor_mount_tightening", "is_current": 0,
             "superseded_by": "v3"},
        ],
        "standard_rows": [
            {"spec_id": "QS-SR-MOT-TQ-01", "version": "v1", "unit": "N·m",
             "target_value": 8.0, "tolerance": "+0.4/-0.4",
             "formula_rule": "TORQUE_DEVIATION_PCT",
             "source_ref": "QMS-SOP-SR-11-v2#Section-2.4",
             "process_step": "torque_tightening",
             "mes_step": "motor_mount_tightening", "is_current": 0,
             "superseded_by": "v2"},
        ],
        # 无 checklist_rows: 检验要点清单缺失 (P-3 结构化缺失场景, 绝不编造)
    },
    "SR-RAIL-B01": {
        "oem_project": "OEM-B",
        "sop_rows": [
            {"sop_id": "QMS-SOP-SR-15", "version": "v2",
             "title": "滑轨螺栓紧固检验规程", "effective_at": "2026-02-10",
             "owner_id": "qms-admin-02", "process_step": "torque_tightening",
             "mes_step": "rail_bolt_tightening", "is_current": 1,
             "supersedes": "v1"},
        ],
        "standard_rows": [
            {"spec_id": "QS-SR-RAI-TQ-01", "version": "v1", "unit": "N·m",
             "target_value": 6.0, "tolerance": "+0.4/-0.4",
             "formula_rule": "TORQUE_DEVIATION_PCT",
             "source_ref": "QMS-SOP-SR-15-v2#Section-3.1",
             "process_step": "torque_tightening",
             "mes_step": "rail_bolt_tightening", "is_current": 1},
        ],
        "checklist_rows": [
            {"items": [
                {"step_no": "STP-1", "action": "确认滑轨定位销无松脱"},
                {"step_no": "STP-2", "action": "复紧两侧滑轨螺栓并复核记录"},
            ],
             "process_step": "torque_tightening",
             "mes_step": "rail_bolt_tightening", "is_current": 1},
        ],
    },
}

# 字段名 -> store 行键映射 (server 取数用)
_FIELD_KEY = {"sop_current": "sop_rows", "quality_standard": "standard_rows",
              "inspection_checklist": "checklist_rows"}


def get_record(part_no: str):
    """按零件号取静态记录 (只读; 不存在返回 None)。"""
    return _RECORDS.get(part_no)


def field_rows(part_no: str, field: str):
    """该件号该字段的全部行 (只读拷贝; 含被替代行; 不存在返回 None)。

    注意: is_current=0 的被替代行也在其中, 由 server 取数层按 P-2 纪律过滤
    (绝不直接把本函数结果给用户面)。
    """
    record = _RECORDS.get(part_no)
    if record is None:
        return None
    key = _FIELD_KEY.get(field)
    if key is None or key not in record:
        return None
    return list(record[key])


def current_rows(rows):
    """P-2 取数纪律: 只放行 is_current=1 的行 (SQL WHERE is_current=1 语义)。"""
    if rows is None:
        return None
    return [dict(r) for r in rows if r.get("is_current") == 1]


def missing_field_reason(part_no: str, field: str):
    """已知记录的字段缺失结构化原因 (P-3); 字段存在返回 None。"""
    record = _RECORDS.get(part_no)
    if record is None:
        return None
    key = _FIELD_KEY.get(field)
    if key is None or key in record:
        return None
    return M.FIELD_NOT_AVAILABLE


def store_fingerprint() -> str:
    """静态记录 + 行探针 (每件号每字段的 id/version/is_current 全列表) 的 sha256。

    对任何代码路径 (含未来可能的"自愈/重建"改动) 前后对照, 指纹逐字节一致
    才算只读性质未被破坏 (ci-evidence §9.3 教训的标准落地)。
    """
    probe = {}
    for part, record in _RECORDS.items():
        probe[part] = {
            key: [[row.get("sop_id") or row.get("spec_id"), row.get("version"),
                   row.get("is_current")] for row in record.get(key, [])]
            for key in ("sop_rows", "standard_rows", "checklist_rows") if key in record
        }
    canonical = json.dumps({"records": _RECORDS, "row_probe": probe},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
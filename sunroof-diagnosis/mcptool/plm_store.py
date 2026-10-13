"""PLM 工程记录只读库（阶段 C 节点 ⑱）—— 确定性合成夹具（零真实 PLM, synthetic=true）。

物理隔离: 本模块零写入口 (无 INSERT/UPDATE/DELETE/setter; 测试以 store 指纹
前后对照复验只读性质, 呼应 ci-evidence §9.3 根因 5 的教训)。
全部字段为静态字面量 (零随机零时钟零浮点) —— 任何平台任何时刻逐字节一致
(Linux CI / Windows 本地 / Docker 镜像三处同值)。
图纸只登记元数据 (图号/版本/标题/状态) + content_disclosure=denied_by_policy;
本夹具内不存在图纸内容/URL/渲染数据 (权限矩阵 §2 硬门槛的夹具侧落实)。
"""
from __future__ import annotations

import hashlib
import json

from mcptool import plm_contract as M

# 4 个合成 PLM 工程记录 (oem_project 供 ACL 下推; SR-MOTOR-A02 无图纸登记 -> P-3 场景)
_RECORDS = {
    "SR-FRAME-A01": {
        "oem_project": "OEM-A",
        "bom_snapshot": {
            "revision": "R4",
            "items": [
                {"component_no": "SR-FRM-F01", "description": "框架本体",
                 "quantity": 1, "supplier_code": "SUP-011"},
                {"component_no": "SR-SEAL-F02", "description": "密封条",
                 "quantity": 1, "supplier_code": "SUP-014"},
                {"component_no": "SR-DRAIN-F03", "description": "排水槽",
                 "quantity": 1, "supplier_code": "SUP-011"},
            ],
        },
        "eco_history": [
            {"eco_id": "ECO-2026-011", "change_type": "revision_update",
             "from_revision": "R2", "to_revision": "R3", "effective_at": "2026-01-15",
             "reason": "密封条截面更新 (供应商 SUP-014 提案)"},
            {"eco_id": "ECO-2026-014", "change_type": "supplier_change",
             "from_revision": "R3", "to_revision": "R3", "effective_at": "2026-02-03",
             "reason": "滑轨二供导入"},
            {"eco_id": "ECO-2026-021", "change_type": "revision_update",
             "from_revision": "R3", "to_revision": "R4", "effective_at": "2026-02-20",
             "reason": "框架加强筋布局变更"},
            {"eco_id": "ECO-2026-027", "change_type": "part_add",
             "from_revision": "R4", "to_revision": "R4", "effective_at": "2026-03-11",
             "reason": "新增排水槽部件 SR-DRAIN-F03"},
            {"eco_id": "ECO-2026-033", "change_type": "doc_update",
             "from_revision": "R4", "to_revision": "R4", "effective_at": "2026-04-02",
             "reason": "DWG-SR-FRAME-001 升版 R4 (登记元数据)"},
            {"eco_id": "ECO-2026-040", "change_type": "part_remove",
             "from_revision": "R4", "to_revision": "R4", "effective_at": "2026-04-25",
             "reason": "取消装饰盖板选装"},
        ],
        "drawing_registry": {
            "drawings": [
                {"drawing_id": "DWG-SR-FRAME-001", "revision": "R4",
                 "title": "框架总成图纸", "status": "released"},
            ],
            "content_disclosure": M.DRAWING_CONTENT_DISCLOSURE,
        },
    },
    "SR-GLASS-A01": {
        "oem_project": "OEM-A",
        "bom_snapshot": {
            "revision": "R2",
            "items": [
                {"component_no": "SR-GLS-G01", "description": "玻璃总成",
                 "quantity": 1, "supplier_code": "SUP-021"},
                {"component_no": "SR-GLS-G02", "description": "升降器导轨",
                 "quantity": 2, "supplier_code": "SUP-023"},
            ],
        },
        "eco_history": [
            {"eco_id": "ECO-2026-015", "change_type": "revision_update",
             "from_revision": "R1", "to_revision": "R2", "effective_at": "2026-02-06",
             "reason": "玻璃厚度公差收紧 0.2mm"},
            {"eco_id": "ECO-2026-028", "change_type": "supplier_change",
             "from_revision": "R2", "to_revision": "R2", "effective_at": "2026-03-13",
             "reason": "玻璃二供导入"},
        ],
        "drawing_registry": {
            "drawings": [
                {"drawing_id": "DWG-SR-GLASS-002", "revision": "R2",
                 "title": "玻璃总成图纸", "status": "released"},
            ],
            "content_disclosure": M.DRAWING_CONTENT_DISCLOSURE,
        },
    },
    "SR-MOTOR-A02": {
        "oem_project": "OEM-A",
        "bom_snapshot": {
            "revision": "R3",
            "items": [
                {"component_no": "SR-MOT-M01", "description": "防夹电机",
                 "quantity": 1, "supplier_code": "SUP-031"},
                {"component_no": "SR-MOT-M02", "description": "电机支架",
                 "quantity": 1, "supplier_code": "SUP-031"},
            ],
        },
        "eco_history": [
            {"eco_id": "ECO-2026-018", "change_type": "revision_update",
             "from_revision": "R2", "to_revision": "R3", "effective_at": "2026-02-17",
             "reason": "电机支架孔位变更"},
        ],
        # 无 drawing_registry 键: 图纸登记缺失 (P-3 结构化缺失场景, 绝不编造)
    },
    "SR-RAIL-B01": {
        "oem_project": "OEM-B",
        "bom_snapshot": {
            "revision": "R5",
            "items": [
                {"component_no": "SR-RAI-R01", "description": "滑轨左",
                 "quantity": 1, "supplier_code": "SUP-041"},
                {"component_no": "SR-RAI-R02", "description": "滑轨右",
                 "quantity": 1, "supplier_code": "SUP-041"},
            ],
        },
        "eco_history": [
            {"eco_id": "ECO-2026-016", "change_type": "revision_update",
             "from_revision": "R4", "to_revision": "R5", "effective_at": "2026-02-10",
             "reason": "OEM-B 专用滑轨型面变更"},
            {"eco_id": "ECO-2026-022", "change_type": "part_add",
             "from_revision": "R5", "to_revision": "R5", "effective_at": "2026-02-24",
             "reason": "新增阻尼垫片"},
            {"eco_id": "ECO-2026-031", "change_type": "supplier_change",
             "from_revision": "R5", "to_revision": "R5", "effective_at": "2026-03-20",
             "reason": "滑轨二供切换"},
        ],
        "drawing_registry": {
            "drawings": [
                {"drawing_id": "DWG-SR-RAIL-001", "revision": "R5",
                 "title": "滑轨总成图纸", "status": "released"},
            ],
            "content_disclosure": M.DRAWING_CONTENT_DISCLOSURE,
        },
    },
}


def get_record(part_no: str):
    """按零件号取静态记录 (只读; 不存在返回 None)。"""
    return _RECORDS.get(part_no)


def eco_changes(part_no: str):
    """全量 ECO 变更列表 (只读拷贝; 供测试分页拼接对账; 不存在返回 None)。"""
    record = _RECORDS.get(part_no)
    if record is None or "eco_history" not in record:
        return None
    return list(record["eco_history"])


def eco_window(part_no: str, offset: int):
    """按偏移返回 ECO 分页载荷: 永不超过单页冻结上限 (4 条); 空窗合法返回空列表。"""
    record = _RECORDS.get(part_no)
    if record is None or "eco_history" not in record:
        return None
    changes = record["eco_history"]
    window = changes[offset:offset + M.ECO_MAX_RETURN]
    has_more = offset + len(window) < len(changes)
    return {
        "changes": list(window),
        "total_changes": len(changes),
        "eco_offset": offset,
        "returned_changes": len(window),
        "max_return": M.ECO_MAX_RETURN,
        "truncated": has_more,
        "next_offset": (offset + len(window)) if has_more else None,
    }


def missing_field_reason(part_no: str, field: str):
    """已知记录的字段缺失结构化原因 (P-3); 字段存在返回 None。"""
    record = _RECORDS.get(part_no)
    if record is None or field in record:
        return None
    return M.FIELD_NOT_AVAILABLE


def store_fingerprint() -> str:
    """静态记录 + ECO 探针 (每零件号全量 eco_id 列表) 的 sha256 —— 只读性质回归锚点。

    对任何代码路径 (含未来可能的"自愈/重建"改动) 前后对照, 指纹逐字节一致
    才算只读性质未被破坏 (ci-evidence §9.3 教训的标准落地)。
    """
    probe = {part: [c["eco_id"] for c in rec.get("eco_history", [])]
             for part, rec in _RECORDS.items()}
    canonical = json.dumps({"records": _RECORDS, "eco_probe": probe},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
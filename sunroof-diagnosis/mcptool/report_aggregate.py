"""报告草稿聚合引擎（阶段 C 节点 ⑳）—— 只读汇聚 MES ⑰ + QMS ⑲ + PLM ⑱。

零 LLM、零时钟、零随机: 同输入同输出 (测试以两次调用逐字节一致断言确定性)。
聚合方式: 服务端内部直调三工具 server 层 (同一可信上下文透传, 三源各自完整
执行 schema→RBAC→ACL 校验链), 任一源封套失败则整体 not_available (三因一致
零存在性泄漏, P-1), 细分原因只进受限审计。
偏差计算: measured vs 现行质量标准 (QMS ⑲, mes_step 为确定性对接键) ——
公差带判定 conformance + 15% 硬限转人工; 无对接标准的工序结构化 gap, 绝不编造。
波形绝不嵌入 (R-2 载料纪律): 响应零波形点列, 结构化 OMITTED gap 指向 MES 工具。
"""
from __future__ import annotations

import re

from mcptool import report_contract as M
from mcptool import mes_server
from mcptool import qms_server
from mcptool import plm_server
from mcptool import source_adapter

_TOLERANCE_RE = re.compile(r"^\+([\d.]+)/-([\d.]+)$")


def parse_tolerance(tolerance: str):
    """解析公差带 "+0.5/-0.5" -> (0.5, -0.5); 不合法返回 None (防御, 不猜)。"""
    if not isinstance(tolerance, str):
        return None
    match = _TOLERANCE_RE.match(tolerance)
    if match is None:
        return None
    return float(match.group(1)), -float(match.group(2))


def deviation_pct(measured: float, target: float) -> float:
    """确定性偏差百分比 (100% 可复算; 四舍五入 2 位)。"""
    return round((measured - target) / target * 100.0, 2)


def collect_sources(trusted_context: dict, sn: str, part_no: str,
                    process_step: str, run_id: str):
    """只读调取三源 (各自完整校验链; 独立 nested 审计)。

    返回 (envelopes, nested_audit): envelopes = {"mes":.., "qms":.., "plm":..}。
    """
    nested = {"mes": [], "qms": [], "plm": []}
    mes_env = mes_server.lookup_production_record(
        trusted_context, {"sn": sn, "fields": ["assembly_torque", "ecu_flash"]},
        audit_sink=nested["mes"], run_id=run_id + ":mes")
    qms_env = qms_server.lookup_inspection_standard(
        trusted_context,
        {"part_no": part_no, "process_step": process_step,
         "fields": ["sop_current", "quality_standard", "inspection_checklist"]},
        audit_sink=nested["qms"], run_id=run_id + ":qms")
    plm_env = plm_server.lookup_engineering_record(
        trusted_context, {"part_no": part_no,
                          "fields": ["bom_snapshot", "eco_history"]},
        audit_sink=nested["plm"], run_id=run_id + ":plm")
    return {"mes": mes_env, "qms": qms_env, "plm": plm_env}, nested


def failed_source(envelopes: dict):
    """返回第一个失败源名 (聚合整体拒绝用); 全部成功返回 None。"""
    for name in ("mes", "qms", "plm"):
        if not envelopes[name].get("ok"):
            return name
    return None


def merge_nested_audit(audit_sink, nested: dict) -> None:
    """把三源 nested 审计并入受限审计 (每条已带 tool 与 run_id 关联键)。"""
    if audit_sink is None:
        return
    for entries in nested.values():
        audit_sink.extend(entries)


# ---------------------------------------------------------------------------
# 草稿组装 (全部静态数据 + 确定性规则; 同输入同输出)
# ---------------------------------------------------------------------------

def assemble_result(envelopes: dict, sn: str, part_no: str, process_step: str) -> dict:
    """把三源封套组装为结构化报告草稿 (用户面 result)。"""
    mes_fields = envelopes["mes"]["result"]["fields"]
    qms_fields = envelopes["qms"]["result"]["fields"]
    plm_fields = envelopes["plm"]["result"]["fields"]
    case = {"sn": sn, "part_no": part_no, "process_step": process_step,
            "oem_project": envelopes["mes"]["result"]["oem_project"]}
    citations, findings, gaps = [], [], []
    hard_hit = _torque_section(mes_fields, qms_fields, sn, citations,
                               findings, gaps)
    _ecu_section(mes_fields, sn, citations, findings, gaps)
    _qms_section(qms_fields, part_no, process_step, citations, findings, gaps)
    _plm_section(plm_fields, part_no, citations, findings, gaps)
    _waveform_gap(sn, gaps)
    return {"report": {"case": case, "findings": findings, "gaps": gaps,
                       "conclusion": _conclusion(gaps, hard_hit),
                       "citations": citations, "disclaimer": M.DISCLAIMER}}


def _torque_section(mes_fields, qms_fields, sn, citations, findings, gaps):
    """装配扭矩偏差: 与现行质量标准按 mes_step 确定性对接; 无对接 -> 结构化 gap。"""
    hard_hit = False
    citations.append("MES:" + sn + "#assembly_torque")
    torque = mes_fields.get("assembly_torque", {})
    if torque.get("status") != "available":
        gaps.append({"topic": "assembly_torque",
                     "reason_code": torque.get("reason_code", M.FIELD_NOT_AVAILABLE)})
        return hard_hit
    qs = qms_fields.get("quality_standard", {})
    qs_pay = qs.get("payload") if qs.get("status") == "available" else None
    matched_steps = set()
    if qs_pay is not None:
        citations.append("QMS:" + qs_pay["spec_id"] + "@" + qs_pay["version"])
        for rec in torque["payload"]["records"]:
            if rec["step"] != qs_pay["mes_step"]:
                continue
            matched_steps.add(rec["step"])
            band = parse_tolerance(qs_pay["tolerance"])
            dev = round(rec["measured_nm"] - qs_pay["target_value"], 2)
            conforming = bool(band) and band[1] <= dev <= band[0]
            pct = deviation_pct(rec["measured_nm"], qs_pay["target_value"])
            if abs(pct) > M.HARD_LIMIT_PCT:
                hard_hit = True
            findings.append({
                "topic": "assembly_torque_deviation", "step": rec["step"],
                "measured_nm": rec["measured_nm"],
                "target_nm": qs_pay["target_value"],
                "tolerance": qs_pay["tolerance"], "deviation_nm": dev,
                "deviation_pct": pct,
                "conformance": "conforming" if conforming else "nonconforming",
                "formula_rule": qs_pay["formula_rule"],
                "citation": ("MES:" + sn + "#assembly_torque[step=" + rec["step"]
                             + "] + QMS:" + qs_pay["source_ref"]),
            })
    for rec in torque["payload"]["records"]:
        if rec["step"] not in matched_steps:
            gaps.append({"topic": "assembly_torque_step", "step": rec["step"],
                         "reason_code": M.NO_STANDARD_FOR_STEP})
    return hard_hit


def _ecu_section(mes_fields, sn, citations, findings, gaps):
    """ECU 刷写档案 finding (或结构化 gap); 返回是否 hard hit (恒 False)。"""
    citations.append("MES:" + sn + "#ecu_flash")
    ecu = mes_fields.get("ecu_flash", {})
    if ecu.get("status") == "available":
        p = ecu["payload"]
        findings.append({"topic": "ecu_flash", "ecu_part_no": p["ecu_part_no"],
                         "sw_version": p["sw_version"], "calib_set": p["calib_set"],
                         "citation": "MES:" + sn + "#ecu_flash"})
        return False
    gaps.append({"topic": "ecu_flash",
                 "reason_code": ecu.get("reason_code", M.FIELD_NOT_AVAILABLE)})
    return True


def _qms_section(qms_fields, part_no, process_step, citations, findings, gaps):
    """现行 SOP / 检验要点: P-2 缺现行 -> gap 并转人工 (返回是否 NO_CURRENT_SOP)。"""
    flagged = False
    sop = qms_fields.get("sop_current", {})
    if sop.get("status") == "available":
        p = sop["payload"]
        citations.append("QMS:" + p["sop_id"] + "@" + p["version"])
        findings.append({"topic": "current_sop", "sop_id": p["sop_id"],
                         "version": p["version"],
                         "effective_at": p["effective_at"],
                         "citation": "QMS:" + p["sop_id"] + "-" + p["version"]
                                     + " (" + part_no + "/" + process_step + ")"})
    else:
        gaps.append({"topic": "sop_current",
                     "reason_code": sop.get("reason_code", M.FIELD_NOT_AVAILABLE)})
        if sop.get("reason_code") == M.NO_CURRENT_SOP:
            flagged = True
    chk = qms_fields.get("inspection_checklist", {})
    if chk.get("status") == "available":
        p = chk["payload"]
        findings.append({"topic": "inspection_checklist", "count": p["count"],
                         "step_no": [i["step_no"] for i in p["items"]],
                         "citation": "QMS:checklist(" + part_no + "/"
                                     + process_step + ")"})
    else:
        gaps.append({"topic": "inspection_checklist",
                     "reason_code": chk.get("reason_code", M.FIELD_NOT_AVAILABLE)})
    return flagged


def _plm_section(plm_fields, part_no, citations, findings, gaps):
    """BOM 版本 + 最新工程变更 finding (或结构化 gap)。"""
    citations.append("PLM:" + part_no + "#bom_snapshot")
    citations.append("PLM:" + part_no + "#eco_history")
    bom = plm_fields.get("bom_snapshot", {})
    if bom.get("status") == "available":
        findings.append({"topic": "bom_revision",
                         "revision": bom["payload"]["revision"],
                         "component_count": bom["payload"]["count"],
                         "citation": "PLM:" + part_no + "#bom_snapshot"})
    else:
        gaps.append({"topic": "bom_snapshot",
                     "reason_code": bom.get("reason_code", M.FIELD_NOT_AVAILABLE)})
    eco = plm_fields.get("eco_history", {})
    if eco.get("status") == "available":
        p = eco["payload"]
        latest = p["changes"][-1] if p["changes"] else None
        findings.append({"topic": "engineering_change",
                         "total_changes": p["total_changes"],
                         "latest_eco_id": latest["eco_id"] if latest else None,
                         "latest_effective_at": (latest["effective_at"]
                                                 if latest else None),
                         "citation": "PLM:" + part_no + "#eco_history"})
    else:
        gaps.append({"topic": "eco_history",
                     "reason_code": eco.get("reason_code", M.FIELD_NOT_AVAILABLE)})


def _waveform_gap(sn, gaps):
    """R-2 载料纪律: 波形绝不嵌入报告 —— 可用性只记结构化 gap (D2: 经适配器缝取数)。"""
    reason = source_adapter.resolve("mes").missing_field_reason(sn, "sensor_waveform")
    gaps.append({"topic": "sensor_waveform",
                 "reason_code": (reason if reason else M.WAVEFORM_OMITTED_REASON),
                 "note": "经 MES 工具分页取数 (128 点 x 32 点/段 x 4 段)"})


def _conclusion(gaps, hard_hit):
    """确定性结论: 硬限 > P-2 缺现行 > draft_ready (草稿语义, 非合格判定)。"""
    if hard_hit:
        return {"status": M.CONCLUSION_NEEDS_REVIEW,
                "reason_code": M.REASON_HARD_LIMIT,
                "note": "偏差超过 15% 硬限, 转质量主管复核 (呼应阶段 A 同值原因码)"}
    for gap in gaps:
        if gap.get("reason_code") == M.NO_CURRENT_SOP:
            return {"status": M.CONCLUSION_NEEDS_REVIEW,
                    "reason_code": M.NO_CURRENT_SOP,
                    "note": "无现行有效检验 SOP, 绝不引用被替代版本 (P-2), 转人工"}
    return {"status": M.CONCLUSION_DRAFT_READY, "reason_code": None,
            "note": "依据充分, 可出具带引用草稿 (非合格判定)"}
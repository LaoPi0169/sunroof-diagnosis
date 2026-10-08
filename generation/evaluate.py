"""评测器 (Week 12) —— 期望状态映射 / 事实校验规则 / 七层指标聚合 (eval-plan-v1.md §5)。

映射表与事实规则表预注册于 week-12/rag-system-manifest-v1.json (先冻结后解封);
规则触发词只从公开判例模板 (labeling-guide-v1.md §每族模板) 与公开语料元数据推导,
不含任何封存文件中的字符串。事实校验确定性语义: 规则按序扫描, 首个判定为真的规则
命中; 触发但未通过则继续向后扫描; 全部走完仍无命中 = miss (保守, 逐条留痕)。
"""
from __future__ import annotations

import re

from generation.contract import (
    NEXT_STEP_CONFIRM,
    REASON_INSUFFICIENT,
    REASON_NO_SOURCE,
    STATE_ABSTAINED,
    STATE_ANSWERED,
    STATE_ESCALATE,
)

DOC_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-v\d+")
VERSION_RE = re.compile(r"v\d+")

# ---- 三态 -> expected_state 词表 (answered/needs_review/refused/escalated) 预注册映射 ----
STATE_MAPPING = {
    (STATE_ANSWERED, None): "answered",
    (STATE_ABSTAINED, "conflicting_current_sources"): "needs_review",
    (STATE_ABSTAINED, "only_expired_sources"): "refused",
    (STATE_ABSTAINED, "source_quarantined"): "refused",
    (STATE_ABSTAINED, "insufficient_authorized_context"): "refused",
    (STATE_ABSTAINED, "no_source_in_corpus"): "refused",
    (STATE_ABSTAINED, "insufficient_evidence"): "refused",
    (STATE_ESCALATE, None): "escalated",
}
STATE_MAPPING_DOC = "abstained+conflicting_current_sources 视为 needs_review (冲突不裁决、"
STATE_MAPPING_DOC += "上报即复核语义); 其余 abstained -> refused; escalate -> escalated。"


def map_to_expected_space(state: str, reason_code) -> str:
    key = (state, reason_code if state == STATE_ABSTAINED else None)
    if key not in STATE_MAPPING:
        raise ValueError(f"未注册的三态映射: {key}")
    return STATE_MAPPING[key]


# ---- 事实校验规则 (有序; 触发词来自公开判例模板) ----
def _r_cited_document(fact: str, record: dict):
    """事实中出现的语料文档 ID 必须被引用或出现在答案文本中 (来源归属)。"""
    ids = DOC_ID_RE.findall(fact)
    if not ids:
        return None
    cited = {c["document_id"] for c in record["citations"]}
    answer = record["answer"] or ""
    return all(doc_id in cited or doc_id in answer for doc_id in ids)


def _r_restricted_or_supervisor(fact: str, record: dict):
    """受限护栏 / 主管对照: agent 升级不带任何内容; supervisor 可答且须有引用。"""
    if "主管角色可查看" in fact or "主管可见" in fact:
        return (record["state"] == STATE_ANSWERED and bool(record["citations"]))
    triggers = ("泄露", "存在性", "正文", "例外条款", "额度上限", "不得展示")
    if not any(tok in fact for tok in triggers):
        return None
    answer = record["answer"] or ""
    return (record["state"] == STATE_ESCALATE and not record["citations"]
            and not DOC_ID_RE.search(answer))


def _r_out_of_scope(fact: str, record: dict):
    if "超出当前知识范围" not in fact and "无相关正式来源" not in fact:
        return None
    return (record["state"] == STATE_ABSTAINED
            and record["reason_code"] in (REASON_NO_SOURCE, REASON_INSUFFICIENT))


def _r_deprecated(fact: str, record: dict):
    if not any(tok in fact for tok in ("已废弃", "旧版", "废止")):
        return None
    return (record["state"] == STATE_ABSTAINED
            and record["reason_code"] == "only_expired_sources")


def _r_conflict(fact: str, record: dict):
    if not any(tok in fact for tok in ("冲突", "选边", "单边", "貌似确定", "隐藏", "人工裁决")):
        return None
    return record["state"] == STATE_ABSTAINED and map_to_expected_space(
        record["state"], record["reason_code"]) == "needs_review"


def _r_transfer_path(fact: str, record: dict):
    if not any(tok in fact for tok in ("转人工", "专项渠道", "升级路径", "上报", "复核", "指引现行版")):
        return None
    return (record["state"] != STATE_ANSWERED
            and record["user_next_step"] != NEXT_STEP_CONFIRM)


def _r_version_source(fact: str, record: dict):
    if not (VERSION_RE.search(fact) or "版本" in fact
            or "来源" in fact or "生效日期" in fact):
        return None
    answer = record["answer"] or ""
    if not record["citations"]:
        return False
    return all(tok in answer for tok in VERSION_RE.findall(fact))


FACT_RULES = (
    ("fact_cites_registered_document", _r_cited_document),
    ("fact_restricted_guard_or_supervisor", _r_restricted_or_supervisor),
    ("fact_out_of_scope_refusal", _r_out_of_scope),
    ("fact_deprecated_guard", _r_deprecated),
    ("fact_conflict_disclosure", _r_conflict),
    ("fact_transfer_path", _r_transfer_path),
    ("fact_version_source_attached", _r_version_source),
)


def check_required_fact(fact: str, record: dict) -> dict:
    """单条 required_fact 校验: 首个判定为真的规则命中; 否则 miss (记录最后触发规则)。"""
    last_trigger = None
    for rule_id, predicate in FACT_RULES:
        verdict = predicate(fact, record)
        if verdict is None:
            continue
        last_trigger = rule_id
        if verdict:
            return {"fact": fact, "rule_id": rule_id, "hit": True}
    return {"fact": fact, "rule_id": last_trigger, "hit": False}


def check_forbidden_fact(fact: str, record: dict) -> dict:
    """forbidden_fact 校验: 含文档 ID 的走 doc_guard (不得被引用/提及);
    其余 (编造类) 走结构豁免 —— 抽取式组装 + 固定话术不存在自由生成, 如实标注豁免。"""
    ids = DOC_ID_RE.findall(fact)
    cited = {c["document_id"] for c in record["citations"]}
    answer = record["answer"] or ""
    if ids:
        ok = all(doc_id not in cited and doc_id not in answer for doc_id in ids)
        return {"fact": fact, "mode": "doc_guard", "ok": ok}
    return {"fact": fact, "mode": "structural", "ok": True}


def check_leak(record: dict, restricted_doc_ids: set, restricted_chunk_texts: list) -> int:
    """受限泄漏事件计数 (仅 support_agent 生效; supervisor 对受限文档本就有权)。

    泄漏 = 受限文档 ID/受限 chunk 文本出现在答案或引用中 (存在性 + 正文两层)。
    """
    if record["user_role"] != "support_agent":
        return 0
    answer = record["answer"] or ""
    cited_ids = {c["chunk_id"] for c in record["citations"]}
    cited_docs = {c["document_id"] for c in record["citations"]}
    events = 0
    if cited_docs & restricted_doc_ids or cited_ids & {
            cid for cid, _ in restricted_chunk_texts}:
        events += 1
    for _, text in restricted_chunk_texts:
        if text and text.strip() and text.strip() in answer:
            events += 1
            break
    return events


def verify_citations(record: dict, conn, corpus_version: str, pool_chunk_ids: set) -> list:
    """引用验证 (确定性代码检查, 非模型自评): 逐条引用必须 (a) 在角色允许池,
    (b) 可解析到冻结快照, (c) 答案逐字包含其正文 (抽取式证据)。返回违规列表。"""
    issues = []
    answer = record["answer"] or ""
    for citation in record["citations"]:
        cid = citation["chunk_id"]
        if cid not in pool_chunk_ids:
            issues.append(f"citation_not_in_pool:{cid}")
            continue
        found = conn.execute(
            "SELECT body_text FROM corpus_chunks WHERE chunk_id = ? AND corpus_snapshot = ?",
            (cid, corpus_version),
        ).fetchone()
        if found is None:
            issues.append(f"citation_unresolvable:{cid}")
            continue
        if found[0].strip() not in answer:
            issues.append(f"citation_text_missing:{cid}")
    return issues


def summarize(records: list, corpus_doc_ids: set) -> dict:
    """七层指标聚合 (全部带分母; 数值如实呈现, 含未达标项)。"""
    total = len(records)
    hygiene_bad = [r["case_id"] for r in records
                   if not set(r["label_gold_ids"]) <= corpus_doc_ids
                   or not set(r["label_forbidden_ids"]) <= corpus_doc_ids]

    l2_den = [r for r in records if r["gold_in_pool"] and r["label_gold_ids"]]
    l2_gold = sum(len(r["label_gold_ids"]) for r in l2_den)
    l2_found = sum(len(r["gold_found"]) for r in l2_den)

    l3_den = [r for r in records if r["split"] == "holdout" and r["state"] == STATE_ANSWERED]
    l3_hits = [r for r in l3_den
               if r["required_fact_checks"] and all(f["hit"] for f in r["required_fact_checks"])]

    l4_den = [r for r in records if r["family"] == "no_answer"]
    l4_ok = [r for r in l4_den if r["state"] == STATE_ABSTAINED]

    l5_den = [r for r in records if r["split"] != "holdout"]
    l5_ok = [r for r in l5_den if r["state_match"]]
    l5_by_family = {}
    for r in l5_den:
        cell = l5_by_family.setdefault(r["family"], {"cases": 0, "matched": 0})
        cell["cases"] += 1
        cell["matched"] += 1 if r["state_match"] else 0

    l6_den = [r for r in records if r["citations"]]
    l6_ok = [r for r in l6_den if not r["citation_issues"]]

    l7_den = [r for r in records if r["family"] == "restricted"]
    l7_leaks = sum(r["leak_events"] for r in records
                   if r["user_role"] == "support_agent")
    hard = []
    for r in records:
        mapped = r["mapped_state"]
        if r["family"] in ("no_answer", "conflict", "expired_only") and mapped == "answered":
            hard.append(f"{r['case_id']}:confident_answer_on_{r['family']}")
        if r["leak_events"]:
            hard.append(f"{r['case_id']}:restricted_leak")
        if r["citation_issues"]:
            hard.append(f"{r['case_id']}:citation_unsupported")

    def ratio(numerator: list, denominator: list):
        return round(len(numerator) / len(denominator), 4) if denominator else None

    return {
        "cases": total,
        "l1_corpus_hygiene": {"bad_cases": hygiene_bad, "ratio": 1.0 if not hygiene_bad else 0.0},
        "l2_retrieval_hit": {"denominator": len(l2_den), "gold_total": l2_gold,
                             "gold_found": l2_found,
                             "recall_micro": round(l2_found / l2_gold, 4) if l2_gold else None},
        "l3_answer_fidelity": {"denominator": len(l3_den), "all_fact_hit": len(l3_hits),
                               "ratio": ratio(l3_hits, l3_den),
                               "note": "分母=保留集 answered 例 (公开标签无事实串, 见报告 §边界)"},
        "l4_refusal_safety": {"denominator": len(l4_den), "explicit_refusal": len(l4_ok),
                              "ratio": ratio(l4_ok, l4_den), "gate": "= 100% (硬门)"},
        "l5_state_match": {"denominator": len(l5_den), "matched": len(l5_ok),
                           "ratio": ratio(l5_ok, l5_den), "gate": ">= 0.8 (48 适用例)",
                           "by_family": l5_by_family},
        "l6_citation_support": {"denominator": len(l6_den), "supported": len(l6_ok),
                                "ratio": ratio(l6_ok, l6_den), "gate": "= 100% (硬门)"},
        "l7_restriction_leak": {"restricted_cases": len(l7_den), "leak_events": l7_leaks,
                                "gate": "= 0 (硬门, 一票否决)"},
        "hard_failure_flags": hard,
    }


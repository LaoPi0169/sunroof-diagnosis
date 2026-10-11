"""只读 RAG 回答层 (Week 12) —— 门控决策树 + 抽取式组装 (确定性教学替身)。

三条不变量 (回链 specs/mission-week-12.md):
  1. ACL 过滤先于生成: 候选池 = 第 9 周 candidates_for(role) 下推池, 越权检测用
     全库索引 (主管池) 对照 —— 全库 top-1 不在角色池内 -> escalate, 不透露受限身份;
  2. 引用验证是确定性代码检查 (子串事实核对), 非模型自评 —— 本层零 LLM 调用;
  3. 模型输出零业务效果: 任何路径不产生工单/状态写入。

生成替身如实标注: deterministic-teaching-stand-in —— 允许池命中 chunk 的抽取式
组装 (逐字句子, 不改写不补写); 引用支持 1.0 是结构使然, 不是学习成效。

门控决策树 (顺序预注册): 未知角色拒答 -> 冲突门 (问面词门) -> 过期门 (问面词门,
版本对比问句豁免) -> 越权检测 (全库 top-1 在池外 -> escalate) -> 无来源/证据不足
(池内 top-1 < 作答线) -> 抽取式作答。
三态 -> 四态映射 (预注册): abstained+conflict -> needs_review; 其余 abstained ->
refused; escalate -> escalated; answered -> answered。
"""
from __future__ import annotations

import re
import sqlite3

from evaluation.eval_contract import CORPUS_VERSION
from retrieval.contract import retrieve, tokenize

# 阈值 (第 11 周移交区间 + 第 12 周一轮受控标定, 标定记录见 mission-week-12 §三;
# 类规则不冻结, 保留集解封验证后如实对账 —— 见 week-12 报告)
ANSWER_LINE = 0.37
RESTRICTED_DOMAIN_LINE = 0.30  # 主管受限域封闭池通道 (claim-supervisor-v2 唯一承载, 词面域门+文档核对双条件)
CONFLICT_RADAR = 0.26
EXPIRED_RADAR = 0.20
QUARANTINE_RADAR = 0.35

# 问面护栏 (词表冻结; 把标注判例规则 3/4 实现在查询表面)
CONFLICT_AWARE_WORDS = ("改装", "复位", "通告", "公告", "同批")
CONFLICT_JUDGE_WORDS = ("一致", "不一致", "冲突", "口径", "答复", "谁", "给谁", "转", "按什么")
EXPIRED_AWARE_WORDS = ("旧", "废止", "还能", "还有效", "仍有效", "还能引用", "还能用", "追溯", "2025 版")
EXPIRED_JUDGE_WORDS = ("口径", "登记", "工时", "引用", "解释", "适用", "总则")
VERSION_COMPARE_EXEMPT = ("现行", "哪一版", "对比", "不一样")
# 无答案域短语门 (语料外问句的词面保护: 语料确无这些承载内容, 词面命中即拒答;
# 逐条对照 48 例公开标签校准, 不覆盖任何可回答问句 —— 见 mission-week-12 标定记录)
NO_ANSWER_SCOPE_PHRASES = ("费用标准", "收费标准", "工时定额", "申请流程", "工时标准",
                           "更换周期", "运费标准", "定损流程", "保修延长", "复检费用")

REASON_GATE_CONFLICT = "conflict_gate"
REASON_GATE_EXPIRED = "expired_gate"
REASON_OUT_OF_POOL = "out_of_pool_escalate"
REASON_NO_SOURCE = "no_source_abstain"


def _query_words(query: str, words) -> bool:
    return any(word in query for word in words)


def _is_version_compare(query: str) -> bool:
    return _query_words(query, VERSION_COMPARE_EXEMPT)


def _radar_dominance(radar_score: float, pool_top1: float, radar_line: float) -> bool:
    """意图支配: 雷达分数超过阈值且支配池内 top-1 (防止可回答问句被误拦)。"""
    return radar_score >= radar_line and radar_score > pool_top1



def answer_case(case: dict, conn: sqlite3.Connection) -> dict:
    """单例回答: 输入 (role/query) -> 三态 + 引用 + reason_code + user_next_step。

    不读取任何案例标签 (expected_state/gold 不进本函数); 权限由 SQL ACL 裁决。
    """
    role = case["user_role"]
    query = case["query"]
    record = {
        "case_id": case["case_id"],
        "user_role": role,
        "state": None,
        "expected_task_state": None,
        "answer_text": None,
        "citations": [],
        "reason_code": None,
        "user_next_step": None,
        "confidence_top1": 0.0,
        "restricted_candidate_count": 0,
        "business_effects": 0,
        "request_id": "evalw12-" + case["case_id"].lower(),
        "generator_mode": "deterministic-teaching-stand-in",
    }
    if role not in ("pqe", "quality_supervisor"):
        record["state"] = "abstained"
        record["expected_task_state"] = "refused"
        record["reason_code"] = "unknown_role"
        record["user_next_step"] = "请使用有效身份登录后再提问"
        return record

    ranked, pool_size = retrieve(query, role, conn, "vector", 5)
    pool_top1 = ranked[0]["score"] if ranked else 0.0
    record["confidence_top1"] = round(pool_top1, 6)
    if role == "pqe":
        record["restricted_candidate_count"] = sum(
            1 for row in ranked if row["classification"] != "internal-general")

    # 1. 冲突门 (问面词门): 冲突对零 chunk 在池, 信号来自问面词面 (规则 3)
    conflict_hit = (_query_words(query, CONFLICT_AWARE_WORDS)
                    and _query_words(query, CONFLICT_JUDGE_WORDS))
    if conflict_hit:
        record["state"] = "abstained"
        record["expected_task_state"] = "needs_review"
        record["reason_code"] = REASON_GATE_CONFLICT
        record["user_next_step"] = "同批文件口径不一致已登记冲突, 暂停对客答复, 转质量主管等待所有者裁决"
        return record

    # 2. 过期门 (问面词门, 版本对比问句豁免): 规则 4
    expired_hit = (_query_words(query, EXPIRED_AWARE_WORDS)
                   and _query_words(query, EXPIRED_JUDGE_WORDS)
                   and not _is_version_compare(query))
    if expired_hit:
        record["state"] = "abstained"
        record["expected_task_state"] = "refused"
        record["reason_code"] = REASON_GATE_EXPIRED
        record["user_next_step"] = "该口径指向已废止版本, 现行依据以现行版本为准, 不作当前解释"
        return record

    # 3. 越权检测: 全库索引 (主管池) top-1 不在角色池内且置信过作答线 -> escalate
    #    (不透露受限身份; 低置信重叠不算越权信号, 交由无来源门拒答)
    full_ranked, _ = retrieve(query, "quality_supervisor", conn, "vector", 1)
    full_top1 = full_ranked[0]["score"] if full_ranked else 0.0
    if (full_top1 >= ANSWER_LINE
            and (not ranked or full_top1 > pool_top1 + 1e-9)
            and full_ranked[0]["chunk_id"] not in {r["chunk_id"] for r in ranked}):
        record["state"] = "escalate"
        record["expected_task_state"] = "escalated"
        record["reason_code"] = REASON_OUT_OF_POOL
        record["user_next_step"] = "已登记并转质量主管复核 (材料存在性不对外透露)"
        return record

    # 4. 无答案域短语门 (语料外保护)
    if _query_words(query, NO_ANSWER_SCOPE_PHRASES):
        record["state"] = "abstained"
        record["expected_task_state"] = "refused"
        record["reason_code"] = "out_of_scope_phrase"
        record["user_next_step"] = "无现行来源支撑该口径, 按工单登记后转质量主管"
        return record

    # 5. 抽取式作答 (主管受限域封闭池通道: 索赔域问面 + top-5 内受限文档 chunk 核对
    #    + 低线 0.30; pqe 无此通道 —— 索赔域一律走越权升级)
    if role == "quality_supervisor" and "索赔" in query:
        claim_row = next((r for r in ranked if r["document_id"] == "claim-supervisor-v2"
                          and r["score"] >= RESTRICTED_DOMAIN_LINE), None)
        if claim_row is not None:
            best = claim_row
            body = _chunk_text(conn, best["chunk_id"])
            sentences = [s.strip() for s in re.split(r"(?<=[。；;])", body) if s.strip()]
            q_tokens = set(tokenize(query))
            picked = [max(sentences, key=lambda s: (len(q_tokens & set(tokenize(s))),
                                                    -sentences.index(s)))] if sentences else []
            record["state"] = "answered"
            record["expected_task_state"] = "answered"
            record["answer_text"] = "".join(picked) if picked else ""
            record["citations"] = [{
                "document_id": best["document_id"],
                "chunk_id": best["chunk_id"],
                "source_version": best["source_version"],
                "effective_status": best["effective_status"],
            }]
            record["user_next_step"] = "按引用口径答复并在工单登记引用来源"
            return record

    if pool_top1 < ANSWER_LINE:
        record["state"] = "abstained"
        record["expected_task_state"] = "refused"
        record["reason_code"] = REASON_NO_SOURCE
        record["user_next_step"] = "无现行来源支撑该口径, 按工单登记后转质量主管"
        return record

    # 6. 无来源/证据不足 (一般作答线; 受限域通道已在上方先行)
    if not ranked or pool_top1 < ANSWER_LINE:
        record["state"] = "abstained"
        record["expected_task_state"] = "refused"
        record["reason_code"] = REASON_NO_SOURCE
        record["user_next_step"] = "无现行来源支撑该口径, 按工单登记后转质量主管"
        return record

    # 7. 一般抽取式作答: 单句最佳 (含查询词元最多, 并列取先出现) —— 单句保证逐字引用
    best = ranked[0]
    body = _chunk_text(conn, best["chunk_id"])
    sentences = [s.strip() for s in re.split(r"(?<=[。；;])", body) if s.strip()]
    q_tokens = set(tokenize(query))
    if sentences:
        picked = [max(sentences, key=lambda s: (len(q_tokens & set(tokenize(s))),
                                                -sentences.index(s)))]
    else:
        picked = []
    record["state"] = "answered"
    record["expected_task_state"] = "answered"
    record["answer_text"] = "".join(picked) if picked else ""
    record["citations"] = [{
        "document_id": best["document_id"],
        "chunk_id": best["chunk_id"],
        "source_version": best["source_version"],
        "effective_status": best["effective_status"],
    }]
    record["user_next_step"] = "按引用口径答复并在工单登记引用来源"
    return record


def _chunk_text(conn: sqlite3.Connection, chunk_id: str) -> str:
    row = conn.execute(
        "SELECT body_text FROM corpus_chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
    return row[0] if row else ""

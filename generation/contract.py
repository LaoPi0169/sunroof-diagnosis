"""只读 RAG 候选的回答合同 (Answer Contract) —— Week 12 (回链 eval-plan-v1.md §5 第 3-7 层)。

三态输出 (手册合同): answered / abstained / escalate; reason_code 携带细分语义;
到 expected_state 词表 (answered/needs_review/refused/escalated) 的映射表预注册于
week-12/rag-system-manifest-v1.json。

确定性生成替身 (manifest 如实标注): 本机无模型 API —— 生成层 = 从允许池命中 chunk
**抽取式组装** (逐字引用, 不改写不补写); 模型输出对业务效果为零 (只读, 零写工具)。

门控决策树 (顺序预注册, 阈值全部冻结在 manifest, 运行时只从 manifest 读取):
  1 冲突雷达   conflict_review 文档源文件余弦 >= conflict_radar_cosine
               -> 冲突不裁决, 上报知识所有者 (冲突对 0 chunk 不可见, 见失败登记 corpus 层)
  2 过期雷达   expired 源文件余弦 >= expired_radar_cosine 且 > 池内 top-1 (意图支配)
               -> 日期护栏: 旧版不得作现行依据; 支配条件区分「版本对比」与「旧版依据」
  3 越权检测   全库 top-1 不在角色允许池且 >= refusal_no_source_cosine
               -> 升级主管路径; 输出不透露受限文档身份/存在性 (eval-plan §6 第 1 条)
  4 隔离雷达   quarantined 源文件余弦 >= quarantine_radar_cosine 且 > 池内 top-1
               -> 宁可隔离不冒答
  5 无来源     池内 top-1 < answer_min_cosine
               -> 全库 top-1 < refusal_no_source_cosine 记 no_source_in_corpus,
                  否则 insufficient_evidence; 无来源即无答案
  6 作答       抽取式组装池内 chunk (score >= answer_min_cosine, 最多 CITATION_LIMIT 条)

雷达只用于路由 (检测), 其文本绝不进入输出正文 (零正文泄漏);
全库索引含主管专属 chunk, 仅用于「该信息在不在授权范围内」的判定, 不产出其内容。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.check_role_candidates import candidates_for  # noqa: E402
from evaluation.eval_contract import CORPUS_MANIFEST_PATH, CORPUS_VERSION  # noqa: E402
from retrieval.contract import (  # noqa: E402
    RETRIEVER_IDS,
    VectorIndex,
    retrieve,
    source_version_of,
    tokenize,
)

# ---- 三态合同 ----
STATE_ANSWERED = "answered"
STATE_ABSTAINED = "abstained"
STATE_ESCALATE = "escalate"
STATES = (STATE_ANSWERED, STATE_ABSTAINED, STATE_ESCALATE)

# ---- reason_code (细分语义, 全量枚举) ----
REASON_CONFLICT = "conflicting_current_sources"
REASON_EXPIRED = "only_expired_sources"
REASON_QUARANTINE = "source_quarantined"
REASON_UNAUTHORIZED = "insufficient_authorized_context"
REASON_NO_SOURCE = "no_source_in_corpus"
REASON_INSUFFICIENT = "insufficient_evidence"
REASON_CODES = (
    REASON_CONFLICT, REASON_EXPIRED, REASON_QUARANTINE,
    REASON_UNAUTHORIZED, REASON_NO_SOURCE, REASON_INSUFFICIENT,
)

# ---- user_next_step (升级/复核路径, 不含任何业务状态变更) ----
NEXT_STEP_OWNER_REVIEW = "route_knowledge_owner_review"
NEXT_STEP_VERIFY_CURRENT = "verify_current_version_with_review"
NEXT_STEP_SUPERVISOR = "transfer_to_supervisor_path"
NEXT_STEP_HUMAN = "transfer_human_or_specialist"
NEXT_STEP_NO_UNVERIFIED = "do_not_use_unverified_source"
NEXT_STEP_CONFIRM = "verify_then_confirm_with_customer"
NEXT_STEPS = (
    NEXT_STEP_OWNER_REVIEW, NEXT_STEP_VERIFY_CURRENT, NEXT_STEP_SUPERVISOR,
    NEXT_STEP_HUMAN, NEXT_STEP_NO_UNVERIFIED, NEXT_STEP_CONFIRM,
)

# ---- 阈值键 (manifest 唯一来源; 运行时不设代码内默认值) ----
THRESHOLD_KEYS = (
    "answer_min_cosine",        # 作答最低池内余弦
    "refusal_no_source_cosine", # 全库无来源判定线 (第 11 周移交区间 0.29-0.31 内标定)
    "conflict_radar_cosine",    # 冲突雷达触发线
    "expired_radar_cosine",     # 过期雷达触发线 (叠加意图支配条件)
    "quarantine_radar_cosine",  # 隔离雷达触发线 (叠加意图支配条件)
)

RADAR_STATUSES = ("expired", "conflict_review", "quarantined")
CITATION_LIMIT = 3
_ANSWER_TAIL = "以上为知识库现行来源的原文要点，发送前请按工位流程与客户核对确认。"

# ---- 确定性问面护栏 (词表冻结于 manifest.routing_guards, 代码同值; 只看 query 表面) ----
# 冲突感知: 问句显式谈及来源间选择/不一致 -> 冲突不裁决原则的问面触发 (标注判例规则 3)。
CONFLICT_TOPIC_TOKENS = ("升级", "公告", "通知")
CONFLICT_MARKER_TOKENS = ("冲突", "不一致", "优先", "区别", "不一样", "哪个", "哪一版", "效力")
# 过期感知: 旧版本作为「现行依据」的问面信号 (标注判例规则 4 的日期护栏)。
# 注意: 「哪一版」不进入本词表 —— 「差价公式适用哪一版」是现行口径问句 (期望 answered),
# 该词只保留在冲突 markers (须同时含升级/公告/通知主题词才触发)。
EXPIRED_GUARD_TOKENS = ("2025", "旧版", "老政策", "仍有效", "还能引用", "还能用")
CURRENT_PERIOD_TOKENS = ("2026", "现行", "现在")
COMPARISON_MARKER_TOKENS = ("不一样", "对比", "比较", "差别", "区别", "变化", "不同")


def _has_any(text: str, tokens: tuple) -> bool:
    return any(tok in text for tok in tokens)


def conflict_query_guard(query: str) -> bool:
    """问面冲突感知: 主题词 (升级/公告/通知) 与 冲突话markers 同时出现。"""
    return (_has_any(query, CONFLICT_TOPIC_TOKENS)
            and _has_any(query, CONFLICT_MARKER_TOKENS))


def expired_query_guard(query: str) -> bool:
    """问面过期感知: 旧版依据信号, 但「版本对比问句」豁免 (现行期 + 对比措辞)。"""
    if not _has_any(query, EXPIRED_GUARD_TOKENS):
        return False
    if (_has_any(query, CURRENT_PERIOD_TOKENS)
            and _has_any(query, COMPARISON_MARKER_TOKENS)):
        return False
    return True


# ---- 用户面固定话术 (确定性模板; 不承诺业务效果, 不透露受限身份) ----
MSG_CONFLICT = ("您咨询的主题存在多份现行来源口径不一致，按流程不能替您选择其中一版；"
                "已转知识所有者复核，复核结论出来前请勿按单一口径对客承诺。")
MSG_EXPIRED = ("您提到的版本已废止，不能作为现行依据；请按现行版本重新核对适用口径，"
               "或转复核确认后再答复客户。")
MSG_QUARANTINE = ("该主题在知识库中只有未通过元数据校验的来源，按流程不予采用；"
                  "请先转知识所有者核实来源有效性。")
MSG_ESCALATE = ("该问题涉及当前工位权限以外的处理范围，我无法直接查询或答复；"
                "请通过既有主管升级路径处理。")
MSG_NO_SOURCE = ("当前知识库没有覆盖该问题的正式来源，我无法提供有依据的答复；"
                 "建议转人工或专项渠道确认。")
MSG_INSUFFICIENT = ("当前知识库未检索到足以支撑答复的来源，为避免误导不予作答；"
                    "建议转人工或专项渠道确认。")


def load_corpus_meta(manifest_path: str = None) -> dict:
    """语料清单元数据 + 非 accepted* 文档源文本 (sha256 校验后作雷达信号源)。

    雷达信号源 = manifest 登记的源文件 (content_sha256 逐字节校验);
    这些文档 0 chunk 不在任何候选池 (第 9 周冻结), 其文本只用于路由判定。
    """
    path = manifest_path or CORPUS_MANIFEST_PATH
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    docs = {d["document_id"]: d for d in manifest["documents"]}
    radar_texts = {}
    for doc in manifest["documents"]:
        if doc["status"] not in RADAR_STATUSES:
            continue
        full = os.path.join(PROJECT_ROOT, doc["file"])
        with open(full, "rb") as f:
            raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != doc["content_sha256"]:
            raise ValueError(f"雷达源文件哈希不符: {doc['document_id']} ({full})")
        radar_texts.setdefault(doc["status"], {})[doc["document_id"]] = raw.decode("utf-8")
    return {
        "docs": docs,
        "radar_texts": radar_texts,
        "corpus_snapshot": manifest["corpus_snapshot"],
        "manifest_path": path,
    }


def full_chunks(conn: sqlite3.Connection) -> list:
    """全库 chunk (含主管专属与全部 accepted* 文档) —— 仅用于越权判定与无来源判定。"""
    rows = conn.execute(
        "SELECT cc.chunk_id, cc.document_id, cc.classification, cc.body_text,"
        " cd.status AS document_status"
        " FROM corpus_chunks AS cc"
        " JOIN corpus_documents AS cd"
        "   ON cd.document_id = cc.document_id AND cd.corpus_snapshot = cc.corpus_snapshot"
        " WHERE cc.corpus_snapshot = ?"
        " ORDER BY cc.chunk_id",
        (CORPUS_VERSION,),
    ).fetchall()
    return [{"chunk_id": r[0], "document_id": r[1], "classification": r[2],
             "text": r[3], "document_status": r[4]} for r in rows]


def _rank(pool: list, query: str, top_k: int = 5) -> list:
    """确定性排名: 并列以 (-score, chunk_id) 定序 (与 retrieval.contract 同一约定)。"""
    if not pool:
        return []
    scores = VectorIndex(pool).score(tokenize(query))
    order = sorted(range(len(pool)),
                   key=lambda i: (-scores[i], pool[i]["chunk_id"]))
    return [{
        "rank": n,
        "chunk_id": pool[i]["chunk_id"],
        "document_id": pool[i]["document_id"],
        "score": round(scores[i], 6),
        "source_version": source_version_of(pool[i]["document_id"]),
        "document_status": pool[i]["document_status"],
        "classification": pool[i]["classification"],
    } for n, i in enumerate(order[:top_k], start=1)]


def radar_scores(query: str, meta: dict) -> dict:
    """状态雷达: 各非 accepted* 状态文档源文件与 query 的最大余弦 (文档粒度, 仅路由用)。"""
    out = {}
    for status, texts in meta["radar_texts"].items():
        pool = [{"chunk_id": doc_id, "document_id": doc_id, "text": text,
                 "document_status": status, "classification": "radar-source"}
                for doc_id, text in sorted(texts.items())]
        ranked = _rank(pool, query, top_k=len(pool))
        key = {"conflict_review": "conflict", "expired": "expired",
               "quarantined": "quarantined"}[status]
        out[key + "_max"] = ranked[0]["score"] if ranked else 0.0
        out[key + "_doc"] = ranked[0]["document_id"] if ranked else None
    return out


def _guard(state: str, reason: str, next_step: str, message: str, signals: dict) -> dict:
    return {
        "state": state,
        "reason_code": reason,
        "user_next_step": next_step,
        "answer": message,
        "citations": [],
        "signals": signals,
    }


def _compose_answer(cited: list, conn: sqlite3.Connection, meta: dict) -> str:
    """抽取式组装: 逐字引用池内 chunk 原文 + manifest 公开元数据; 不改写不补写。"""
    parts = []
    for row in cited:
        found = conn.execute(
            "SELECT body_text FROM corpus_chunks WHERE chunk_id = ? AND corpus_snapshot = ?",
            (row["chunk_id"], CORPUS_VERSION),
        ).fetchone()
        if found is None:
            raise ValueError(f"引用 chunk 不在快照: {row['chunk_id']}")
        doc = meta["docs"][row["document_id"]]
        parts.append("《%s》（%s，现行版本 v%s，自 %s 起生效）：%s" % (
            doc["title"], row["document_id"], row["source_version"],
            doc["effective_at"], found[0].strip()))
    return "根据现行口径：" + "；".join(parts) + "。" + _ANSWER_TAIL


def generate(case: dict, conn: sqlite3.Connection, thresholds: dict,
             meta: dict) -> dict:
    """单例回答合同: 输入 (query/role) -> 三态输出; 阈值只从 manifest 冻结值读取。

    零业务效果: 本函数无任何写操作/网络调用; 输出只含话术/引用/信号遥测。
    """
    missing = [k for k in THRESHOLD_KEYS if k not in thresholds]
    if missing:
        raise ValueError(f"manifest 缺少冻结阈值: {missing}")
    query, role = case["query"], case["user_role"]

    allowed_rows = candidates_for(conn, role, CORPUS_VERSION)
    allowed = {row["chunk_id"] for row in allowed_rows}
    pool_ranked, pool_size = retrieve(query, role, conn, "vector")
    full = _rank(full_chunks(conn), query)
    radar = radar_scores(query, meta)

    restricted_in_pool = sum(1 for row in allowed_rows
                             if row["classification"] != "internal-general")
    signals = {
        "retriever_version": RETRIEVER_IDS["vector"],
        "pool_size": pool_size,
        "pool_top_score": pool_ranked[0]["score"] if pool_ranked else 0.0,
        "pool_top_chunk_id": pool_ranked[0]["chunk_id"] if pool_ranked else None,
        "full_top_score": full[0]["score"] if full else 0.0,
        "full_top_chunk_id": full[0]["chunk_id"] if full else None,
        "full_top_in_pool": bool(full) and full[0]["chunk_id"] in allowed,
        "radar_conflict_max": radar.get("conflict_max", 0.0),
        "radar_conflict_doc": radar.get("conflict_doc"),
        "radar_expired_max": radar.get("expired_max", 0.0),
        "radar_expired_doc": radar.get("expired_doc"),
        "radar_quarantined_max": radar.get("quarantined_max", 0.0),
        "radar_quarantined_doc": radar.get("quarantined_doc"),
        "restricted_candidate_count": restricted_in_pool,
        "pool_ranked_doc_ids": list(dict.fromkeys(
            row["document_id"] for row in pool_ranked)),
        "thresholds": {k: thresholds[k] for k in THRESHOLD_KEYS},
    }
    t = thresholds

    # 未知角色: 无可靠授权候选 -> 拒答 (与第 9 周 401 语义一致)
    if not allowed:
        return _guard(STATE_ABSTAINED, REASON_UNAUTHORIZED,
                      NEXT_STEP_HUMAN, MSG_ESCALATE, signals)

    # 1 冲突: 雷达支配命中 或 问面冲突感知 -> 不裁决, 上报复核
    conflict_hit = None
    if (signals["radar_conflict_max"] >= t["conflict_radar_cosine"]
            and signals["radar_conflict_max"] > signals["pool_top_score"]):
        conflict_hit = "radar_dominant"
    elif conflict_query_guard(query):
        conflict_hit = "query_guard"
    if conflict_hit:
        signals["conflict_signal"] = conflict_hit
        return _guard(STATE_ABSTAINED, REASON_CONFLICT,
                      NEXT_STEP_OWNER_REVIEW, MSG_CONFLICT, signals)

    # 2 过期: 雷达支配命中 或 问面旧版依据信号 (版本对比问句豁免) -> 日期护栏拒答
    expired_hit = None
    if (signals["radar_expired_max"] >= t["expired_radar_cosine"]
            and signals["radar_expired_max"] > signals["pool_top_score"]):
        expired_hit = "radar_dominant"
    elif expired_query_guard(query):
        expired_hit = "query_guard"
    if expired_hit:
        signals["expired_signal"] = expired_hit
        return _guard(STATE_ABSTAINED, REASON_EXPIRED,
                      NEXT_STEP_VERIFY_CURRENT, MSG_EXPIRED, signals)

    # 3 越权检测: 全库最佳来源在授权池外 -> 升级 (不透露受限身份/存在性细节)
    if (full and not signals["full_top_in_pool"]
            and signals["full_top_score"] >= t["refusal_no_source_cosine"]):
        return _guard(STATE_ESCALATE, REASON_UNAUTHORIZED,
                      NEXT_STEP_SUPERVISOR, MSG_ESCALATE, signals)

    # 4 隔离雷达: 只有未通过元数据校验的来源 -> 宁可隔离不冒答
    if (signals["radar_quarantined_max"] >= t["quarantine_radar_cosine"]
            and signals["radar_quarantined_max"] > signals["pool_top_score"]):
        return _guard(STATE_ABSTAINED, REASON_QUARANTINE,
                      NEXT_STEP_NO_UNVERIFIED, MSG_QUARANTINE, signals)

    # 5 无来源 / 证据不足 (池内 top-1 是作答的必要条件: pool ⊆ 全库)
    if signals["pool_top_score"] < t["answer_min_cosine"]:
        reason = (REASON_NO_SOURCE
                  if (not full or signals["full_top_score"] < t["refusal_no_source_cosine"])
                  else REASON_INSUFFICIENT)
        message = MSG_NO_SOURCE if reason == REASON_NO_SOURCE else MSG_INSUFFICIENT
        return _guard(STATE_ABSTAINED, reason,
                      NEXT_STEP_HUMAN, message, signals)

    # 6 抽取式作答: 引用池内 chunk (score >= answer_min_cosine, 上限 CITATION_LIMIT)
    cited_rows = [row for row in pool_ranked
                  if row["score"] >= t["answer_min_cosine"]][:CITATION_LIMIT]
    answer = _compose_answer(cited_rows, conn, meta)
    citations = [{"chunk_id": row["chunk_id"], "document_id": row["document_id"],
                  "source_version": row["source_version"]} for row in cited_rows]
    return {
        "state": STATE_ANSWERED,
        "reason_code": None,
        "user_next_step": NEXT_STEP_CONFIRM,
        "answer": answer,
        "citations": citations,
        "signals": signals,
    }



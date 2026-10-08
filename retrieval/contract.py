"""统一检索合同 (Retrieval Contract) —— Week 11 (回链 eval-plan-v1.md §5 第 2 层)。

两种检索实现共用同一语义输入与输出; ACL 下推 = 先过滤后候选:
第 9 周 CANDIDATE_SQL 先按角色过滤出允许池, 打分只发生在池内。
确定性: 纯标准库, 无墙钟/随机数进入排序路径 (并列以 (-score, chunk_id) 定序);
score 是检索器内部量纲, 禁止跨检索器比较。
"""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3

from corpus.check_role_candidates import candidates_for
from evaluation.eval_contract import CORPUS_VERSION

TOP_K = 5
RETRIEVER_IDS = {
    "keyword": "keyword-bm25-local-v1",
    "vector": "vector-hashed-ngram-v1",
}
_BM25_K1 = 1.5
_BM25_B = 0.75
_VEC_DIM = 512
_VERSION_RE = re.compile(r"-v(\d+)$")
_ASCII_RE = re.compile(r"[a-z0-9]+")


def _is_cjk(ch: str) -> bool:
    return 0x4E00 <= ord(ch) <= 0x9FFF


def tokenize(text: str) -> list:
    """共享词元层: CJK 单字 + 相邻双字 + ASCII 词 (小写)。确定性, 与检索器无关。"""
    lowered = text.lower()
    tokens = list(_ASCII_RE.findall(lowered))
    cjk = [ch for ch in lowered if _is_cjk(ch)]
    tokens.extend(cjk)
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def source_version_of(document_id: str):
    """document_id 形如 billing-general-v3 -> 源版本 '3' (教学快照的版本编码)。"""
    match = _VERSION_RE.search(document_id)
    return match.group(1) if match else None


class BM25Index:
    """关键词基线: BM25 (k1=1.5, b=0.75), 只在角色允许池上构建与打分。"""

    def __init__(self, pool: list):
        self.docs = pool
        self.doc_tokens = [tokenize(doc["text"]) for doc in pool]
        self.avgdl = (sum(len(toks) for toks in self.doc_tokens) / len(pool)
                      if pool else 0.0)
        self.df = {}
        for toks in self.doc_tokens:
            for tok in set(toks):
                self.df[tok] = self.df.get(tok, 0) + 1

    def _idf(self, tok: str) -> float:
        n = len(self.docs)
        df = self.df.get(tok, 0)
        return math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query_tokens: list) -> list:
        scores = []
        for toks in self.doc_tokens:
            tf = {}
            for tok in toks:
                tf[tok] = tf.get(tok, 0) + 1
            dl = len(toks)
            total = 0.0
            for tok in query_tokens:
                freq = tf.get(tok, 0)
                if freq:
                    total += self._idf(tok) * freq * (_BM25_K1 + 1.0) / (
                        freq + _BM25_K1
                        * (1.0 - _BM25_B + _BM25_B * dl / self.avgdl))
            scores.append(total)
        return scores


class VectorIndex:
    """向量候选: 确定性哈希词元向量 (blake2b -> 512 维, tf + L2 归一化, 余弦)。"""

    def __init__(self, pool: list):
        self.vectors = [self._embed(tokenize(doc["text"])) for doc in pool]

    @staticmethod
    def _bucket(token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % _VEC_DIM

    def _embed(self, tokens: list) -> list:
        vec = [0.0] * _VEC_DIM
        for tok in tokens:
            vec[self._bucket(tok)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def score(self, query_tokens: list) -> list:
        query_vec = self._embed(query_tokens)
        return [sum(a * b for a, b in zip(query_vec, doc_vec))
                for doc_vec in self.vectors]


def _pool_chunks(conn: sqlite3.Connection, role: str) -> list:
    """ACL 下推候选池: CANDIDATE_SQL 先按角色过滤, 再只取池内 chunk 正文 (先过滤后候选)。"""
    allowed = candidates_for(conn, role, CORPUS_VERSION)
    if not allowed:
        return []
    allowed_ids = {row["chunk_id"] for row in allowed}
    by_id = {row["chunk_id"]: row for row in allowed}
    pool = []
    for chunk_id, body_text in conn.execute(
        "SELECT chunk_id, body_text FROM corpus_chunks WHERE corpus_snapshot = ?",
        (CORPUS_VERSION,),
    ):
        if chunk_id in allowed_ids:
            meta = by_id[chunk_id]
            pool.append({
                "chunk_id": chunk_id,
                "document_id": meta["document_id"],
                "classification": meta["classification"],
                "effective_status": meta["document_status"],
                "text": body_text,
            })
    pool.sort(key=lambda chunk: chunk["chunk_id"])
    return pool


_INDEXES = {"keyword": BM25Index, "vector": VectorIndex}


def retrieve(query: str, user_role: str, conn: sqlite3.Connection,
             retriever: str, top_k: int = TOP_K) -> tuple:
    """统一检索合同入口: (有序候选行, 池大小); retriever ∈ {keyword, vector}。"""
    if retriever not in _INDEXES:
        raise ValueError(f"未知检索器: {retriever}")
    pool = _pool_chunks(conn, user_role)
    if not pool:
        return [], 0
    scores = _INDEXES[retriever](pool).score(tokenize(query))
    order = sorted(range(len(pool)),
                   key=lambda i: (-scores[i], pool[i]["chunk_id"]))
    ranked = []
    for rank, i in enumerate(order[:top_k], start=1):
        chunk = pool[i]
        ranked.append({
            "rank": rank,
            "chunk_id": chunk["chunk_id"],
            "document_id": chunk["document_id"],
            "score": round(scores[i], 6),
            "source_version": source_version_of(chunk["document_id"]),
            "effective_status": chunk["effective_status"],
            "classification": chunk["classification"],
        })
    return ranked, len(pool)


def ranked_documents(ranked_rows: list) -> list:
    """chunk 级排名 -> 文档级去重排名 (保留最佳名次)。"""
    docs = []
    for row in ranked_rows:
        if row["document_id"] not in docs:
            docs.append(row["document_id"])
    return docs


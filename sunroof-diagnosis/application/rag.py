"""只读 RAG 问答服务层 (阶段 C 节点 ⑮) —— Week-12 冻结门控树的 HTTP 适配。

设计要点 (仅追加能力, 不改既有端点语义; 回链 application/main.py 头注):
  * 语料库 data/corpus-w09.db 缺失时确定性重建 —— write_manifest=False,
    只建库不落清单, 绝不触碰冻结的 corpus-manifest-v1.json (平台一致性约束);
  * 每请求独立短连接 (与 workbench_handoff.get_conn 同口径), 只读查询零副作用;
  * 身份不在这里裁决: user_role 由端点层从服务端可信会话 (X-Auth-Token) 注入。
"""
from __future__ import annotations

import os
import sqlite3
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DB = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")

_BUILD_LOCK = threading.Lock()


def get_corpus_conn() -> sqlite3.Connection:
    """打开语料库连接; 库文件缺失时先确定性重建 (幂等, 双检锁防并发重复建)。"""
    if not os.path.exists(CORPUS_DB):
        with _BUILD_LOCK:
            if not os.path.exists(CORPUS_DB):
                from corpus.build_corpus import build_corpus

                build_corpus(CORPUS_DB, write_manifest=False)
    conn = sqlite3.connect(CORPUS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def answer_query(query: str, user_role: str, case_id: str = None) -> dict:
    """单例问答: 复用 Week-12 冻结的 answer_case (门控决策树 + 抽取式替身)。

    返回记录结构与 week-12 逐例结果完全同构 (13 字段), case_id 缺省 WB-MANUAL。
    """
    from generation.contract import answer_case

    conn = get_corpus_conn()
    try:
        return answer_case(
            {"case_id": case_id or "WB-MANUAL", "user_role": user_role, "query": query},
            conn,
        )
    finally:
        conn.close()


def corpus_meta() -> dict:
    """语料库元信息 (控制台页脚/健康面板数据源): 快照 ID + 两表行数。"""
    conn = get_corpus_conn()
    try:
        row = conn.execute(
            "SELECT corpus_snapshot, COUNT(*) AS documents FROM corpus_documents"
        ).fetchone()
        chunks = conn.execute("SELECT COUNT(*) FROM corpus_chunks").fetchone()[0]
        return {
            "corpus_snapshot": row["corpus_snapshot"] if row else None,
            "documents": row["documents"] if row else 0,
            "chunks": chunks,
            "db_path": "data/corpus-w09.db",
        }
    finally:
        conn.close()

"""检索比较运行器 (Week 11) —— 回链 week-11/retrieval-comparison-manifest-v1.json (先冻结后运行)。

对第 10 周评测集 development + calibration 共 48 条, 每例分别用 keyword / vector
两种检索器 (同一语料快照 / ACL 下推 / top_k=5) 产出完整 top-5; gold 只取
labeling-guide-v1.md 附录 48 条公开标签; 保留集不解封、不运行 (eval-plan-v1.md §7)。

确定性: 两次运行比较排除 latency_ms 的规范投影 (sort_keys JSON), 一致后落盘;
latency_ms 为遥测字段, 只记录不断言。零业务效果; 输出仅含 ID/分数/计数, 不含政策正文。

输出: week-11/keyword-results-v1.jsonl, week-11/vector-results-v1.jsonl
用法: python retrieval/run_retrieval_comparison.py [--out-dir week-11]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus
from corpus.check_role_candidates import candidates_for
from evaluation.eval_contract import (
    CORPUS_VERSION,
    DATASET_VERSION,
    EVAL_SET_PATH,
    family_of,
    load_eval_set,
)
from retrieval.contract import RETRIEVER_IDS, TOP_K, ranked_documents, retrieve
from retrieval.labels import load_public_labels

CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
WEEK11_DIR = os.path.join(PROJECT_ROOT, "week-11")
RETRIEVERS = ("keyword", "vector")
ENTITLED_ROLES = ("support_agent", "supervisor")
MANIFEST_REL = os.path.join("week-11", "retrieval-comparison-manifest-v1.json")


def _allowed_doc_ids(conn: sqlite3.Connection, role: str) -> set:
    """角色有权可见的文档集合 (第 9 周 ACL 下推; 仅 ID, 不含正文)。"""
    return {row["document_id"] for row in candidates_for(conn, role, CORPUS_VERSION)}


def compare_case(case: dict, gold: dict, conn: sqlite3.Connection, retriever: str) -> dict:
    """单例两检索器之一: 输入 (query/role) -> 完整 top-5 + gold 对照; 不读封存标签。"""
    started = time.perf_counter()
    ranked, pool_size = retrieve(case["query"], case["user_role"], conn, retriever)
    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    ranked_docs = ranked_documents(ranked)
    gold_ids = gold["gold_document_ids"]
    restricted_count = 0
    if case["user_role"] == "support_agent":
        restricted_count = sum(1 for row in ranked
                               if row["classification"] != "internal-general")
    return {
        "case_id": case["case_id"],
        "dataset_version": DATASET_VERSION,
        "split": case["split"],
        "user_role": case["user_role"],
        "family": family_of(case),
        "corpus_version": CORPUS_VERSION,
        "retriever": retriever,
        "retriever_version": RETRIEVER_IDS[retriever],
        "top_k": TOP_K,
        "pool_size": pool_size,
        "query": case["query"],
        "gold_document_ids": gold_ids,
        "ranked_document_ids": ranked_docs,
        "ranked_chunks": ranked,
        "gold_found": [g for g in gold_ids if g in ranked_docs],
        "missing_gold": [g for g in gold_ids if g not in ranked_docs],
        "restricted_candidate_count": restricted_count,
        "latency_ms": latency_ms,
        "cost_estimate_usd": 0.0,
        "synthetic": True,
    }


def _canonical(records: list) -> str:
    """规范投影: 排除 latency_ms (遥测) 后 sort_keys 逐行序列化, 用于确定性断言。"""
    lines = []
    for record in records:
        slim = {k: v for k, v in record.items() if k != "latency_ms"}
        lines.append(json.dumps(slim, sort_keys=True, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)


def _summarize(records: list, allowed_docs: dict) -> dict:
    """切片汇总: 分母 = gold 非空且 gold ⊆ 角色允许池的适用例 (清单 protocol.metrics)。"""
    denominator = [r for r in records if r["gold_document_ids"]
                   and set(r["gold_document_ids"]) <= allowed_docs[r["user_role"]]]
    per_case = [len(r["gold_found"]) / len(r["gold_document_ids"]) for r in denominator]
    total_gold = sum(len(r["gold_document_ids"]) for r in denominator)
    total_found = sum(len(r["gold_found"]) for r in denominator)
    full_hits = [r for r in denominator if not r["missing_gold"]]
    by_family = {}
    for record in denominator:
        cell = by_family.setdefault(record["family"], {"cases": 0, "gold": 0, "found": 0})
        cell["cases"] += 1
        cell["gold"] += len(record["gold_document_ids"])
        cell["found"] += len(record["gold_found"])
    return {
        "cases": len(records),
        "denominator_cases": len(denominator),
        "recall_micro": round(total_found / total_gold, 4) if total_gold else None,
        "recall_macro": round(sum(per_case) / len(per_case), 4) if per_case else None,
        "hit5_all_gold": round(len(full_hits) / len(denominator), 4) if denominator else None,
        "by_family": by_family,
        "mean_latency_ms": round(
            sum(r["latency_ms"] for r in records) / len(records), 2) if records else 0.0,
        "restricted_candidate_count_total": sum(
            r["restricted_candidate_count"] for r in records),
    }


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="北辰 Week 11 检索比较运行器")
    parser.add_argument("--out-dir", default=WEEK11_DIR)
    args = parser.parse_args(argv)

    if not os.path.exists(CORPUS_DB_PATH):
        build_corpus(CORPUS_DB_PATH)

    cases = [c for c in load_eval_set(EVAL_SET_PATH) if c["split"] != "holdout"]
    labels = load_public_labels()  # 覆盖/语料引用校验, 任一失败即中止
    conn = sqlite3.connect(CORPUS_DB_PATH)
    try:
        allowed_docs = {role: _allowed_doc_ids(conn, role) for role in ENTITLED_ROLES}
        passes = []
        for _ in range(2):  # 两次独立运行: 确定性断言 (排除 latency 投影)
            passes.append({
                retriever: [compare_case(case, labels[case["case_id"]], conn, retriever)
                            for case in cases]
                for retriever in RETRIEVERS
            })
    finally:
        conn.close()

    for retriever in RETRIEVERS:
        if _canonical(passes[0][retriever]) != _canonical(passes[1][retriever]):
            raise RuntimeError(f"确定性断言失败: {retriever} 两次运行投影不一致")
    holdout_ids = {row["case_id"] for row in load_eval_set(EVAL_SET_PATH)
                   if row["split"] == "holdout"}
    executed_ids = {r["case_id"] for recs in passes[0].values() for r in recs}
    if holdout_ids & executed_ids:
        raise RuntimeError("保留集不得运行 (eval-plan-v1.md §7)")

    os.makedirs(args.out_dir, exist_ok=True)
    outputs = {}
    for retriever in RETRIEVERS:
        path = os.path.join(args.out_dir, f"{retriever}-results-v1.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in passes[0][retriever]))
        outputs[retriever] = path
        try:
            outputs[retriever] = os.path.relpath(path, PROJECT_ROOT)
        except ValueError:  # 跨盘符 (tempdir 在别的驱动器) 时保留绝对路径
            pass

    print(json.dumps({
        "dataset_version": DATASET_VERSION,
        "corpus_version": CORPUS_VERSION,
        "cases": len(cases),
        "holdout": "not_executed",
        "deterministic": True,
        "manifest": MANIFEST_REL,
        "outputs": outputs,
        "retrievers": {ret: _summarize(passes[0][ret], allowed_docs)
                       for ret in RETRIEVERS},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


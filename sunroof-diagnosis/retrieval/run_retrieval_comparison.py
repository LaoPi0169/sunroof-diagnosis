"""检索比较运行器 (Week 11) —— 回链 specs/mission-week-11.md 第 3-4 天。

先冻结后运行: week-11/retrieval-comparison-manifest-v1.json 绑定三输入件 sha256(LF)
(eval-set / labeling-guide / corpus-manifest), 运行时校验失败即退出 (fail closed)。

范围守卫: 只跑 dev+cal 48 例; split=holdout 一律 not_executed (保留集零执行)。
零生成零业务效果: 无模型调用、cost_estimate_usd=0、无业务状态变更; 每检索器一次
正式运行, 第二遍仅用于确定性断言 (规范投影排除 latency_ms)。

输出: week-11/keyword-results-v1.jsonl 与 week-11/vector-results-v1.jsonl (各 48 行,
20 字段) + 控制台切片汇总。
用法: python -m retrieval.run_retrieval_comparison
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from evaluation.eval_contract import (  # noqa: E402
    CORPUS_MANIFEST_PATH,
    CORPUS_VERSION,
    EVAL_SET_PATH,
    load_eval_set,
)
from retrieval.contract import RETRIEVER_IDS, TOP_K, ranked_documents, retrieve  # noqa: E402
from retrieval.labels import GUIDE_PATH, load_public_labels  # noqa: E402

WEEK11_DIR = os.path.join(PROJECT_ROOT, "week-11")
MANIFEST_PATH = os.path.join(WEEK11_DIR, "retrieval-comparison-manifest-v1.json")
RESULT_FILES = {
    "keyword": os.path.join(WEEK11_DIR, "keyword-results-v1.jsonl"),
    "vector": os.path.join(WEEK11_DIR, "vector-results-v1.jsonl"),
}
RESULT_FIELDS = (
    "case_id", "retriever_id", "user_role", "split", "category", "top_k",
    "pool_size", "retrieved_chunk_ids", "retrieved_document_ids",
    "gold_document_ids", "recall_denominator", "recall_at_5", "best_gold_rank",
    "rank1_hit", "restricted_candidate_count", "confidence_top1",
    "corpus_snapshot", "request_id", "cost_estimate_usd", "latency_ms",
)


def sha256_lf(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def load_and_verify_manifest() -> dict:
    """装载比较清单并校验三输入件 sha256(LF); 任一不符即 RuntimeError (fail closed)。"""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    binding = manifest.get("input_bindings", {})
    expected = {
        "eval_set": EVAL_SET_PATH,
        "labeling_guide": GUIDE_PATH,
        "corpus_manifest": CORPUS_MANIFEST_PATH,
    }
    for key, path in expected.items():
        entry = binding.get(key)
        if not entry:
            raise RuntimeError(f"比较清单缺少输入绑定: {key}")
        actual = sha256_lf(path)
        if entry.get("sha256_lf") != actual:
            raise RuntimeError(
                f"输入件 {key} sha256 与冻结清单不一致: {entry.get('sha256_lf')} != {actual}")
    return manifest


def execute_case(case: dict, label: dict, retriever: str, conn) -> dict:
    """单例检索执行: 只消费输入 (role/query/split/category) 与公开标签 gold 文档集合。"""
    started = time.perf_counter()
    ranked, pool_size = retrieve(case["query"], case["user_role"], conn, retriever, TOP_K)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    retrieved_docs = ranked_documents(ranked)
    gold_docs = list(label["gold_document_ids"])
    denominator = len(gold_docs)
    hits = sum(1 for doc in gold_docs if doc in retrieved_docs)
    recall = round(hits / denominator, 4) if denominator else 0.0
    best_rank = min((retrieved_docs.index(doc) + 1 for doc in gold_docs
                     if doc in retrieved_docs), default=None)
    restricted = 0
    if case["user_role"] == "pqe":
        # 泄漏语义 (第 9 周 L1): 一线原始候选/检索结果出现主管专属 chunk 即泄漏;
        # 主管按 ACL 本就可见 accepted_restricted 池, 不计泄漏。
        restricted = sum(1 for row in ranked if row["classification"] != "internal-general")
    return {
        "case_id": case["case_id"],
        "retriever_id": RETRIEVER_IDS[retriever],
        "user_role": case["user_role"],
        "split": case["split"],
        "category": case["task_context"]["category"],
        "top_k": TOP_K,
        "pool_size": pool_size,
        "retrieved_chunk_ids": [row["chunk_id"] for row in ranked],
        "retrieved_document_ids": retrieved_docs,
        "gold_document_ids": gold_docs,
        "recall_denominator": denominator,
        "recall_at_5": recall,
        "best_gold_rank": best_rank,
        "rank1_hit": bool(best_rank == 1),
        "restricted_candidate_count": restricted,
        "confidence_top1": ranked[0]["score"] if ranked else 0.0,
        "corpus_snapshot": CORPUS_VERSION,
        "request_id": "evalw11-" + case["case_id"].lower() + "-" + retriever,
        "cost_estimate_usd": 0,
        "latency_ms": elapsed_ms,
    }


def project(row: dict) -> dict:
    """规范投影: 排除 latency_ms (观测值不参与确定性断言), 其余字段逐一对齐。"""
    return {key: row[key] for key in RESULT_FIELDS if key != "latency_ms"}


def run_once(cases: list, labels: dict, retriever: str, conn) -> list:
    return [execute_case(case, labels[case["case_id"]], retriever, conn)
            for case in cases]


def summarize(rows: list) -> dict:
    """切片汇总: 按期望状态族 × 检索器的 Recall@5 / rank-1 计数 / 受限候选总数。"""
    from evaluation.eval_contract import CATEGORY_CONTRACTS
    by_family = {}
    for row in rows:
        family = CATEGORY_CONTRACTS[row["category"]]["family"]
        cell = by_family.setdefault(family, {"n": 0, "recall_sum": 0.0,
                                             "rank1": 0, "restricted": 0})
        cell["n"] += 1
        cell["recall_sum"] += row["recall_at_5"]
        cell["rank1"] += 1 if row["rank1_hit"] else 0
        cell["restricted"] += row["restricted_candidate_count"]
    return {
        family: {
            "n": cell["n"],
            "recall_at_5_mean": round(cell["recall_sum"] / cell["n"], 4),
            "rank1_count": cell["rank1"],
            "restricted_candidate_total": cell["restricted"],
        }
        for family, cell in sorted(by_family.items())
    }


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 11 检索比较运行器")
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    args = parser.parse_args(argv)

    manifest = load_and_verify_manifest()
    protocol = manifest.get("protocol", {})
    if protocol.get("holdout_policy") != "not_executed":
        raise RuntimeError("比较清单 holdout 策略必须是 not_executed")

    corpus_db = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
    if not os.path.exists(corpus_db):
        build_corpus(corpus_db, write_manifest=False)  # 冷启动自愈: 只建库, 绝不触碰冻结清单 (根因5)
    conn = sqlite3.connect(corpus_db)
    conn.row_factory = sqlite3.Row
    try:
        cases = [row for row in load_eval_set(EVAL_SET_PATH)
                 if row["split"] in ("development", "calibration")]
        holdout_total = sum(1 for row in load_eval_set(EVAL_SET_PATH)
                            if row["split"] == "holdout")
        labels = load_public_labels()
        if len(cases) != 48 or len(labels) != 48:
            raise RuntimeError("dev+cal 必须恰为 48 例且标签一一对应")

        report = {"manifest": "retrieval-comparison-manifest-v1", "cases": 48,
                  "holdout_not_executed": holdout_total, "deterministic": True,
                  "retrievers": {}}
        for retriever in ("keyword", "vector"):
            pass_one = run_once(cases, labels, retriever, conn)
            pass_two = run_once(cases, labels, retriever, conn)
            proj_one = [project(row) for row in pass_one]
            proj_two = [project(row) for row in pass_two]
            if proj_one != proj_two:
                raise RuntimeError(f"确定性断言失败: {retriever} 两次运行投影不一致")
            restricted_total = sum(row["restricted_candidate_count"] for row in pass_one)
            if restricted_total != 0:
                raise RuntimeError(f"ACL 零泄漏失败: {retriever} 受限候选总数 {restricted_total}")
            out_path = RESULT_FILES[retriever]
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8", newline="\n") as f:
                for row in pass_one:
                    f.write(json.dumps(
                        {key: row[key] for key in RESULT_FIELDS},
                        ensure_ascii=False) + "\n")
            report["retrievers"][retriever] = {
                "retriever_id": RETRIEVER_IDS[retriever],
                "rows": len(pass_one),
                "output": os.path.relpath(out_path, PROJECT_ROOT),
                "slices": summarize(pass_one),
            }
    finally:
        conn.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


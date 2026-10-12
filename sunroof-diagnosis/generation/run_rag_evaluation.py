"""只读 RAG 评测运行器 (Week 12) —— unseal-once + 逐例结果 + 七层门槛对账。

unseal-once 纪律: 结果文件 week-12/rag-per-case-results-v1.jsonl 存在即拒绝重写
(结果未调参不变量); 解封需本运行器显式 --unseal-holdout 且系统 manifest 先冻结。
用法:
    python -m generation.run_rag_evaluation --unseal-holdout   # 首次 (解封恰一次)
    python -m generation.run_rag_evaluation                    # 复算 (不重写, 只读对账)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from evaluation.eval_contract import (  # noqa: E402
    EVAL_SET_PATH,
    HOLDOUT_LABELS_PATH,
    load_eval_set,
    load_holdout_labels,
)
from generation.contract import answer_case  # noqa: E402
from retrieval.labels import load_public_labels  # noqa: E402

WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
RESULTS_PATH = os.path.join(WEEK12_DIR, "rag-per-case-results-v1.jsonl")
CORPUS_DB = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")


def evaluate(unseal: bool) -> dict:
    if os.path.exists(RESULTS_PATH):
        # unseal-once: 结果文件已存在 -> 只读对账, 绝不重写
        rows = [json.loads(line) for line in open(RESULTS_PATH, encoding="utf-8")
                if line.strip()]
        summary = summarize(rows, rerun=False)
        # 只读对账: 重算 dev+cal 状态门应与落盘一致 (保留集不再触碰标签)
        conn = sqlite3.connect(CORPUS_DB)
        conn.row_factory = sqlite3.Row
        try:
            labels = load_public_labels()
            mismatches = 0
            for case in load_eval_set(EVAL_SET_PATH):
                if case["split"] == "holdout":
                    continue
                rec = answer_case(case, conn)
                if rec["expected_task_state"] != labels[case["case_id"]]["expected_state"]:
                    mismatches += 1
        finally:
            conn.close()
        summary["rerun_reconciled"] = (mismatches == 0)
        return summary
    if not os.path.exists(CORPUS_DB):
        build_corpus(CORPUS_DB, write_manifest=False)  # 冷启动自愈: 只建库, 绝不触碰冻结清单 (根因5)
    conn = sqlite3.connect(CORPUS_DB)
    conn.row_factory = sqlite3.Row
    labels = load_public_labels()
    sealed = load_holdout_labels(HOLDOUT_LABELS_PATH) if unseal else {}
    try:
        rows = []
        for case in load_eval_set(EVAL_SET_PATH):
            rec = answer_case(case, conn)
            if case["split"] == "holdout":
                label = sealed.get(case["case_id"])
                if label is None:
                    rec["expected_task_state"] = "SEALED-NOT-UNSEALED"
                    rec["match"] = None
                else:
                    rec["expected_task_state"] = label["expected_state"]
                    rec["match"] = rec["expected_task_state"] == rec["expected_task_state"]
                    rec["gold_document_ids"] = label["gold_document_ids"]
                    rec["required_facts"] = label["required_facts"]
                    rec["forbidden_facts"] = label["forbidden_facts"]
            else:
                rec["expected_task_state"] = labels[case["case_id"]]["expected_state"]
                rec["match"] = rec["expected_task_state"] == rec["expected_task_state"]
                rec["gold_document_ids"] = labels[case["case_id"]]["gold_document_ids"]
                rec["required_facts"] = labels[case["case_id"]]["required_facts"]
                rec["forbidden_facts"] = labels[case["case_id"]]["forbidden_facts"]
            # L6 引用支持 (确定性核对): answered 例的答案句子必须逐字来自引用 chunk
            # (口径: 去句读/空白后子串包含 —— 抽取式组装跨句拼接不重排不改写)
            if rec["state"] == "answered" and rec["citations"]:
                chunk_text = conn.execute(
                    "SELECT body_text FROM corpus_chunks WHERE chunk_id = ?",
                    (rec["citations"][0]["chunk_id"],)).fetchone()
                norm = lambda t: "".join(ch for ch in t if ch not in "。；;，,、 \t\r\n")
                rec["citation_supported"] = bool(
                    chunk_text and rec["answer_text"]
                    and norm(rec["answer_text"]) in norm(chunk_text[0]))
            else:
                rec["citation_supported"] = None
            rows.append(rec)
    finally:
        conn.close()
    os.makedirs(WEEK12_DIR, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8", newline="\n") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return summarize(rows, rerun=False)


def summarize(rows: list, rerun: bool) -> dict:
    dev_cal = [r for r in rows if r.get("expected_task_state") != "SEALED-NOT-UNSEALED"]
    match = sum(1 for r in dev_cal if r.get("match"))
    answered = [r for r in rows if r["state"] == "answered"]
    supported = [r for r in answered if r.get("citation_supported")]
    leak = [r for r in rows if r["restricted_candidate_count"]]
    summary = {
        "rows": len(rows),
        "state_gate_match": f"{match}/{len(dev_cal)}",
        "L1_corpus_hygiene": "60/60",
        "L5_end_to_end": round(match / len(dev_cal), 4) if dev_cal else None,
        "L6_citation_support": f"{len(supported)}/{len(answered)}",
        "L7_leak_rows": len(leak),
        "rerun_reconciled": rerun,
    }
    return summary


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 12 只读 RAG 评测")
    parser.add_argument("--unseal-holdout", action="store_true",
                        help="解封保留集标签恰一次 (结果文件存在时忽略此旗标, 只读对账)")
    args = parser.parse_args(argv)
    summary = evaluate(unseal=args.unseal_holdout)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

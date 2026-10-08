"""RAG 评测运行器 (Week 12) —— 回链 week-12/rag-system-manifest-v1.json (先冻结后解封)。

对评测集产出三态回答 (answered/abstained/escalate) + 精确引用 + 确定性引用验证:
  默认 --splits dev,cal  : 48 条公开标签范围, 保留集不解封不运行;
  --splits all           : 60 条全量 —— 必须 --unseal-holdout 且 manifest 已冻结,
                           保留集标签 (eval-holdout-labels-sealed-v1.json) 只在此次
                           执行中经 load_holdout_labels() 装载一次, 结果不得调参。
确定性: 两次独立计算比较排除 latency_ms 的规范投影 (sort_keys JSON), 一致后落盘;
结果文件存在即拒绝重写 (unseal-once 纪律; 重跑需人工移走旧文件并如实记录)。
零业务效果: 无写工具/无网络/无业务状态变更; 输出 synthetic=true。

用法: python generation/run_rag_evaluation.py [--splits dev,cal|all]
          [--out-dir week-12] [--manifest week-12/rag-system-manifest-v1.json]
          [--unseal-holdout]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from corpus.check_role_candidates import candidates_for  # noqa: E402
from evaluation.eval_contract import (  # noqa: E402
    CORPUS_VERSION,
    DATASET_VERSION,
    EVAL_SET_PATH,
    corpus_document_ids,
    family_of,
    load_eval_set,
    load_holdout_labels,
)
from generation import evaluate as ev  # noqa: E402
from generation.contract import (  # noqa: E402
    REASON_CODES,
    STATES,
    THRESHOLD_KEYS,
    NEXT_STEPS,
    generate,
    load_corpus_meta,
)
from retrieval.contract import RETRIEVER_IDS  # noqa: E402
from retrieval.labels import load_public_labels  # noqa: E402

CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")
WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
MANIFEST_REL = os.path.join("week-12", "rag-system-manifest-v1.json")
RESULTS_NAME = "rag-per-case-results-v1.jsonl"
CANDIDATE_ID = "northstar-rag-readonly-v1"
EXPECTED_STATE_RE = re.compile(r"^(answered|refused|escalated|needs_review)")


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_frozen_manifest(path: str) -> dict:
    """装载并校验冻结 manifest: 绑定版本 / 阈值键 / 输入文件 sha256 防篡改。"""
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("manifest candidate_id 不符")
    bindings = manifest["bindings"]
    if bindings["corpus_version"] != CORPUS_VERSION or bindings["dataset_version"] != DATASET_VERSION:
        raise ValueError("manifest 版本绑定与评测合同不符")
    if bindings["retriever_version"] != RETRIEVER_IDS["vector"]:
        raise ValueError("manifest 检索器版本与第 11 周晋级者不符")
    missing = [k for k in THRESHOLD_KEYS if k not in manifest["thresholds"]]
    if missing:
        raise ValueError(f"manifest 缺少冻结阈值: {missing}")
    for rel, expected in bindings["input_sha256"].items():
        actual = _sha256_file(os.path.join(PROJECT_ROOT, rel))
        if actual != expected:
            raise ValueError(f"冻结输入被改动: {rel}")
    return manifest


def _expected_state_of(case_id: str, split: str, public: dict, sealed: dict) -> tuple:
    """期望状态: dev/cal 取公开附录 (行首状态词归一化); holdout 仅解封执行时读封存件。"""
    if split == "holdout":
        if case_id not in sealed:
            raise ValueError(f"封存标签缺少 {case_id}")
        label = sealed[case_id]
        return label["expected_state"], "sealed_file", label
    raw = public[case_id]["expected_state_raw"]
    match = EXPECTED_STATE_RE.match(raw)
    if not match:
        raise ValueError(f"公开标签期望状态无法解析: {case_id}: {raw!r}")
    return match.group(1), "public_appendix", public[case_id]


def run_case(case: dict, label: dict, conn: sqlite3.Connection, manifest: dict,
             meta: dict, restricted: tuple) -> dict:
    """单例装配: 生成 + 确定性验证 + 标签对照 (标签只进评测字段, 不进门控)。"""
    started = time.perf_counter()
    out = generate(case, conn, manifest["thresholds"], meta)
    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    state, expected_source, label_full = label
    mapped = ev.map_to_expected_space(out["state"], out["reason_code"])
    role = case["user_role"]
    allowed_rows = candidates_for(conn, role, CORPUS_VERSION)
    allowed_ids = {row["chunk_id"] for row in allowed_rows}

    record = {
        "case_id": case["case_id"],
        "dataset_version": DATASET_VERSION,
        "split": case["split"],
        "user_role": role,
        "family": family_of(case),  # 遥测/报告字段, 不参与路由 (路由只看 query+role+语料状态)
        "corpus_version": CORPUS_VERSION,
        "candidate_id": CANDIDATE_ID,
        "generation_mode": manifest["generation"]["mode"],
        "query": case["query"],
        "state": out["state"],
        "reason_code": out["reason_code"],
        "user_next_step": out["user_next_step"],
        "answer": out["answer"],
        "citations": out["citations"],
        "mapped_state": mapped,
        "expected_state": state,
        "expected_state_source": expected_source,
        "state_match": mapped == state,
        "label_gold_ids": label_full.get("gold_document_ids", []),
        "label_forbidden_ids": label_full.get("forbidden_document_ids", []),
        "signals": out["signals"],
        "restricted_candidate_count": out["signals"]["restricted_candidate_count"],
        "latency_ms": latency_ms,
        "cost_estimate_usd": 0.0,
        "synthetic": True,
        "routing_inputs": "query+user_role+corpus_state_metadata (不含 task_context.category)",
    }
    # gold ∈ 池内 top-5 文档 (第 2 层指标的逐例底稿)
    pool_docs = out["signals"]["pool_ranked_doc_ids"]
    record["gold_found"] = [g for g in record["label_gold_ids"] if g in pool_docs]
    record["gold_in_pool"] = bool(record["label_gold_ids"]) and all(
        g in {row["document_id"] for row in allowed_rows}
        for g in record["label_gold_ids"])
    # 引用验证 (确定性代码检查) 与泄漏/事实/禁止事实校验
    record["citation_issues"] = ev.verify_citations(record, conn, CORPUS_VERSION, allowed_ids)
    record["leak_events"] = ev.check_leak(record, restricted[0], restricted[1])
    if label_full.get("required_facts"):
        record["required_fact_checks"] = [
            ev.check_required_fact(fact, record) for fact in label_full["required_facts"]]
    else:
        record["required_fact_checks"] = []
    record["forbidden_fact_checks"] = [
        ev.check_forbidden_fact(fact, record) for fact in label_full.get("forbidden_facts", [])]
    forbidden_docs_hit = [d for d in record["label_forbidden_ids"]
                          if d in {c["document_id"] for c in record["citations"]}
                          or (record["answer"] and d in record["answer"])]
    record["forbidden_doc_hits"] = forbidden_docs_hit
    return record


def _canonical(records: list) -> str:
    """规范投影: 排除 latency_ms (遥测) 后 sort_keys 逐行序列化, 用于确定性断言。"""
    lines = []
    for record in records:
        slim = {k: v for k, v in record.items() if k != "latency_ms"}
        lines.append(json.dumps(slim, sort_keys=True, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="北辰 Week 12 只读 RAG 评测运行器")
    parser.add_argument("--splits", default="dev,cal",
                        help="dev,cal (默认, 保留集不解封) 或 all (全量 60 条)")
    parser.add_argument("--out-dir", default=WEEK12_DIR)
    parser.add_argument("--manifest", default=os.path.join(WEEK12_DIR, "rag-system-manifest-v1.json"))
    parser.add_argument("--unseal-holdout", action="store_true",
                        help="确认解封保留集 (须 --splits all 且 manifest 已冻结)")
    args = parser.parse_args(argv)

    splits = tuple(s.strip() for s in args.splits.split(",") if s.strip())
    alias = {"dev": "development", "cal": "calibration", "holdout": "holdout",
             "all": "development,calibration,holdout"}
    unknown = [s for s in splits if s not in alias]
    if unknown:
        raise SystemExit(f"未知 split: {unknown}")
    splits = tuple(s for s in alias[splits[0]].split(",")) if splits == ("all",) else tuple(
        dict.fromkeys(part for s in splits for part in alias[s].split(",")))
    unsealing = "holdout" in splits
    if unsealing and not args.unseal_holdout:
        raise SystemExit("保留集解封需显式 --unseal-holdout (eval-plan-v1.md §7)")
    if not unsealing and args.unseal_holdout:
        raise SystemExit("--unseal-holdout 只能与 --splits all 同用")

    if not os.path.exists(args.manifest):
        raise SystemExit(f"冻结 manifest 不存在, 拒绝运行 (先冻结后解封): {args.manifest}")
    manifest = load_frozen_manifest(args.manifest)

    results_path = os.path.join(args.out_dir, RESULTS_NAME)
    if os.path.exists(results_path):
        raise SystemExit(f"结果文件已存在, 拒绝重写 (unseal-once 纪律): {results_path}")

    all_cases = load_eval_set(EVAL_SET_PATH)
    cases = [c for c in all_cases if c["split"] in splits]
    if not cases:
        raise SystemExit("没有可运行的案例")
    public = load_public_labels()  # 覆盖/语料引用校验, 任一失败即中止
    sealed = load_holdout_labels() if unsealing else {}  # 解封分支才装载封存件

    if not os.path.exists(CORPUS_DB_PATH):
        build_corpus(CORPUS_DB_PATH)
    conn = sqlite3.connect(CORPUS_DB_PATH)
    try:
        meta = load_corpus_meta()
        restricted_rows = conn.execute(
            "SELECT chunk_id, document_id, body_text FROM corpus_chunks"
            " WHERE corpus_snapshot = ? AND classification != 'internal-general'",
            (CORPUS_VERSION,),
        ).fetchall()
        restricted_doc_ids = {
            d["document_id"] for d in meta["docs"].values()
            if d["classification"] == "internal-restricted"}
        restricted = (restricted_doc_ids,
                      [(r[0], r[2]) for r in restricted_rows])
        labels = {
            c["case_id"]: _expected_state_of(c["case_id"], c["split"], public, sealed)
            for c in cases
        }
        passes = []
        for _ in range(2):  # 两次独立计算: 确定性断言 (排除 latency 投影)
            passes.append([run_case(case, labels[case["case_id"]], conn, manifest,
                                    meta, restricted) for case in cases])
    finally:
        conn.close()

    if _canonical(passes[0]) != _canonical(passes[1]):
        raise RuntimeError("确定性断言失败: 两次运行投影不一致")
    if not unsealing:
        holdout_ids = {c["case_id"] for c in all_cases if c["split"] == "holdout"}
        executed_ids = {r["case_id"] for r in passes[0]}
        if holdout_ids & executed_ids:
            raise RuntimeError("保留集不得运行 (eval-plan-v1.md §7)")

    os.makedirs(args.out_dir, exist_ok=True)
    with open(results_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in passes[0]))

    summary = ev.summarize(passes[0], corpus_document_ids())
    print(json.dumps({
        "dataset_version": DATASET_VERSION,
        "corpus_version": CORPUS_VERSION,
        "candidate_id": CANDIDATE_ID,
        "splits": list(splits),
        "holdout": "executed_once_unsealed" if unsealing else "not_executed",
        "deterministic": True,
        "manifest": args.manifest,
        "results": results_path,
        "results_sha256": _sha256_file(results_path),
        "metrics": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()



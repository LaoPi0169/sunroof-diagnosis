"""离线质量金丝雀 (Week 16) —— 固定考卷在固定环境下定期重考 (两种体温计之一)。

回链: week-16/task-quality-canary-contract-v1.md。与运行期 SLI (run_telemetry_replay)
的关系: 分母不同、证据不同、永不合并 —— 金丝雀度量「内容正确性」, 运行期度量
「过程健康度」。

机制 (全部复用第 12 周冻结评测管线, generation/* 零改动):
  1. 答案键 = week-12/rag-per-case-results-v1.jsonl 冻结逐例结果 (60 行,
     文件 sha256 随运行输出; 冻结物只读消费, 一字节不写);
  2. 考卷 = eval-set-v1.jsonl (60 例, 与答案键一一对应);
  3. 冻结 manifest (week-12/rag-system-manifest-v1.json) 装载时逐字节校验四个
     输入文件 sha256 (runner.load_frozen_manifest, 不符即拒绝运行 fail closed);
  4. 每例重跑 generation.run_rag_evaluation.run_case (与冻结评测同一函数),
     对照: observed mapped_state vs 冻结 expected_state (state_match)、
     observed vs 冻结 mapped_state (行为漂移)、引用/禁止引用/泄漏;
  5. 失败指路: state_match=false 的案例自动关联 week-12/failure-register-v1.jsonl
     的 failure_id —— 金丝雀的职责是「发现失败并指路登记」, 不是「假装全绿」。

已知基线 (第 12 周解封结果): 58/60 匹配; 2 例已知失败 (NS-HOLD-06 硬门:
自信作答于无答案例, 期望 refused; NS-HOLD-08: 安全侧拒答, 期望 needs_review)。
金丝雀通过判据 = 行为与冻结基线一致 (behavior_drift_cases 为空 且 已知失败
组合不变), 不是「60/60 全绿」。

用法: python -m mcptool.run_canary [--out week-16/canary-baseline-v1.jsonl]
退出码: 0 = 与冻结基线一致; 1 = 行为漂移或对账结构破坏。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from generation import run_rag_evaluation as runner  # noqa: E402
from generation.contract import load_corpus_meta  # noqa: E402

WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
FROZEN_RESULTS_PATH = os.path.join(WEEK12_DIR, "rag-per-case-results-v1.jsonl")
FAILURE_REGISTER_PATH = os.path.join(WEEK12_DIR, "failure-register-v1.jsonl")
EVAL_SET_PATH = os.path.join(PROJECT_ROOT, "eval-set-v1.jsonl")
MANIFEST_PATH = os.path.join(WEEK12_DIR, "rag-system-manifest-v1.json")

ANSWER_KEY_SOURCE = "frozen_week12_answer_key"
DENOMINATOR_NOTE = ("分母 = 冻结评测集 60 例 (考卷); 与运行期 SLI 的 task_run_id "
                    "分母无关; 两视图分子分母永不共享, 两数永不合并成一个数")


def _sha256_file(path: str, *, normalize_eol=False) -> str:
    """文件摘要; normalize_eol=True 时按 LF 归一 (对齐第 12 周冻结记录口径 ——
    工作区 checkout 为 CRLF 时冻结 sha 仍可比; raw 摘要另行保留作字节级搅动检测)。"""
    with open(path, "rb") as f:
        data = f.read()
    if normalize_eol:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _load_jsonl(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _register_index() -> dict:
    """failure-register-v1.jsonl -> {case_id: [failure_id, ...]} (失败指路用)。"""
    index = {}
    for row in _load_jsonl(FAILURE_REGISTER_PATH):
        case_ids = row.get("case_id", "")
        for case_id in [part.strip() for part in case_ids.split("/")]:
            if case_id and not case_id.endswith(".."):
                index.setdefault(case_id, []).append(row["failure_id"])
    return index


def prepare_evaluation_context():
    """共享评测上下文 (金丝雀与回放共用): (conn, manifest, meta, restricted,
    eval_rows, frozen_key)。冻结输入 sha256 校验失败 -> 直接抛异常 (fail closed)。"""
    eval_rows = {row["case_id"]: row for row in _load_jsonl(EVAL_SET_PATH)}
    frozen_rows = _load_jsonl(FROZEN_RESULTS_PATH)
    frozen_key = {row["case_id"]: row for row in frozen_rows}
    missing = sorted(set(frozen_key) - set(eval_rows))
    if missing:
        raise ValueError(f"答案键案例不在考卷中: {missing}")
    manifest = runner.load_frozen_manifest(MANIFEST_PATH)
    if not os.path.exists(runner.CORPUS_DB_PATH):
        build_corpus(runner.CORPUS_DB_PATH)
    conn = sqlite3.connect(runner.CORPUS_DB_PATH)
    meta = load_corpus_meta()
    restricted_rows = conn.execute(
        "SELECT chunk_id, document_id, body_text FROM corpus_chunks"
        " WHERE corpus_snapshot = ? AND classification != 'internal-general'",
        (runner.CORPUS_VERSION,),
    ).fetchall()
    restricted_doc_ids = {d["document_id"] for d in meta["docs"].values()
                          if d["classification"] == "internal-restricted"}
    restricted = (restricted_doc_ids, [(r[0], r[2]) for r in restricted_rows])
    return conn, manifest, meta, restricted, eval_rows, frozen_key


def run_canary(context=None, *, close_conn=False) -> tuple:
    """重考 60 例; 返回 (逐例 rows, summary)。context 缺省自建并复用连接。"""
    owns_ctx = context is None
    conn, manifest, meta, restricted, eval_rows, frozen_key = (
        context or prepare_evaluation_context())
    register = _register_index()
    rows = []
    try:
        for case_id, key in frozen_key.items():
            case = eval_rows[case_id]
            label = (key["expected_state"], ANSWER_KEY_SOURCE, {
                "gold_document_ids": key.get("label_gold_ids", []),
                "forbidden_document_ids": key.get("label_forbidden_ids", []),
            })
            record = runner.run_case(case, label, conn, manifest, meta, restricted)
            observed = record["mapped_state"]
            rows.append({
                "case_id": case_id,
                "split": key["split"],
                "family": key["family"],
                "user_role": key["user_role"],
                "expected_state": key["expected_state"],
                "observed_mapped_state": observed,
                "frozen_mapped_state": key["mapped_state"],
                "state_match": observed == key["expected_state"],
                "behavior_drift": observed != key["mapped_state"],
                "citations_ok": not record["citation_issues"],
                "forbidden_ok": not record["forbidden_doc_hits"],
                "leak_ok": not record["leak_events"],
                "corpus_version": runner.CORPUS_VERSION,
                "candidate_id": runner.CANDIDATE_ID,
            })
    finally:
        if owns_ctx or close_conn:
            conn.close()

    drift_cases = [row["case_id"] for row in rows if row["behavior_drift"]]
    mismatch_rows = [row for row in rows if not row["state_match"]]
    mismatches = [{
        "case_id": row["case_id"],
        "expected_state": row["expected_state"],
        "observed_mapped_state": row["observed_mapped_state"],
        "hard_failure": (row["expected_state"] == "refused"
                         and row["observed_mapped_state"] == "answered"),
        "register_refs": register.get(row["case_id"], []),
    } for row in mismatch_rows]
    matched = sum(1 for row in rows if row["state_match"])
    hygiene_bad = [row["case_id"] for row in rows
                   if not (row["citations_ok"] and row["forbidden_ok"]
                           and row["leak_ok"])]
    summary = {
        "view": "canary",
        "answer_key": {
            "path": os.path.relpath(FROZEN_RESULTS_PATH, PROJECT_ROOT),
            "sha256": _sha256_file(FROZEN_RESULTS_PATH, normalize_eol=True),
            "raw_sha256": _sha256_file(FROZEN_RESULTS_PATH),
            "eol_note": ("sha256 按 LF 行尾归一计算以对齐第 12 周冻结记录 "
                         "(5a614cda…dfed); raw_sha256 为磁盘字节摘要, 仅行尾搅动"
                         "不同时两者不一致 (既有口径: git restore 不入库)"),
        },
        "manifest_sha256": _sha256_file(MANIFEST_PATH),
        "versions": {
            "corpus_version": runner.CORPUS_VERSION,
            "dataset_version": runner.DATASET_VERSION,
            "retriever_version": runner.RETRIEVER_IDS["vector"],
            "candidate_id": runner.CANDIDATE_ID,
        },
        "cases_total": len(rows),
        "state_match_count": matched,
        "state_match_rate": f"{matched}/{len(rows)}",
        "behavior_drift_cases": drift_cases,
        "mismatches": mismatches,
        "hygiene_violation_cases": hygiene_bad,
        "external_write_effects": 0,
        "denominator_note": DENOMINATOR_NOTE,
        "verdict": (
            "与冻结基线一致: 行为无漂移, 已知失败组合不变 (金丝雀职责 = 发现失败"
            "并指路登记, 不是全绿)" if not drift_cases and not hygiene_bad
            else "行为漂移或语料卫生破坏 -> 与冻结基线不一致, 须人工归因"),
    }
    return rows, summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="离线质量金丝雀 (Week 16)")
    parser.add_argument("--out", default=None,
                        help="逐例对照 jsonl 输出路径 (缺省只打印汇总)")
    args = parser.parse_args(argv)
    rows, summary = run_canary()
    if args.out:
        out_path = os.path.join(PROJECT_ROOT, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    drifted = bool(summary["behavior_drift_cases"]) or bool(
        summary["hygiene_violation_cases"])
    sys.exit(1 if drifted else 0)


if __name__ == "__main__":
    main()



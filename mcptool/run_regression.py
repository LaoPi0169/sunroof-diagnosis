"""13 条回归种子工具在环对账 (Week 13 第 5 天) —— 回链 week-12/regression-seeds-v1.jsonl。

每种子流程:
  1. 以种子角色构造可信会话, 经 case.lookup_context 取回工单上下文 (3 字段);
  2. TASK_TYPE_TO_CATEGORY 冻结映射还原 category, 与 eval-set-v1.jsonl 转写逐字段
     对账 (category/region/period), 不一致即判失败 (防工单库与评测集漂移);
  3. 用工具取回字段重构 case.task_context (category/region 来自工具; business_line/
     period 为评测集保留字段, 管线不消费), 跑第 12 周冻结管线 run_case;
  4. locked_behavior 逐字段对照: state / reason_code / expected_state / mapped_state /
     citations_include / citations=[] / leak=0 / 结构化 signal。
边界: 生成层 (generation/*) 与第 12 周落盘结果零改动; 13 种子全为 dev/cal,
保留集不涉及; 工具只读 (外部写效果恒 0)。

用法: python -m mcptool.run_regression
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from generation import run_rag_evaluation as runner  # noqa: E402
from generation.contract import load_corpus_meta  # noqa: E402
from retrieval.labels import load_public_labels  # noqa: E402

from . import contract as C
from .client import McpHost, TrustedSession

WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
SEEDS_PATH = os.path.join(WEEK12_DIR, "regression-seeds-v1.jsonl")
EVAL_SET_PATH = os.path.join(PROJECT_ROOT, "eval-set-v1.jsonl")
MANIFEST_PATH = os.path.join(WEEK12_DIR, "rag-system-manifest-v1.json")
CORPUS_DB_PATH = runner.CORPUS_DB_PATH
FIXED_NOW = "2026-10-08T00:00:00Z"
SESSION_USERS = {"support_agent": "user-zhou-ning",
                 "supervisor": "user-gao-supervisor"}
_SIGNAL_PREFIX_RE = re.compile(r"^([a-z_]+)=(true|false|[a-z_]+)$")


def _load_jsonl(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _context_consistent(tool_ctx: dict, eval_ctx: dict) -> dict:
    """工具取回上下文与评测集转写的冻结映射对账。"""
    mapped = C.TASK_TYPE_TO_CATEGORY.get(tool_ctx.get("task_type"))
    return {
        "category_via_map": mapped == eval_ctx["category"],
        "region_verbatim": tool_ctx.get("region") == eval_ctx["region"],
        "purchased_at_year": str(tool_ctx.get("purchased_at", ""))[:4] == eval_ctx["period"],
    }


def _check_locked(locked: dict, record: dict) -> dict:
    """locked_behavior 逐字段对照; 非结构化自由文本描述不做断言 (随报告透传)。"""
    checks = {"state": record["state"] == locked["state"]}
    if "reason_code" in locked:
        checks["reason_code"] = record["reason_code"] == locked["reason_code"]
    if "citations_include" in locked:
        checks["citations_include"] = any(
            locked["citations_include"] in citation["document_id"]
            for citation in record["citations"])
    if "expected_state" in locked:
        checks["expected_state"] = record["expected_state"] == locked["expected_state"]
    if "mapped_state" in locked:
        checks["mapped_state"] = record["mapped_state"] == locked["mapped_state"]
    if "citations" in locked:
        checks["citations_empty"] = record["citations"] == locked["citations"]
    if "leak" in locked:
        checks["leak_zero"] = record["leak_events"] == locked["leak"]
    if "signal" in locked:
        prefix = locked["signal"].split("（")[0].strip()
        match = _SIGNAL_PREFIX_RE.match(prefix)
        if match:
            key, expected = match.group(1), match.group(2)
            expected_value = {"true": True, "false": False}.get(expected, expected)
            checks[f"signal_{key}"] = record["signals"].get(key) == expected_value
    return checks

def _run_seed(seed: dict, case: dict, conn, manifest, meta, restricted,
              public) -> dict:
    """单种子: 工具取上下文 -> 映射对账 -> 冻结管线重跑 -> 锁定行为对照。"""
    session = TrustedSession("northstar-training",
                             SESSION_USERS[seed["user_role"]], seed["user_role"])
    host = McpHost(session, clock=lambda: FIXED_NOW)
    tool_res = host.call(
        {"case_id": case["case_id"], "fields": list(C.FIELD_ENUM)})
    tool_ok = tool_res.get("status") == "ok"
    tool_ctx = tool_res["records"][0]["context"] if tool_ok else None
    consistency = (_context_consistent(tool_ctx, case["task_context"])
                   if tool_ok else {"tool_call_failed": True})
    context_ok = tool_ok and all(consistency.values())
    checks, record_state = {}, None
    if context_ok:
        # 工具在环: task_context 的 category/region 由工具取回字段派生
        case_via_tool = {**case, "task_context": {
            "category": C.TASK_TYPE_TO_CATEGORY[tool_ctx["task_type"]],
            "region": tool_ctx["region"],
            "business_line": case["task_context"]["business_line"],
            "period": case["task_context"]["period"]}}
        label = runner._expected_state_of(
            case["case_id"], case["split"], public, {})
        record = runner.run_case(case_via_tool, label, conn, manifest,
                                 meta, restricted)
        checks = _check_locked(seed["locked_behavior"], record)
        record_state = record["state"]
    return {
        "seed_id": seed["seed_id"],
        "case_id": seed["case_id"],
        "user_role": seed["user_role"],
        "state": record_state,
        "tool_context": tool_ctx,
        "context_consistent": context_ok,
        "consistency_detail": consistency,
        "checks": checks,
        "match": context_ok and bool(checks) and all(checks.values()),
    }


def run_all():
    """跑全部 13 条种子; 返回 (逐行结果, 总结)。"""
    seeds = _load_jsonl(SEEDS_PATH)
    eval_rows = {row["case_id"]: row for row in _load_jsonl(EVAL_SET_PATH)}
    manifest = runner.load_frozen_manifest(MANIFEST_PATH)
    if not os.path.exists(CORPUS_DB_PATH):
        build_corpus(CORPUS_DB_PATH)
    conn = sqlite3.connect(CORPUS_DB_PATH)
    public = load_public_labels()
    meta = load_corpus_meta()
    restricted_rows = conn.execute(
        "SELECT chunk_id, document_id, body_text FROM corpus_chunks"
        " WHERE corpus_snapshot = ? AND classification != 'internal-general'",
        (runner.CORPUS_VERSION,),
    ).fetchall()
    restricted_doc_ids = {d["document_id"] for d in meta["docs"].values()
                          if d["classification"] == "internal-restricted"}
    restricted = (restricted_doc_ids, [(r[0], r[2]) for r in restricted_rows])
    rows = []
    try:
        for seed in seeds:
            rows.append(_run_seed(seed, eval_rows[seed["case_id"]], conn,
                                  manifest, meta, restricted, public))
    finally:
        conn.close()
    identical = sum(1 for row in rows if row["match"])
    summary = {
        "seeds_total": len(rows),
        "identical": identical,
        "mismatches": [row["seed_id"] for row in rows if not row["match"]],
        "verdict": f"{identical}/{len(rows)} identical",
        "tool": C.TOOL_NAME,
        "tool_version": C.TOOL_VERSION,
        "external_write_effects": 0,
        "pipeline_unchanged": "generation/* 与第 12 周落盘结果零改动",
    }
    return rows, summary


def main(argv=None) -> None:
    rows, summary = run_all()
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if not summary["mismatches"] else 1)


if __name__ == "__main__":
    main()


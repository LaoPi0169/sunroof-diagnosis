"""
评测集校验器 (Week 10) —— 回链 specs/mission-week-10.md 第 4 天

对 eval-set-v1.jsonl 做确定性校验 (零模型调用):
  V1 结构: 必备输入字段齐全; 输入行不携带任何标签字段 (封存边界); 版本/角色/类别合法
  V2 矩阵: 36/12/12 与 24/10/8/8/10 及族×split 细分精确成立
  V3 语料引用: 封存标签 gold/forbidden 与类别参考文档都在第 9 周快照清单内
  V4 近重复: 同 split 内归一化 query 零重复; 跨 split 归一化 query 零泄漏
  V5 封存: 保留集标签键与 holdout case_id 一一对应, 期望状态与事实字段合法

输出: JSON 报告 {rows, matrix, findings, verdict}; 退出码 0=VALID, 1=INVALID。
用法: python evaluation/validate_eval_set.py [--eval-set eval-set-v1.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from evaluation.eval_contract import (  # noqa: E402
    CATEGORY_CONTRACTS,
    CORPUS_VERSION,
    DATASET_VERSION,
    EXPECTED_STATES,
    FAMILY_BY_SPLIT,
    FAMILY_TOTALS,
    LABEL_FIELDS,
    REQUIRED_INPUT_FIELDS,
    ROLES,
    SPLIT_TOTALS,
    corpus_document_ids,
    family_of,
    load_eval_set,
    load_holdout_labels,
)


def normalize_query(text: str) -> str:
    """近重复归一化: 去空白与中英标点后小写 (跨 split 泄漏检查的确定性口径)。"""
    return re.sub(r"[\s，。、,\.?？!！：:；;（）()\-—]", "", text).lower()


def validate(rows: list, corpus_ids: set, labels: dict) -> tuple:
    """返回 (findings, matrix); findings 为空 = 合同满足。"""
    findings = []
    seen_case_ids = set()
    seen_queries = {}
    matrix: dict = {}
    for row in rows:
        case_id = row.get("case_id", "?")
        split = row.get("split")
        # V1 结构与封存边界
        for field in REQUIRED_INPUT_FIELDS:
            if field not in row:
                findings.append({"rule": "V1-missing-input-field", "case_id": case_id,
                                 "detail": "缺少必备输入字段 " + field})
        for label_field in LABEL_FIELDS:
            if label_field in row:
                findings.append({"rule": "V1-label-in-input", "case_id": case_id,
                                 "detail": "输入行携带标签字段 " + label_field})
        if row.get("dataset_version") != DATASET_VERSION:
            findings.append({"rule": "V1-dataset-version", "case_id": case_id,
                             "detail": "dataset_version 必须为 " + DATASET_VERSION})
        if row.get("corpus_version") != CORPUS_VERSION:
            findings.append({"rule": "V1-corpus-version", "case_id": case_id,
                             "detail": "corpus_version 必须为 " + CORPUS_VERSION})
        if row.get("user_role") not in ROLES:
            findings.append({"rule": "V1-role", "case_id": case_id,
                             "detail": "user_role 非法"})
        category = row.get("task_context", {}).get("category")
        if category not in CATEGORY_CONTRACTS:
            findings.append({"rule": "V1-category", "case_id": case_id,
                             "detail": "task_context.category 非法"})
        if not str(row.get("query", "")).strip():
            findings.append({"rule": "V1-empty-query", "case_id": case_id,
                             "detail": "query 为空"})
        if not row.get("risk_tags"):
            findings.append({"rule": "V1-risk-tags", "case_id": case_id,
                             "detail": "缺少风险标签"})
        if case_id in seen_case_ids:
            findings.append({"rule": "V1-duplicate-case-id", "case_id": case_id,
                             "detail": "case_id 重复"})
        seen_case_ids.add(case_id)
        # V2 矩阵累计
        family = family_of(row) if category in CATEGORY_CONTRACTS else "?"
        cell = matrix.setdefault(split, {})
        cell[family] = cell.get(family, 0) + 1
        # V4 近重复与跨 split 泄漏
        key = normalize_query(str(row.get("query", "")))
        if key in seen_queries:
            prior = seen_queries[key]
            if prior["split"] == split:
                findings.append({"rule": "V4-duplicate-query", "case_id": case_id,
                                 "detail": "与 " + prior["case_id"] + " 同 split 归一化重复"})
            else:
                findings.append({"rule": "V4-cross-split-leak", "case_id": case_id,
                                 "detail": "与 " + prior["case_id"] + " 跨 split 近重复"})
        else:
            seen_queries[key] = {"case_id": case_id, "split": split}
    # V2 计数精确断言
    split_counts = {s: sum(c.values()) for s, c in matrix.items()}
    if split_counts != SPLIT_TOTALS:
        findings.append({"rule": "V2-split-totals", "case_id": "-",
                         "detail": "split 计数 " + json.dumps(split_counts, sort_keys=True)
                                   + " != " + json.dumps(SPLIT_TOTALS, sort_keys=True)})
    family_counts: dict = {}
    for cells in matrix.values():
        for family, count in cells.items():
            family_counts[family] = family_counts.get(family, 0) + count
    if family_counts != FAMILY_TOTALS:
        findings.append({"rule": "V2-family-totals", "case_id": "-",
                         "detail": "状态族计数 " + json.dumps(family_counts, sort_keys=True)
                                   + " != " + json.dumps(FAMILY_TOTALS, sort_keys=True)})
    for split, expected in FAMILY_BY_SPLIT.items():
        if matrix.get(split, {}) != expected:
            findings.append({"rule": "V2-family-by-split", "case_id": "-",
                             "detail": split + " 细分 "
                                       + json.dumps(matrix.get(split, {}), sort_keys=True)
                                       + " != " + json.dumps(expected, sort_keys=True)})
    # V3 语料引用 (类别参考文档必须在第 9 周快照内)
    for category, contract in CATEGORY_CONTRACTS.items():
        for doc_id in contract["reference_docs"]:
            if corpus_ids and doc_id not in corpus_ids:
                findings.append({"rule": "V3-category-reference", "case_id": category,
                                 "detail": "类别参考文档不在第 9 周快照: " + doc_id})
    # V5 封存标签 (存在时: 键一一对应 + 引用可解析 + 状态/事实合法)
    holdout_ids = {row["case_id"] for row in rows if row.get("split") == "holdout"}
    if labels:
        if set(labels.keys()) != holdout_ids:
            findings.append({"rule": "V5-label-keys", "case_id": "-",
                             "detail": "封存标签键与保留集 case_id 不一一对应"})
        for case_id, label in labels.items():
            if label.get("expected_state") not in EXPECTED_STATES:
                findings.append({"rule": "V5-expected-state", "case_id": case_id,
                                 "detail": "期望状态非法: " + str(label.get("expected_state"))})
            for doc_id in label.get("gold_document_ids", []):
                if corpus_ids and doc_id not in corpus_ids:
                    findings.append({"rule": "V3-gold-reference", "case_id": case_id,
                                     "detail": "gold 文档不在第 9 周快照: " + doc_id})
            for doc_id in label.get("forbidden_document_ids", []):
                if corpus_ids and doc_id not in corpus_ids:
                    findings.append({"rule": "V3-forbidden-reference", "case_id": case_id,
                                     "detail": "forbidden 文档不在第 9 周快照: " + doc_id})
            if not label.get("required_facts") or not label.get("forbidden_facts"):
                findings.append({"rule": "V5-facts", "case_id": case_id,
                                 "detail": "缺少必要事实或禁止事实"})
    return findings, matrix


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="北辰 Week 10 评测集校验器")
    parser.add_argument("--eval-set", default=os.path.join(PROJECT_ROOT, "eval-set-v1.jsonl"))
    args = parser.parse_args(argv)

    eval_path = args.eval_set if os.path.isabs(args.eval_set) \
        else os.path.join(PROJECT_ROOT, args.eval_set)
    rows = load_eval_set(eval_path)
    corpus_ids = corpus_document_ids()
    labels_path = os.path.join(PROJECT_ROOT, "eval-holdout-labels-sealed-v1.json")
    labels = load_holdout_labels(labels_path) if os.path.exists(labels_path) else {}

    findings, matrix = validate(rows, corpus_ids, labels)
    report = {
        "dataset_version": DATASET_VERSION,
        "eval_set": os.path.relpath(eval_path, PROJECT_ROOT),
        "rows": len(rows),
        "matrix": matrix,
        "corpus_refs_available": bool(corpus_ids),
        "labels_sealed_present": bool(labels),
        "findings": findings,
        "verdict": "INVALID" if findings else "VALID",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if not findings else 1)


if __name__ == "__main__":
    main()


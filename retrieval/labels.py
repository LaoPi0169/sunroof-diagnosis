"""公开标签装载器 (Week 11) —— 只读解析 labeling-guide-v1.md 附录 (第 10 周冻结件)。

附录 48 条公开标签 (开发 36 + 校准 12) 是检索比较 gold 的唯一公开来源;
保留集标签封存于 eval-holdout-labels-sealed-v1.json, 本装载器不读封存件。
附录单元格为紧凑写法 (case_id 用 / 连接, 文档格带「已废弃」等注记):
用文档 ID 正则提取, '—' 记为空。装载时同时校验覆盖与语料引用可解析。
"""
from __future__ import annotations

import os
import re

from evaluation.eval_contract import (
    EVAL_SET_PATH,
    PROJECT_ROOT,
    corpus_document_ids,
    load_eval_set,
)

GUIDE_PATH = os.path.join(PROJECT_ROOT, "labeling-guide-v1.md")
DOC_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-v\d+")
CASE_ID_RE = re.compile(r"^NS-(DEV|CAL)-\d{2}$")
EMPTY_CELLS = {"", "-", "--", "—", "–"}


def _parse_table_rows(text: str) -> list:
    """提取附录表格数据行 (4 列); 跳过表头/分隔行, 忽略附录之前的内容。"""
    rows = []
    in_appendix = False
    for line in text.splitlines():
        if line.startswith("## 附录"):
            in_appendix = True
            continue
        if not in_appendix or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        if cells[0] == "case_id" or set(cells[0]) <= {"-", " ", ""}:
            continue  # 表头与分隔行
        rows.append(cells)
    return rows


def _expand_case_ids(case_cell: str) -> list:
    """展开附录紧凑写法: 'NS-DEV-01/02/10' -> NS-DEV-01/02/10 (纯数字片段继承前缀)。"""
    ids = []
    prefix = None
    for part in (c.strip() for c in case_cell.split("/")):
        if part.isdigit():
            if prefix is None:
                raise ValueError(f"附录紧凑片段缺少前缀: {case_cell!r}")
            ids.append(prefix + part)
        else:
            if not CASE_ID_RE.match(part):
                raise ValueError(f"附录 case_id 形态非法: {part!r}")
            ids.append(part)
            prefix = part[: part.rfind("-") + 1]
    return ids


def load_public_labels(guide_path: str = None) -> dict:
    """返回 {case_id: {gold_document_ids, forbidden_document_ids, expected_state_raw}}。

    校验 (任一失败即 ValueError, 不产出半成品标签):
      1. case_id 形态合法且无重复;
      2. 覆盖与 eval-set 的 development+calibration 48 条一一对应;
      3. 引用的 gold/forbidden 文档全部在第 9 周快照清单内 (与第 10 周验收门第 3 条同源)。
    """
    path = guide_path or GUIDE_PATH
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    labels = {}
    for case_cell, gold_cell, forbidden_cell, expected_cell in _parse_table_rows(text):
        gold = [] if gold_cell in EMPTY_CELLS else DOC_ID_RE.findall(gold_cell)
        forbidden = ([] if forbidden_cell in EMPTY_CELLS
                     else DOC_ID_RE.findall(forbidden_cell))
        for case_id in _expand_case_ids(case_cell):
            if not CASE_ID_RE.match(case_id):
                raise ValueError(f"附录 case_id 形态非法: {case_id!r}")
            if case_id in labels:
                raise ValueError(f"附录 case_id 重复: {case_id}")
            labels[case_id] = {
                "gold_document_ids": gold,
                "forbidden_document_ids": forbidden,
                "expected_state_raw": expected_cell,
            }

    eval_ids = {row["case_id"] for row in load_eval_set(EVAL_SET_PATH)
                if row["split"] != "holdout"}
    if set(labels) != eval_ids:
        raise ValueError(
            "公开标签覆盖不匹配: missing=%s extra=%s"
            % (sorted(eval_ids - set(labels)), sorted(set(labels) - eval_ids)))

    corpus_ids = corpus_document_ids()
    for case_id, label in labels.items():
        for doc_id in label["gold_document_ids"] + label["forbidden_document_ids"]:
            if doc_id not in corpus_ids:
                raise ValueError(f"{case_id} 引用文档不在第 9 周快照: {doc_id}")
    return labels

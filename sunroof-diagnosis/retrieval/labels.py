"""公开标签装载器 (Week 11) —— 只读解析 labeling-guide-v1.md 附录 (第 10 周冻结件)。

附录 48 条公开标签 (开发 36 + 校准 12) 是检索比较 gold 的唯一公开来源;
保留集标签封存于 eval-holdout-labels-sealed-v1.json, 本装载器不读封存件。
本仓库附录为两个 ```json 分块 (labels-a / labels-b, 见 labeling-guide-v1.md §三),
按 case_id 合并; 装载时校验覆盖与语料引用可解析。
"""
from __future__ import annotations

import json
import os
import re

from evaluation.eval_contract import (
    EVAL_SET_PATH,
    PROJECT_ROOT,
    corpus_document_ids,
    load_eval_set,
)

GUIDE_PATH = os.path.join(PROJECT_ROOT, "labeling-guide-v1.md")
CASE_ID_RE = re.compile(r"^SR-(DEV|CAL)-\d{2}$")
_APPENDIX_HEADER = "## 三、公开标签附录"
_FENCE_RE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*(?:\r?\n|$)", re.S)


def _appendix_blocks(text: str) -> list:
    """提取附录小节内的全部 json 围栏块 (附录之前的内容不参与)。"""
    start = text.find(_APPENDIX_HEADER)
    if start < 0:
        raise ValueError("labeling-guide-v1.md 缺少公开标签附录小节")
    return [m.group(1) for m in _FENCE_RE.finditer(text[start:])]


def load_public_labels(guide_path: str = None) -> dict:
    """返回 {case_id: {gold_document_ids, forbidden_document_ids, expected_state,
    family, required_facts, forbidden_facts, risk_notes}}。

    校验 (任一失败即 ValueError, 不产出半成品标签):
      1. case_id 形态合法且无重复;
      2. 覆盖与 eval-set 的 development+calibration 48 条一一对应;
      3. 引用的 gold/forbidden 文档全部在第 9 周快照清单内 (与第 10 周验收门第 3 条同源)。
    """
    path = guide_path or GUIDE_PATH
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    labels = {}
    for block in _appendix_blocks(text):
        for entry in json.loads(block):
            case_id = entry.get("case_id", "")
            if not CASE_ID_RE.match(case_id):
                raise ValueError(f"附录 case_id 形态非法: {case_id!r}")
            if case_id in labels:
                raise ValueError(f"附录 case_id 重复: {case_id}")
            labels[case_id] = entry

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

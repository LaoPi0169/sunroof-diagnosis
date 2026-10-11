"""离线质量金丝雀 (Week 16) —— 冻结答案键重考 60 例, 只发现新漂移不掩盖旧失败。

分母纪律: 金丝雀分母 = 考卷 60 例, 与运行期 SLI 分母 (窗口任务数) 永不共享。
用法: python -m mcptool.run_canary [--out week-16/canary-baseline-v1.jsonl]
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

from evaluation.eval_contract import (  # noqa: E402
    EVAL_SET_PATH,
    load_eval_set,
    load_holdout_labels,
)
from generation.contract import answer_case  # noqa: E402
from generation.run_rag_evaluation import RESULTS_PATH  # noqa: E402

WEEK16_DIR = os.path.join(PROJECT_ROOT, "week-16")
KNOWN_FAILURE_F12 = {}  # 本仓库第 12 周 60/60 全对齐, 无已知失败指路口径 (如实为空)


def run_canary(out_path: str = None) -> dict:
    if not os.path.exists(RESULTS_PATH):
        raise RuntimeError("第 12 周结果文件缺失 (答案键), 先完成第 12 周评测")
    frozen = {row["case_id"]: row for row in
              (json.loads(line) for line in open(RESULTS_PATH, encoding="utf-8")
               if line.strip())}
    conn = sqlite3.connect(os.path.join(PROJECT_ROOT, "data", "corpus-w09.db"))
    conn.row_factory = sqlite3.Row
    rows = []
    try:
        for case in load_eval_set(EVAL_SET_PATH):
            rec = answer_case(case, conn)
            frozen_row = frozen[case["case_id"]]
            drift = rec["state"] != frozen_row["state"]
            rows.append({
                "case_id": case["case_id"],
                "frozen_state": frozen_row["state"],
                "canary_state": rec["state"],
                "behavior_drift": drift,
                "f12_register": KNOWN_FAILURE_F12.get(case["case_id"]),
            })
    finally:
        conn.close()
    drift_cases = [row["case_id"] for row in rows if row["behavior_drift"]]
    os.makedirs(os.path.dirname(out_path or WEEK16_DIR), exist_ok=True)
    out = out_path or os.path.join(WEEK16_DIR, "canary-baseline-v1.jsonl")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"cases": len(rows), "behavior_drift_cases": drift_cases,
            "verdict": "DRIFT" if drift_cases else "CONSISTENT", "output": out}


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 16 离线金丝雀")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    print(json.dumps(run_canary(args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

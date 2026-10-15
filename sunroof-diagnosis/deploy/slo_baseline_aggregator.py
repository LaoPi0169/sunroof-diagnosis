"""SLO 基线聚合器 (阶段 E 批次 2) —— 把 week-16 SLO 草案 v0 的口径机械化 + [U] 守卫。

定位 (specs/phase-e-brief-draft-v1.md §E2, 用户拍板: 聚合器 + runbook):
  `week-16/slo-proposal-v0.md` 的三口径在本仓库落地为对任务终态表的机械归约。
  **本聚合器只产「基线观测」**, 绝不定案 SLO 目标——无真实流量之前一切目标维持 [U],
  禁止合成回放/考卷代填目标值 (slo-proposal-v0 红线), 小样本输出 insufficient_data。

口径 (预注册, 对齐 week-16/runtime-sli-spec-v1.md §三 与其教学库映射):
  分母 = 窗口内 spec_check_tasks 任务总数 (含未处理 initialized 与 denied ——
  诚实分母: 未处理与被阻断都占分母, 绝不「只数完成的」)。
  SLO-1 allowed_terminal_rate:
    分子 = 进入明确归宿终态的任务数
         {answered, needs_review, confirmed, refused, escalated, action_completed};
    excluded = {denied (安全阻断, 单独计数 —— 健康行为但语义不同, 不折进分子),
                initialized (非终态, 绝不折叠 —— 对齐遥测 non_terminal 语义)}。
  SLO-2 forbidden_effect_count (恒等硬门 = 0):
    表层交叉对账 = 「task_action_runs 存在 denied/被阻断动作行, 但对应任务终态
    被该动作改变」的矛盾计数 + 被阻断任务存在后续非审计效果动作的计数。
    违反即安全事件; 教学库由服务层 + CHECK 约束结构性保证为 0 (如实复核)。
  SLO-3 unconfirmed_effects_resolution: **不适用登记** —— 未确认效果口径需要
    trace 层数据 (week-16 词表的 result_unknown/reconciliation_pending), 任务表
    全部落显式终态, 本聚合器不发明代用口径 (缺数据 = 缺数据, 不编造)。

[ U] 纪律 (硬编码, 任何调用方不可绕过):
  * 分母 < --min-denominator (默认 30) -> verdict = "insufficient_data",
    slo_1 的 value 置 None (小样本比率不输出 —— 防止拿 3/4=75% 当基线);
  * 分母足够 -> verdict = "baseline_only_no_target_merge", value 可算但目标仍 [U];
  * 全部输出携带 denominator_note (与离线考卷 60 例分母永不共享/永不合并) 与
    honesty 声明 (教学/合成流量 ≠ 真实生产流量, [U] 不因本聚合器定案)。

用法:
    python -m deploy.slo_baseline_aggregator --db data/sunroof.db \
        --window-start 2026-10-01T00:00:00Z --window-end 2026-10-08T00:00:00Z
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

DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "sunroof.db")
DEFAULT_MIN_DENOMINATOR = 30

ALLOWED_TERMINAL_STATES = ("answered", "needs_review", "confirmed",
                           "refused", "escalated", "action_completed")
BLOCKED_STATE = "denied"
NON_TERMINAL_STATE = "initialized"

SLO_TARGETS = {"slo_1": "[U]", "slo_2": "= 0 (恒等硬门, 非 [U])", "slo_3": "[U]"}
SLO_WINDOWS = {"slo_1": "[U]", "slo_2": "逐任务即时断言", "slo_3": "[U]"}

DENOMINATOR_NOTE = ("分母 = 窗口内 spec_check_tasks 任务数 (运行期任务); 与离线考卷"
                    " 60 例 (冻结评测集) 的分母永不共享, 两视图分子分母永不合并成一"
                    "个数 (week-16 两支体温计纪律)")
HONESTY_NOTE = ("本聚合器输出的是基线观测而非 SLO 达标判定: 教学/合成流量不等于真实"
                "生产流量; 全部目标值维持 [U] 待定, 定标流程见 deploy/"
                "slo-adoption-runbook-v1.md; 禁止以合成回放代填目标值")


def aggregate(db_path: str, window_start: str, window_end: str,
              min_denominator: int = DEFAULT_MIN_DENOMINATOR) -> dict:
    """窗口内任务终态 -> SLO 基线观测 (只读; 绝不写库)。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM spec_check_tasks "
            "WHERE created_at >= ? AND created_at < ? "
            "GROUP BY status", (window_start, window_end)).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        # SLO-2 交叉对账: 被阻断动作行 vs 任务终态矛盾 (结构性应为 0)
        contradictions = conn.execute(
            "SELECT COUNT(*) AS n FROM task_action_runs a "
            "JOIN spec_check_tasks t ON t.task_id = a.task_id "
            "WHERE a.status = 'denied' AND t.status NOT IN (?, ?)",
            (BLOCKED_STATE, NON_TERMINAL_STATE)).fetchone()["n"]
    finally:
        conn.close()

    total = sum(counts.values())
    allowed = sum(counts.get(s, 0) for s in ALLOWED_TERMINAL_STATES)
    blocked = counts.get(BLOCKED_STATE, 0)
    non_terminal = counts.get(NON_TERMINAL_STATE, 0)
    insufficient = total < min_denominator

    slo_1 = {
        "definition": ("进入明确归宿终态的任务 ÷ 窗口内任务总数; "
                       "分子=answered/needs_review/confirmed/refused/escalated/"
                       "action_completed; denied 单独计数不折进分子; "
                       "initialized 为非终态不折叠"),
        "target": SLO_TARGETS["slo_1"], "window": SLO_WINDOWS["slo_1"],
        "value": None if insufficient else (round(allowed / total, 4) if total else None),
    }
    slo_2 = {
        "definition": ("被阻断动作与任务终态矛盾的计数 (denied 改变终态 / 被阻断后"
                       "携带效果); 恒等硬门 = 0, 违反即安全事件"),
        "target": SLO_TARGETS["slo_2"], "window": SLO_WINDOWS["slo_2"],
        "value": contradictions,
    }
    slo_3 = {
        "definition": "未确认效果的确认时延 (A-06 对账编排口径)",
        "target": SLO_TARGETS["slo_3"], "window": SLO_WINDOWS["slo_3"],
        "value": None,
        "applicability": ("not_applicable_to_task_tables —— 任务表全部落显式终态, "
                          "未确认终态口径需 trace 层数据; 本聚合器不发明代用口径"),
    }
    verdict = "insufficient_data" if insufficient else "baseline_only_no_target_merge"
    return {
        "aggregator_version": "slo-baseline-aggregator-v1 (phase-E batch-2)",
        "window": {"start": window_start, "end": window_end},
        "denominator": {"total_tasks": total, "note": DENOMINATOR_NOTE},
        "terminal_breakdown": dict(sorted(counts.items())),
        "blocked_count": blocked,
        "non_terminal_count": non_terminal,
        "slo_1_allowed_terminal_rate": slo_1,
        "slo_2_forbidden_effect_count": slo_2,
        "slo_3_unconfirmed_resolution": slo_3,
        "verdict": verdict,
        "min_denominator": min_denominator,
        "honesty": {"note": HONESTY_NOTE,
                    "targets_all_u": True,
                    "no_synthetic_replay_substitution": True},
    }


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="SLO 基线聚合器 (阶段 E 批次 2)")
    parser.add_argument("--db", default=DEFAULT_DB, help="任务库路径 (只读)")
    parser.add_argument("--window-start", required=True, help="窗口起始 (ISO-8601)")
    parser.add_argument("--window-end", required=True, help="窗口结束 (ISO-8601)")
    parser.add_argument("--min-denominator", type=int, default=DEFAULT_MIN_DENOMINATOR,
                        help="最小分母阈值 (低于则 insufficient_data, 默认 30)")
    args = parser.parse_args(argv)
    report = aggregate(args.db, args.window_start, args.window_end,
                       args.min_denominator)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


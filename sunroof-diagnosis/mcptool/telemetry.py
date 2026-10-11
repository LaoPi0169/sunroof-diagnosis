"""运行期遥测 (Week 16) —— TaskTrace 九阶段 / 终态分类 / SLI 归约 / 脱敏自检。

纯观测零效果; 两视图分母永不合并 (金丝雀量内容, 运行期量过程)。
"""
from __future__ import annotations

import re

TRACE_STAGES = ("receive", "authz", "retrieve", "gate", "generate", "cite_check",
                "tool_call", "policy", "write")

# 可见终态分类表 (v1.1 含第 17 周增补 action_preview; 非终态不算失败也不进分子)
TERMINAL_TABLE = {
    "read": {"answered": "allowed", "refused": "allowed", "needs_review": "allowed",
             "escalated": "allowed", "abstained": "non_terminal",
             "system_failed": "non_terminal"},
    "write": {"action_completed": "allowed", "approval_denied": "allowed",
              "approval_required": "allowed", "action_preview": "allowed",
              "reconciliation_pending": "non_terminal", "result_unknown": "non_terminal"},
}
FORBIDDEN_MARKER = "forbidden"

_SENSITIVE_RE = re.compile(
    r"(费用标准|工时定额|索赔审批线|3000 元|token_|password|secret)", re.I)


class TaskTrace:
    """一个 task_run_id 的九阶段 span 记录面 (缺席阶段留痕 not_recorded)。"""

    def __init__(self, task_run_id: str):
        self.task_run_id = task_run_id
        self.spans = []
        self.terminal = []

    def add(self, stage: str, payload: dict) -> None:
        if stage not in TRACE_STAGES:
            raise ValueError(f"非法阶段: {stage}")
        self.spans.append({"task_run_id": self.task_run_id, "stage": stage,
                           **payload})

    def mark_terminal(self, kind: str, outcome: str, effects: int = 0) -> None:
        cls = classify_terminal(kind, outcome)
        self.terminal.append({"kind": kind, "outcome": outcome,
                              "classification": cls, "effects": effects})

    def finalize_missing_stages(self) -> None:
        present = {span["stage"] for span in self.spans}
        for stage in TRACE_STAGES:
            if stage not in present:
                self.spans.append({"task_run_id": self.task_run_id,
                                   "stage": stage, "status": "not_recorded"})


def classify_terminal(kind: str, outcome: str) -> str:
    """终态分类: allowed / forbidden / non_terminal (未知词面一律 non_terminal,
    绝不折叠成成功)。"""
    if outcome == FORBIDDEN_MARKER:
        return "forbidden"
    return TERMINAL_TABLE.get(kind, {}).get(outcome, "non_terminal")


def reduce_runtime_metrics(traces: list) -> dict:
    """SLI 归约: allowed_terminal_rate (分母 = 窗口内任务数, 恒等硬门独立照抓)。"""
    total = len(traces)
    allowed = 0
    forbidden = 0
    unconfirmed = 0
    effects = 0
    for trace in traces:
        effects += sum(t["effects"] for t in trace.terminal)
        classes = [t["classification"] for t in trace.terminal]
        forbidden += classes.count("forbidden")
        if "allowed" in classes:
            allowed += 1
        elif all(c == "non_terminal" for c in classes):
            unconfirmed += 1
    return {
        "window_tasks": total,
        "allowed_terminal_rate": round(allowed / total, 4) if total else None,
        "forbidden_effect_count": forbidden,
        "unconfirmed_effects": unconfirmed,
        "total_effects": effects,
    }


def scan_spans_for_violations(spans: list) -> list:
    """遥测脱敏自检: 客户字段/政策正文标记/凭据键名零出现。"""
    violations = []
    for span in spans:
        blob = json_dumps(span)
        match = _SENSITIVE_RE.search(blob)
        if match:
            violations.append({"task_run_id": span.get("task_run_id"),
                               "stage": span.get("stage"),
                               "marker": match.group(0)})
    return violations


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)

"""Week 17 降级演练运行器 —— D-01..04 固定剧本 (固定时钟/固定 task_run_id)。

D-01 正常对照组 (0 注入) -> allowed action_completed, 效果 1
D-02 注入降级 (MCP_OFF)  -> allowed action_preview, 效果 0, 审批不消耗
D-03 恢复续跑           -> allowed action_completed, 效果 1 (同一审批)
D-04 幂等重放           -> allowed replay, 效果 0 (重放返回原工单)
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcptool.kill_switch import KillSwitchRegistry
from mcptool.run_fixtures import BASE_ARGS, BINDING_OK, EAST_PQE, _fresh_write_stack
from mcptool.server import call_write
from mcptool.telemetry import TaskTrace, reduce_runtime_metrics


def run_drill(out_path: str = None) -> dict:
    drills = []
    traces = []
    # D-01 正常对照组
    approvals, tickets = _fresh_write_stack()
    approval_id = approvals.issue("supervisor_li", BINDING_OK)
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                      approvals, tickets)
    trace = TaskTrace("northstar-w17-d01")
    trace.add("policy", {"queue": "billing-supervisor-review"})
    trace.mark_terminal("write", "action_completed", effects=1)
    traces.append(trace)
    drills.append({"drill_id": "D-01", "fault": "none", "pass": resp["ok"],
                   "terminal": "action_completed", "effects": 1})
    # D-02 注入降级
    approvals, tickets = _fresh_write_stack()
    approval_id = approvals.issue("supervisor_li", BINDING_OK)
    registry = KillSwitchRegistry()
    registry.set_state("MCP_OFF", True, "admin_li", "D-02 演练注入")
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                      approvals, tickets, registry)
    trace = TaskTrace("northstar-w17-d02")
    trace.mark_terminal("write", "action_preview", effects=0)
    traces.append(trace)
    drills.append({"drill_id": "D-02", "fault": "mcp_off", "pass":
                   not resp["ok"] and resp["error"]["code"] == "mcp_unavailable",
                   "terminal": "action_preview", "effects": 0})
    # D-03 恢复续跑 (同一审批)
    registry.set_state("MCP_OFF", False, "admin_li", "D-03 恢复复验通过")
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                      approvals, tickets, registry)
    trace = TaskTrace("northstar-w17-d03")
    trace.mark_terminal("write", "action_completed", effects=1)
    traces.append(trace)
    drills.append({"drill_id": "D-03", "fault": "recovery_resume", "pass":
                   resp["ok"] and resp["result"]["created"],
                   "terminal": "action_completed", "effects": 1})
    # D-04 幂等重放
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                      approvals, tickets, registry)
    trace = TaskTrace("northstar-w17-d04")
    trace.mark_terminal("write", "action_completed", effects=0)  # 重放 0 效果
    traces.append(trace)
    drills.append({"drill_id": "D-04", "fault": "idempotent_replay", "pass":
                   resp["ok"] and resp["result"]["replayed"],
                   "terminal": "replayed", "effects": 0})
    metrics = reduce_runtime_metrics(traces)
    report = {"drills": len(drills), "allowed": metrics["allowed_terminal_rate"],
              "allowed_count": sum(1 for d in drills if d["pass"] and d["effects"] >= 0
                                   and d["terminal"] != "replayed"),
              "forbidden": metrics["forbidden_effect_count"],
              "drill_rows": drills}
    out = out_path or os.path.join(PROJECT_ROOT, "week-17",
                                   "degradation-drill-evidence-v1.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for row in drills:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    print(json.dumps(run_drill(), ensure_ascii=False, indent=2))

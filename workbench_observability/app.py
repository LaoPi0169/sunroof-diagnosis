"""业务用户可观测性工作台 (Week 16) —— 浏览器点击式「两种体温计」测试入口, 全程无命令行。

交付对象: 业务用户 —— 只操作浏览器页面 (workbench_observability/static/index.html),
不接触命令行、不安装环境。交付人双击 start_workbench_observability.bat (或
python -m uvicorn workbench_observability.app:app --host 127.0.0.1 --port 8016)
启动一次, 测试者打开 http://127.0.0.1:8016, 按
week-16/business-user-test-script-v1.md (O-00..O-06) 逐步测试。

定位 (回链 week-16/ 三份合同 + 手册 3667-3888):
  - 本模块是 mcptool.run_canary / mcptool.run_telemetry_replay 之上的薄 HTTP 壳:
    不实现任何新业务规则、不新增任何写入口 (模型可调用工具仍 1 读 + 3 写);
  - 「两张体温计」并排展示: 离线考卷金丝雀 (60 例冻结分母) vs 运行期 SLI 回放
    (10 个 task_run_id 分母) —— 两个数字在页面上物理分开, 永不相加/平均/对比;
  - 任务追踪面板: 任选 task_run_id 还原九阶段链 (两断点: RAG->策略版本链 /
    MCP->审批+幂等键);
  - 脱敏检查面板: 对当前 span 流机械扫描 (正文/载荷/凭据键零出现);
  - SLO 面板: 草案 v0 全部目标/窗口标 [U], 页面明示「非达标声明」。
教学模拟边界: 单用户单会话; 时钟固定 2026-10-09T00:00:00Z (回放可复现);
本文件已入 ci/redaction_scan.py SCOPE_FILES (凭据/政策正文零出现)。
"""
from __future__ import annotations

import os
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from mcptool.run_canary import run_canary
from mcptool.run_telemetry_replay import run_replay
from mcptool.telemetry import scan_spans_for_violations

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX_HTML = os.path.join(_STATIC_DIR, "index.html")

# SLO 草案 v0 (机读底稿; 全文见 week-16/slo-proposal-v0.md)
SLO_PROPOSAL_V0 = {
    "proposal_id": "slo-proposal-v0",
    "status": "draft-v0 (非达标声明: 无真实流量, 全部目标/窗口为 [U] 待定)",
    "definition_of_u": ("[U] = 待定。无真实流量数据, 目标值与窗口须上线后以真实"
                        "基线评审确定; 禁止用合成回放/考卷结果代填 [U]。"),
    "slos": [
        {"id": "SLO-1", "name": "allowed_terminal_rate",
         "sli": "allowed 可见终态任务数 / 窗口内 task_run_id 总数",
         "target": "[U]", "window": "[U]",
         "baseline_demo": "合成回放 O-01..O-10 = 8/10 (口径演示, 非真实流量基线)"},
        {"id": "SLO-2", "name": "forbidden_effect_count",
         "sli": "拒/撤/系统失败终态任务的效果增量总和",
         "target": "= 0 (合同硬门, 恒等约束非 [U]; 第 14/15 周夹具已证)",
         "window": "逐任务即时断言 (违反即安全事件)"},
        {"id": "SLO-3", "name": "unconfirmed_effects_resolution",
         "sli": "result_unknown/reconciliation_pending 任务的效果确认时延",
         "target": "[U]", "window": "[U]",
         "baseline_demo": "回放 O-08 携带 1 个未确认效果 (对账后归零或保持待办)"},
        {"id": "SLO-4", "name": "canary_drift_alert_latency",
         "sli": "考卷金丝雀行为漂移从发生到告警的时延",
         "target": "[U]", "window": "[U] (发版前强制 + 定期节奏待定)",
         "baseline_demo": "60 例重考 ~2s; 行为漂移 = 退出码 1"},
        {"id": "SLO-5", "name": "human_review_sampling_rate",
         "sli": "抽样复核覆盖率 (人工对答案, 不能拿金丝雀代填)",
         "target": "[U]", "window": "[U]"},
    ],
    "review_inputs": ["真实流量量级与峰谷", "值班/告警接警能力",
                      "工单/升级处理 SLA", "合规与审计要求"],
    "view_note": ("SLO-1 属运行期体温计; 考卷金丝雀分数 (58/60) 不是 SLO, "
                  "是内容正确性回归门 —— 两数永不合并。"),
}

_STATE = {"replay": None, "canary": None, "updated_at": None}


def _snapshot():
    replay = _STATE["replay"]
    canary = _STATE["canary"]
    return {
        "has_replay": replay is not None,
        "replay_seed": replay["seed"] if replay else None,
        "tasks": replay["tasks"] if replay else [],
        "metrics": replay["metrics"] if replay else None,
        "has_canary": canary is not None,
        "canary_summary": canary["summary"] if canary else None,
        "updated_at": _STATE["updated_at"],
    }


app = FastAPI(title="Week 16 可观测性 · 业务用户工作台")


@app.get("/", include_in_schema=False)
def index():
    """前台页面 (中文, 原生 HTML/JS, 零依赖零构建)。"""
    return FileResponse(_INDEX_HTML)


@app.get("/api/state")
def get_state():
    return _snapshot()


@app.post("/api/reset")
def reset():
    """重置: 清空回放与考卷结果 (冻结物/组件不受影响)。"""
    _STATE["replay"] = None
    _STATE["canary"] = None
    _STATE["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return _snapshot()


@app.post("/api/replay")
def replay():
    """运行期视图: 跑十任务合成回放 (固定时钟, 全新 task_run_id)。"""
    _STATE["replay"] = run_replay()
    _STATE["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    replay_result = _STATE["replay"]
    return {"seed": replay_result["seed"], "tasks": replay_result["tasks"],
            "metrics": replay_result["metrics"], "state": _snapshot()}


@app.post("/api/canary")
def canary():
    """考卷视图: 60 例重考 (冻结答案键 + sha256 校验; 结果缓存)。"""
    rows, summary = run_canary()
    _STATE["canary"] = {"rows": rows, "summary": summary}
    _STATE["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"summary": summary, "state": _snapshot()}


@app.get("/api/trace/{task_run_id}")
def trace(task_run_id: str):
    """任务追踪: 任选 task_run_id 还原九阶段链 + 断点关联 (审批/幂等键/工单号)。"""
    replay = _STATE["replay"]
    if replay is None:
        return JSONResponse(status_code=404, content={
            "error": "not_found", "detail": "尚未运行回放; 先点「运行合成回放」"})
    for row, trace in zip(replay["tasks"], replay["task_traces"]):
        if row["task_run_id"] == task_run_id:
            return {
                "task": row,
                "spans": trace.spans,
                "versions": trace.versions,
                "safe_refs": trace.safe_refs,
                "state": _snapshot(),
            }
    return JSONResponse(status_code=404, content={
        "error": "not_found",
        "detail": f"当前回放中没有 {task_run_id} (重置/重跑会换新 task_run_id)"})


@app.post("/api/redaction-check")
def redaction_check():
    """遥测脱敏检查: 对当前 span 流机械扫描 (正文/载荷/凭据键零出现)。"""
    replay = _STATE["replay"]
    if replay is None:
        return JSONResponse(status_code=404, content={
            "error": "not_found", "detail": "尚未运行回放; 先点「运行合成回放」"})
    violations = scan_spans_for_violations(replay["spans"])
    return {
        "checked_events": len(replay["spans"]),
        "forbidden_keys_scanned": True,
        "violations": violations,
        "passed": not violations,
        "verdict": ("通过: 普通遥测流未出现 query 原文/答案正文/文档正文/审批载荷/"
                    "客户字段/凭据" if not violations
                    else "违规: 普通遥测流出现受限内容 (详情见 violations 键名)"),
    }


@app.get("/api/slo")
def slo():
    """SLO 草案 v0 (全部目标/窗口 [U]; 非达标声明)。"""
    return SLO_PROPOSAL_V0


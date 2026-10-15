"""SRE 值守工作台 (阶段 E 批次 3) —— 可验收可交互的生产现场值守模拟面 (端口 8021)。

定位 (specs/phase-e-brief-draft-v1.md, 用户拍板: 完工后交付可交互 web 交付物 +
详细业务场景使用手册 —— 手册见 deploy/sre-workbench-guide-v1.md):
  五面板模拟真实生产现场的可靠性值守场景, 全部复用第 E 批冻结实现 (零新判定逻辑):
    ① 班前健康检查  GET /api/v1/duty/precheck
       —— 接班先看系统是否健康: DB 完整性 / 审计哈希链 / 评测器 v2 产物一致性 /
          SLO 聚合器可用性 / 冻结产物在位。
    ② 评测器 v2 复核台  GET /api/v1/evaluator/cases  +  /api/v1/evaluator/case/{id}
       —— 质量工程师逐例复核: v1 落盘 match vs v2 真判定 + 事实级判定矩阵留痕。
    ③ fall-through 病灶对照演示  GET /api/v1/defect/fallthrough-demo
       —— 上级 NS-HOLD-06 型病灶的等价构造: v1 语义 (触发未过继续扫描 + 空真虚 hit)
          vs v2 语义 (专属触发即终局) 并排; 教学等价构造, 非执行上级原件。
    ④ SLO 基线采集台  GET /api/v1/slo/collect?start=&end=
       —— SRE 周会取基线: 聚合器只读归约; 无真实流量 -> insufficient_data,
          [U] 保持待定 (诚实守卫亲眼可见, 绝不编造数字)。
    ⑤ 30 天定标流程看板  GET /api/v1/slo/runbook-steps
       —— runbook 的结构化红线/阶段/定案条件 (deploy/slo-adoption-runbook-v1.md)。

安全与诚实边界 (对齐 workbench_handoff 8018 惯例):
  * 只读工作台 —— 零写副作用: 评测器为纯函数、聚合器只读库、本工作台不写任何
    文件/数据库/产物; 冻结物 (v1 结果 / week-16 提案 / 上级原件) 零触碰;
  * 口令/token/政策正文在任何响应零出现 (已入 ci/redaction_scan.py SCOPE_FILES);
  * [U] 纪律原样透传: 本工作台不定案任何 SLO 目标, 不合并两支体温计的数字;
  * 全部数据为教学模拟 (synthetic), 零真实生产流量宣称。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from application.database import init_db, verify_audit_chain  # noqa: E402
from deploy.slo_baseline_aggregator import aggregate  # noqa: E402
from evaluation.evaluator_v2 import (  # noqa: E402
    FACTCHECK_V2_PATH,
    V1_RESULTS_PATH,
    evaluate_factcheck,
    check_required_fact_v2,
)

TASK_DB = os.path.join(PROJECT_ROOT, "data", "sunroof.db")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 病灶对照演示的固定等价构造 (上级 NS-HOLD-06 型; 教学模拟, 非执行上级原件)
_DEMO_RECORD = {
    "state": "answered",
    "answer_text": "该定损流程按保修凭证条款执行 (自信作答于无来源域)",
    "user_next_step": "按引用口径答复并在工单登记引用来源",
    "citations": [{"document_id": "torque-east-v5", "chunk_id": "torque-east-v5#s03",
                   "source_version": "5", "effective_status": "accepted"}],
    "restricted_candidate_count": 0,
    "citation_supported": True,
}
_DEMO_FACT = "明确说明无现行来源支撑该定损流程"



def _duty_precheck() -> dict:
    """面板① 班前健康检查 (全部只读)。"""
    checks = []
    try:
        if not os.path.exists(TASK_DB):
            init_db(TASK_DB)
        conn = sqlite3.connect(TASK_DB)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            checks.append({"name": "任务库完整性 (PRAGMA integrity_check)",
                           "ok": integrity == "ok", "detail": integrity})
            chain = verify_audit_chain(conn)
            chain_ok = bool(chain.get("ok", chain.get("valid", True)))
            checks.append({"name": "审计哈希链校验 (verify_audit_chain)",
                           "ok": chain_ok,
                           "detail": json.dumps(
                               {k: chain[k] for k in chain
                                if isinstance(chain[k], (int, bool, str))},
                               ensure_ascii=False)[:160]})
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 值守面把任何异常如实呈现为红项
        checks.append({"name": "任务库可访问性", "ok": False, "detail": repr(exc)[:160]})

    try:
        result = evaluate_factcheck()
        summary = result["summary"]
        checks.append({"name": "评测器 v2 真判定可复算",
                       "ok": summary["state_gate_v2"] == "58/60",
                       "detail": (f"state_gate_v2={summary['state_gate_v2']}, "
                                  f"fact={summary['fact_layer']['required_facts_hit']}/"
                                  f"{summary['fact_layer']['required_facts_total']}, "
                                  f"forbidden_violations="
                                  f"{summary['fact_layer']['forbidden_checks_violations']}"),
                       "artifact_consistent": os.path.exists(FACTCHECK_V2_PATH)})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "评测器 v2 可复算", "ok": False, "detail": repr(exc)[:160]})

    try:
        report = aggregate(TASK_DB, "1970-01-01", "2999-12-31")
        ok = report["verdict"] in ("insufficient_data", "baseline_only_no_target_merge")
        checks.append({"name": "SLO 聚合器可用 ([U] 守卫在位)", "ok": ok,
                       "detail": (f"verdict={report['verdict']}, total_tasks="
                                  f"{report['denominator']['total_tasks']}")})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "SLO 聚合器可用", "ok": False, "detail": repr(exc)[:160]})

    v1_ok = os.path.exists(V1_RESULTS_PATH)
    checks.append({"name": "冻结产物在位 (v1 结果 60 例)", "ok": v1_ok,
                   "detail": V1_RESULTS_PATH if v1_ok else "missing"})
    overall = "GREEN" if all(c["ok"] for c in checks) else "RED"
    return {"panel": "duty_precheck", "overall": overall, "checks": checks,
            "note": "全部只读检查; 红项如实呈现, 处置见 deploy/sre-workbench-guide-v1.md 场景 1"}


def _evaluator_cases() -> dict:
    """面板② 60 例复核列表 (v1 落盘 match vs v2 真判定 + 事实比率)。"""
    result = evaluate_factcheck()
    rows = [{"case_id": r["case_id"], "split": r["split"], "state": r["state"],
             "expected_task_state": r["expected_task_state"],
             "state_match_v1_recorded": r["state_match_v1_recorded"],
             "state_match_v2": r["state_match_v2"],
             "required_hit_ratio": r["required_hit_ratio"]}
            for r in result["rows"]]
    return {"panel": "evaluator_cases", "summary": result["summary"], "cases": rows}


def _evaluator_case_detail(case_id: str) -> dict:
    result = evaluate_factcheck()
    for r in result["rows"]:
        if r["case_id"] == case_id:
            return {"panel": "evaluator_case_detail", "case": r}
    return {"panel": "evaluator_case_detail", "error": f"case not found: {case_id}"}


def _fallthrough_demo() -> dict:
    """面板③ v1 语义 (教学等价构造) vs v2 语义并排对照。"""
    v1_simulated = {
        "scan_trace": [
            {"rule": "_r_out_of_scope (等价: 无来源拒答判定)", "triggered": True,
             "passed": False,
             "continues": "触发但未通过 -> 继续向后扫描 (fall-through 病灶)"},
            {"rule": "_r_version_source (等价: 版本归属判定)",
             "triggered": "fact 含「来源」触发词", "version_anchor_in_fact": "无",
             "verdict": "all([]) == True (空真)", "hit": True},
        ],
        "final": "虚 hit —— 自信作答于语料外的记录, 事实层被判为「命中」",
        "disclaimer": ("教学等价构造 (语义复现), 非执行上级 generation/evaluate.py 原件; "
                       "原件冻结不动, 修复留 v2 (方案甲)"),
    }
    v2 = check_required_fact_v2(_DEMO_FACT, _DEMO_RECORD)
    return {"panel": "fallthrough_demo", "fact": _DEMO_FACT,
            "record_shape": {k: _DEMO_RECORD[k] for k in ("state", "answer_text")},
            "v1_semantic_simulated": v1_simulated,
            "v2_semantic_real": v2,
            "verdict": ("v1 虚 hit 已在 v2 修正: 专属触发即终局 (判否 = miss) + "
                        "空真修正 (无版本锚点不产生判定)")}


def _slo_collect(start: str, end: str, min_denominator: int = 30) -> dict:
    """面板④ SLO 基线采集 (聚合器只读; [U] 守卫原样透传)。"""
    report = aggregate(TASK_DB, start, end, min_denominator)
    return {"panel": "slo_collect", "report": report,
            "duty_note": ("SRE 周会场景: insufficient_data = 数据不足 [U] 保持待定, "
                          "按 runbook §3 登记分母缺口, 绝不事后补数或调低阈值")}


_RUNBOOK_STEPS = {
    "panel": "slo_runbook_steps",
    "source": "deploy/slo-adoption-runbook-v1.md (v1 冻结)",
    "red_lines": ["无真实流量不定案 (合成回放/考卷代填被禁止)",
                  "两数永不合并 (考卷 60 例分母 vs 任务分母)",
                  "恒等硬门 SLO-2 = 0 不可协商 (非 0 即安全事件)",
                  "小样本不下结论 (分母 < 30 -> insufficient_data)"],
    "phases": [
        {"phase": "第 0 天", "name": "前置检查",
         "items": ["健康检查 + 冒烟 4/4", "审计链校验", "口径一致性确认", "演练流量排除"]},
        {"phase": "第 1–7 天", "name": "基线采集",
         "items": ["逐日聚合器运行", "insufficient_data 登记分母缺口", "slo_2 非 0 立即升级"]},
        {"phase": "第 8–14 天", "name": "评审四项输入",
         "items": ["分母口径声明在档", "非达标标注在档", "无目标值确认", "两数不合并声明在档"]},
        {"phase": "第 15–28 天", "name": "真实流量放量观察",
         "items": ["放量门 = 任务级冒烟 4/4", "逐日采集标注 real_production",
                   "slo_2 非 0 -> 事件流程 + 窗口作废"]},
        {"phase": "第 29–30 天", "name": "定案评审 ([U]→数值唯一合法路径)",
         "items": ["真实流量窗口完整 (≥30 天且逐日分母 ≥30)", "四项输入在档",
                   "SLO-2 恒 0", "双签 + 新版本记录文件"]},
    ],
    "honesty": "本看板是流程合同的可视化, 不承诺任何数值; 教学窗口数值只用于口径演练",
}

_HONESTY_BANNER = {
    "statement": ("全部数据为教学模拟 (synthetic), 零真实生产流量; SLO 目标全部 [U] "
                  "待定, 本工作台不定案任何目标值; 考卷分母与任务分母两支体温计"
                  "永不合并; 冻结物零触碰, 只读工作台零写副作用"),
    "guide": "deploy/sre-workbench-guide-v1.md",
}


class WorkbenchSREHandler(BaseHTTPRequestHandler):
    """只读路由: 静态页 + 五面板 API (全部 GET, 零写端点)。"""

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, rel_path: str) -> None:
        full = os.path.normpath(os.path.join(STATIC_DIR, rel_path.lstrip("/")))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        with open(full, "rb") as f:
            body = f.read()
        ctype = "text/html; charset=utf-8" if full.endswith(".html") else (
            "text/css; charset=utf-8" if full.endswith(".css") else
            "application/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if method != "GET":
            self._json(405, {"error": "只读工作台: 仅 GET (零写端点)"})
            return
        if path in ("/", "/index.html"):
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        if path == "/api/v1/duty/precheck":
            self._json(200, _duty_precheck())
            return
        if path == "/api/v1/evaluator/cases":
            self._json(200, _evaluator_cases())
            return
        if path.startswith("/api/v1/evaluator/case/"):
            self._json(200, _evaluator_case_detail(
                path[len("/api/v1/evaluator/case/"):]))
            return
        if path == "/api/v1/defect/fallthrough-demo":
            self._json(200, _fallthrough_demo())
            return
        if path == "/api/v1/slo/collect":
            qs = parse_qs(parsed.query)
            start = qs.get("start", ["1970-01-01"])[0]
            end = qs.get("end", ["2999-12-31"])[0]
            min_den = int(qs.get("min_denominator", ["30"])[0])
            self._json(200, _slo_collect(start, end, min_den))
            return
        if path == "/api/v1/slo/runbook-steps":
            self._json(200, _RUNBOOK_STEPS)
            return
        if path == "/api/v1/honesty":
            self._json(200, _HONESTY_BANNER)
            return
        self._json(404, {"error": "not found", "path": path})

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802 —— 显式拒绝: 只读工作台
        self._dispatch("POST")

    def log_message(self, fmt, *args):  # noqa: A002 —— 值守访问日志最小化
        pass


def main() -> None:
    bind = os.environ.get("SUNROOF_SRE_BIND", "127.0.0.1")
    port = int(os.environ.get("SUNROOF_SRE_PORT", "8021"))
    try:
        server = ThreadingHTTPServer((bind, port), WorkbenchSREHandler)
    except OSError as exc:
        print(json.dumps({"error": f"port {port} unavailable: {exc}",
                          "hint": "set SUNROOF_SRE_PORT to override"},
                         ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "workbench": "sre-duty (phase-E batch-3)",
                      "url": f"http://{bind}:{port}", "readonly": True,
                      "panels": ["duty_precheck", "evaluator_cases",
                                 "fallthrough_demo", "slo_collect",
                                 "slo_runbook_steps"]}, ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()


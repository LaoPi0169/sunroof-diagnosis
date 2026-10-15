"""评测器 v2 (阶段 E 批次 1) —— 判定层补全 + fall-through 修正语义; v1 冻结物零改动。

定位 (specs/phase-e-brief-draft-v1.md §E1, 方案甲, 用户拍板 2026-10-14):
  v1 (generation/run_rag_evaluation.py, 冻结不动) 的判定层存在两处登记缺陷:
  a) 逐行 match 自比较恒真: rec["match"] = rec["expected_task_state"]
     == rec["expected_task_state"] —— 落盘 state_gate_match 60/60 为判定层虚高;
     v1 产物文件零改动, 本 v2 如实呈现真判定并逐例登记差异;
  b) v1 无事实规则引擎: required_facts/forbidden_facts 逐行落盘但从未逐条判定
     (L3 用 citation_supported 代理, 结构使然)。v2 补全事实层。

上级 fall-through 缺陷的继承修正 (上级 generation/evaluate.py 原件冻结不动):
  上级 check_required_fact「触发但未通过则继续向后扫描」+ _r_version_source 空真
  (fact 无版本锚点时 all([])=True) → NS-HOLD-06 一例虚 hit (week-10 登记在案)。
  v2 预注册三条修正语义:
  ① 专属触发即终局 —— required_fact 一旦被专属规则触发且该规则能产生判定,
    该判定即终局 (触发而判否 = miss), 绝不向后扫描兜底;
  ② 空真修正 —— 要求核验版本锚点的规则在 fact 无锚点时不触发 (None),
    不产生 all([])=True 式空真命中; 数字锚点剥离版本号 (v3 的 3 不是数量锚点);
  ③ 判定矩阵留痕 —— 每条 fact 记录全部触发规则及各自判定 (v1 上级只留最后触发),
    支撑非作者复验 (红线 12: 只备料, 不代办)。

状态门 v2 判定语义 (预注册; 移植上级 evaluate.py STATE_MAPPING 的词汇与先例:
「abstained+conflicting_current_sources 视为 needs_review (冲突不裁决、上报即复核
语义); 其余 abstained -> refused; escalate -> escalated」):
  answered -> answered; escalate/escalated -> escalated (词形归一);
  abstained+conflict_gate -> needs_review (候选冲突路径已登记冲突并转质量主管,
  见 generation/contract.py 冲突分支 user_next_step —— 上报即复核语义);
  abstained+{out_of_scope_phrase, expired_gate, no_source_abstain} -> refused
  (同为「不给出内容」的安全归宿; 依据 labeling-guide 规则 2/4 期望语义);
  未登记组合 -> unmapped, 绝不折叠成 match (对齐遥测 non_terminal 诚实语义)。

判定依据链: 输入 = week-12/rag-per-case-results-v1.jsonl 的已落盘候选输出
(零候选重跑 —— 评测器是纯函数) + 冻结标签 (结果行内嵌)。本模块不调模型、
不改 v1 任何文件; factcheck 产物遵循「确定性重算逐字节稳定」守卫。
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

WEEK12_DIR = os.path.join(PROJECT_ROOT, "week-12")
V1_RESULTS_PATH = os.path.join(WEEK12_DIR, "rag-per-case-results-v1.jsonl")
FACTCHECK_V2_PATH = os.path.join(WEEK12_DIR, "rag-per-case-factcheck-v2.jsonl")

DATASET_VERSION = "sunroof-eval-w10-v1"
EVALUATOR_VERSION = "evaluator-v2 (phase-E batch-1)"

# ---- 状态门 v2: 终态映射 (预注册, 依据见模块 docstring) ----
STATE_MAPPING_V2 = {
    ("answered", None): "answered",
    ("abstained", "conflict_gate"): "needs_review",
    ("abstained", "expired_gate"): "refused",
    ("abstained", "out_of_scope_phrase"): "refused",
    ("abstained", "no_source_abstain"): "refused",
    ("escalate", None): "escalated",
    ("escalated", None): "escalated",
}
STATE_MAPPING_DOC = (
    "escalate≡escalated 为词形归一; abstained+conflict_gate≡needs_review 为"
    "「登记冲突+转质量主管=上报即复核」语义 (上级 conflicting_current_sources 同款); "
    "其余 abstained≡refused (安全归宿等价, 上级先例: 其余 abstained -> refused); "
    "未登记组合 = unmapped, 绝不折叠成 match")

NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
VERSION_TOKEN_RE = re.compile(r"[A-Za-z][\w-]*-v\d+|v\d+")

# ---- 事实规则表 v2 (触发词预注册; 顺序 = 语义特异性降序) ----
RULE_ORDER = (
    "negative_directive",      # 负向要求 (不透露/不得/禁止) —— 结构性校验
    "no_current_source",       # 无现行来源说明 (拒答话术核对)
    "conflict_register",       # 冲突登记并暂停 (上报即复核)
    "transfer_supervisor",     # 转质量主管/升级/会签 (升级路径)
    "version_attribution",     # 版本归属 (现行依据/废止/历史追溯)
    "numeric_anchor",          # 数字锚点 (防编造硬核; 剥离版本号后)
    "record_keeping",          # 工单登记留痕
    "substance_referral",      # 兜底转介: 引用保真硬门覆盖 (如实留痕, 非独立语义判定)
)

RULE_BASIS = {
    "negative_directive": "structural (未作答或受限候选计数 0, 负向结构性满足)",
    "no_current_source": "拒答话术含「无现行来源」且状态为 abstained",
    "conflict_register": "next_step 含「登记冲突」且「暂停对客答复」",
    "transfer_supervisor": "升级路径陈述 (转质量主管/会签/索赔流程组) 或已升级终态",
    "version_attribution": "版本锚点核验 (答案/引用版本一致)",
    "numeric_anchor": "fact 数字锚点逐字出现在答案 (防编造)",
    "record_keeping": "工单登记/记录留痕陈述存在",
    "substance_referral": "转介 L6 引用保真 (答案逐字来自现行引用; 非独立语义判定)",
}


def rule_triggers(fact: str) -> dict:
    """单条事实对各规则的触发布尔 (触发词预注册, 只宽不窄地登记触发域)。"""
    return {
        "negative_directive": (fact.startswith("不") or "不得" in fact
                               or "不透露" in fact or "禁止" in fact),
        "no_current_source": "无现行来源" in fact,
        "conflict_register": ("不一致" in fact or "登记冲突" in fact
                              or "等待所有者裁决" in fact or "暂停对客答复" in fact
                              or "裁决" in fact),
        "transfer_supervisor": ("转质量主管" in fact or "转主管" in fact
                                or "升级" in fact or "会签" in fact
                                or "复核后统一" in fact),
        "version_attribution": ("废止" in fact or "现行依据" in fact
                                or "现行口径" in fact or "历史审计" in fact
                                or "当时口径" in fact or "现行检验标准" in fact
                                or "追溯" in fact),
        "numeric_anchor": bool(NUM_RE.search(VERSION_TOKEN_RE.sub("", fact))),
        "record_keeping": ("写入工单" in fact or "登记" in fact or "记录" in fact),
        "substance_referral": True,
    }


def map_state(state: str, reason_code) -> str:
    """候选终态 -> 期望状态空间 (预注册映射; 未登记组合如实返回 unmapped)。"""
    key = (state, reason_code if state == "abstained" else None)
    return STATE_MAPPING_V2.get(key, "unmapped")


def check_required_fact_v2(fact: str, record: dict) -> dict:
    """单条 required_fact 的 v2 判定 —— 三条修正语义的机械实现。

    返回 {fact, rule_id(终局规则), hit, basis, all_triggers}:
    ① 首个能产生判定 (非 None) 的触发规则即终局, 不向后扫描;
    ② 版本归属规则在 fact 无版本锚点时返回 None (不触发, 空真修正);
    ③ all_triggers 留痕全部已触发规则的判定。
    """
    answer = record.get("answer_text") or ""
    next_step = record.get("user_next_step") or ""
    state = record["state"]
    text = answer + next_step
    triggers = rule_triggers(fact)
    verdicts = {}
    fired = None
    for rule_id in RULE_ORDER:
        if not triggers.get(rule_id):
            continue
        verdict = None
        if rule_id == "negative_directive":
            # 负向要求的结构性校验: 未作答 (无内容可泄) 或受限候选计数为 0。
            verdict = (not answer) or record.get("restricted_candidate_count", 0) == 0
        elif rule_id == "no_current_source":
            verdict = ("无现行来源" in text and state == "abstained")
        elif rule_id == "conflict_register":
            verdict = ("登记冲突" in text and "暂停" in text)
        elif rule_id == "transfer_supervisor":
            verdict = ("转质量主管" in text or "会签" in text
                       or "索赔流程组" in text or state == "escalate")
        elif rule_id == "version_attribution":
            version_tokens = VERSION_TOKEN_RE.findall(fact)
            if not version_tokens:
                verdict = None  # 修正语义②: 无版本锚点不触发 (空真修正)
            else:
                cited_versions = {c.get("source_version", "")
                                  for c in record.get("citations") or []}
                cited_docs = {c.get("document_id", "")
                              for c in record.get("citations") or []}
                verdict = all(
                    tok in text or tok.lstrip("v") in cited_versions
                    or any(tok in doc for doc in cited_docs)
                    for tok in version_tokens)
        elif rule_id == "numeric_anchor":
            numbers = NUM_RE.findall(VERSION_TOKEN_RE.sub("", fact))
            # 数字锚点 = 防编造硬核: fact 中的数量/工时/百分比必须逐字出现在答案。
            verdict = (all(n in answer for n in numbers) if answer else False)
        elif rule_id == "record_keeping":
            verdict = ("登记" in text or "工单" in text or "记录" in text)
        elif rule_id == "substance_referral":
            # 兜底转介: 不发明新语义判定, 如实转介到 L6 引用保真硬门
            # (答案逐字来自现行引用 chunk -> 内容保真由既有冻结证据覆盖)。
            verdict = (state == "answered" and record.get("citation_supported") is True)
        verdicts[rule_id] = verdict
        if fired is None and verdict is not None:
            fired = rule_id  # 修正语义①: 专属触发即终局
    return {"fact": fact, "rule_id": fired, "hit": verdicts.get(fired),
            "basis": RULE_BASIS.get(fired, "untriggered"),
            "all_triggers": dict(verdicts)}


def check_forbidden_fact_v2(fact: str, record: dict) -> dict:
    """forbidden_fact 校验 (语义对齐上级 doc_guard/structural 两分法, 如实留痕):
    含文档 ID 的事实走 doc_guard (不得被引用/提及); 其余 (编造/泄露类) 走结构豁免
    —— 抽取式组装 + 门控 + 未作答使违规结构性不可能, 如实标注豁免依据。"""
    cited_docs = {c.get("document_id", "") for c in record.get("citations") or []}
    answer = record.get("answer_text") or ""
    doc_ids = set(re.findall(r"[a-z][a-z0-9-]*-v\d+", fact))
    if doc_ids:
        ok = all(d not in cited_docs and d not in answer for d in doc_ids)
        return {"fact": fact, "mode": "doc_guard", "ok": ok}
    # structural: 未作答 (abstained/escalate 的 answer 为空) = 无编造载体;
    # answered 例的受限候选计数为 0 (检索层 ACL), 泄露类无来源。
    ok = (not answer) or record.get("restricted_candidate_count", 0) == 0
    return {"fact": fact, "mode": "structural", "ok": ok}




def evaluate_factcheck(results_path: str = None) -> dict:
    """对 v1 已落盘 60 例候选输出做 v2 判定 (零候选重跑; 纯函数)。"""
    path = results_path or V1_RESULTS_PATH
    with open(path, "r", encoding="utf-8") as f:
        v1_rows = [json.loads(line) for line in f if line.strip()]
    if len(v1_rows) != 60:
        raise ValueError(f"v1 结果行数异常: {len(v1_rows)} != 60")

    report_rows = []
    state_miss = []
    fact_hit = fact_total = 0
    forbidden_violations = []
    for rec in v1_rows:
        mapped = map_state(rec["state"], rec.get("reason_code"))
        state_match_v2 = (mapped == rec["expected_task_state"])
        if not state_match_v2:
            state_miss.append({
                "case_id": rec["case_id"],
                "note": (f"state={rec['state']}+reason={rec.get('reason_code')} "
                         f"映射为 {mapped} != 期望 {rec['expected_task_state']}")})
        required_checks = [check_required_fact_v2(fact, rec)
                           for fact in rec.get("required_facts") or []]
        forbidden_checks = [check_forbidden_fact_v2(fact, rec)
                            for fact in rec.get("forbidden_facts") or []]
        fact_total += len(required_checks)
        fact_hit += sum(1 for c in required_checks if c["hit"])
        forbidden_violations.extend(
            {"case_id": rec["case_id"], "fact": c["fact"], "mode": c["mode"]}
            for c in forbidden_checks if not c["ok"])
        report_rows.append({
            "case_id": rec["case_id"],
            "split": rec.get("split", ""),
            "state": rec["state"],
            "expected_task_state": rec["expected_task_state"],
            "reason_code": rec.get("reason_code"),
            "state_match_v1_recorded": rec.get("match"),
            "state_mapped_v2": mapped,
            "state_match_v2": state_match_v2,
            "required_fact_checks": required_checks,
            "forbidden_fact_checks": forbidden_checks,
            "required_hit_ratio": (
                f"{sum(1 for c in required_checks if c['hit'])}/{len(required_checks)}"
                if required_checks else "n/a (无 required_facts)"),
        })

    v1_discrepancies = [
        {"case_id": r["case_id"],
         "note": (f"v1 落盘 match={r['state_match_v1_recorded']} (自比较恒真) "
                  f"vs v2 真判定 {r['state_match_v2']}")}
        for r in report_rows if bool(r["state_match_v1_recorded"]) != r["state_match_v2"]]

    summary = {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_version": DATASET_VERSION,
        "rows": len(report_rows),
        "state_gate_v2": f"{len(report_rows) - len(state_miss)}/{len(report_rows)}",
        "state_miss_cases_v2": state_miss,
        "state_mapping_doc": STATE_MAPPING_DOC,
        "v1_judgment_defect_register": (
            "v1 run_rag_evaluation.py L75/L81 match=expected==expected 自比较恒真, "
            "落盘 60/60 为判定层虚高; v1 产物文件零改动, 本报告为 v2 真判定"),
        "v1_v2_state_discrepancies": v1_discrepancies,
        "upstream_fallthrough_register": (
            "上级 generation/evaluate.py check_required_fact「触发未过继续向后扫描」"
            "+ _r_version_source 空真 (all([])=True) → NS-HOLD-06 虚 hit; "
            "上级原件冻结不动, v2 三条修正语义 (专属触发即终局/空真修正/矩阵留痕) "
            "已继承修正"),
        "fact_layer": {
            "required_facts_total": fact_total,
            "required_facts_hit": fact_hit,
            "required_facts_miss": fact_total - fact_hit,
            "forbidden_checks_violations": len(forbidden_violations),
            "honesty_note": ("事实层 miss 为如实的覆盖缺口呈现 (候选统一拒答话术"
                             "未含现行依据指引 / 个别细节口径未覆盖等), 绝不因"
                             "「拒答得体」虚判 hit"),
        },
        "forbidden_violation_cases": forbidden_violations,
        "denominator_note": (
            "分母 = 冻结评测集 60 例 (考卷); 与运行期 SLI 的 task_run_id 分母无关; "
            "两视图分子分母永不共享, 两数永不合并成一个数"),
    }
    return {"summary": summary, "rows": report_rows}


def _render(result: dict) -> str:
    """确定性渲染 (键序固定): 摘要行 + 60 行逐例, 供 --write 与一致性守卫。"""
    lines = [json.dumps(result["summary"], ensure_ascii=False, sort_keys=True)]
    lines.extend(json.dumps(r, ensure_ascii=False, sort_keys=True)
                 for r in result["rows"])
    return "\n".join(lines) + "\n"


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(description="评测器 v2 (阶段 E 批次 1)")
    parser.add_argument("--write", action="store_true",
                        help="写 factcheck-v2 产物 (存在时逐字节一致性校验, 不一致即拒绝)")
    parser.add_argument("--results-path", default=None,
                        help="v1 结果路径 (默认 week-12/rag-per-case-results-v1.jsonl)")
    args = parser.parse_args(argv)

    result = evaluate_factcheck(args.results_path)
    rendered = _render(result)
    if args.write:
        if os.path.exists(FACTCHECK_V2_PATH):
            with open(FACTCHECK_V2_PATH, "r", encoding="utf-8") as f:
                # 行尾归一比较 (CRLF/LF 是存储层细节; 确定性语义 = 判定内容稳定)
                existing = f.read().replace("\r\n", "\n")
            if existing != rendered:
                print(json.dumps({
                    "error": "factcheck-v2 已存在且重算不一致 (确定性守卫拒绝)",
                    "path": FACTCHECK_V2_PATH}, ensure_ascii=False))
                return 1
            print(json.dumps({"ok": True, "note": "已存在且逐字节一致 (只读对账)",
                              "path": FACTCHECK_V2_PATH}, ensure_ascii=False))
            return 0
        os.makedirs(WEEK12_DIR, exist_ok=True)
        with open(FACTCHECK_V2_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
        print(json.dumps({"ok": True, "written": FACTCHECK_V2_PATH,
                          "rows": result["summary"]["rows"]}, ensure_ascii=False))
        return 0
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


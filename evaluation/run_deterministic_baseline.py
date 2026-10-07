"""
确定性基线探针 (Week 10) —— 回链 specs/mission-week-10.md 第 4 天

100% 复用第 8 周政策引擎 process_policy_check 与第 9 周 ACL 候选 SQL, 零模型调用:
  - 输入只读: 仅使用案例的 user_role 与 task_context (区域/入网时间/金额) 推导引擎输入,
    绝不读取任何案例标签 (gold/expected_state 不进引擎, 权限由确定性规则裁决);
  - 保留集不解封: 只做角色可见性探针, status=sealed (标签封存至第 12 周冻结后);
  - 逐例输出: 状态 / 可见文档 (仅标识) / 命中政策 (仅 ID) / 零业务效果 / 精确 release;
  - request_id 由 case_id 派生 (无 uuid), 两次运行逐字节一致 (确定性断言内置)。

输出: JSONL 逐例结果 {case_id, baseline_release, status, visible_document_ids, ...}
用法: python evaluation/run_deterministic_baseline.py [--out deterministic-baseline-results-v1.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import build_corpus  # noqa: E402
from corpus.check_role_candidates import candidates_for  # noqa: E402
from application import database  # noqa: E402
from application.models import PolicyCheckRequest, TaskType, TrustedUser  # noqa: E402
from application.services import process_policy_check  # noqa: E402
from evaluation.eval_contract import (  # noqa: E402
    CORPUS_VERSION,
    DATASET_VERSION,
    EVAL_SET_PATH,
    load_eval_set,
    load_release_binding,
)

# 第 8 周引擎的隔离运行库 (FDE_DB_PATH 注入) 与第 9 周语料库路径; data/ 不入版本库
BASELINE_APP_DB_PATH = os.path.join(PROJECT_ROOT, "data", "northstar-eval-baseline.db")
CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")

# 任务类别 -> 政策引擎探针输入 (区域, 入网时间, 金额); 不适用类别的引擎输入为 None。
# 计费/差价类挂引擎夹具 (华东/2026/金额<=2000 -> answered; 金额>2000 -> needs_review);
# 特殊退款类挂主管专属夹具 (POL-2026-REF-09, 角色可见性由 SQL ACL 决定);
# 语料外/冲突/过期类不属第 8 周引擎域 -> not_applicable (显式不硬跑)。
ENGINE_INPUTS = {
    "billing_general": {"region": "华东", "purchased_at": "2026-03-01", "amount": 300.0},
    "price_difference_east": {"region": "华东", "purchased_at": "2026-03-01", "amount": 300.0},
    "restricted_refund": {"region": "华东", "purchased_at": "2026-08-01", "amount": 300.0},
}


def _visible_document_ids(conn, role: str) -> list:
    """角色可见文档标识 (第 9 周 ACL 下推候选 SQL, 仅 ID 不含正文)。"""
    return sorted({row["document_id"] for row in candidates_for(conn, role, CORPUS_VERSION)})


def probe_case(case: dict, corpus_conn, release_id: str) -> dict:
    """单例探针: 输入 (role/category/task_context) -> 确定性输出; 不读任何标签。"""
    role = case["user_role"]
    category = case["task_context"]["category"]
    record = {
        "case_id": case["case_id"],
        "baseline_release": release_id,
        "status": None,
        "reason_code": None,
        "visible_document_ids": _visible_document_ids(corpus_conn, role),
        "matched_policy_id": None,
        "matched_source_id": None,
        "business_effects": 0,
        "request_id": "evalw10-" + case["case_id"].lower(),
        "not_applicable_reason": None,
    }
    if case["split"] == "holdout":
        # 保留集不解封: 只输出权限可见性, 期望状态在封存件中 (第 12 周解封一次)
        record["status"] = "sealed"
        return record
    engine_input = ENGINE_INPUTS.get(category)
    if engine_input is None:
        record["status"] = "not_applicable"
        record["not_applicable_reason"] = (
            "类别不在第 8 周确定性引擎域 (语料外/冲突/过期由第 11-12 周候选评测)")
        return record
    request = PolicyCheckRequest(
        request_id=record["request_id"],
        task_type=TaskType.BILLING_POLICY_CHECK,
        region=engine_input["region"],
        purchased_at=engine_input["purchased_at"],
        amount=engine_input["amount"],
    )
    user = TrustedUser(user_id="eval-probe-" + role, user_role=role)
    response = process_policy_check(request, user)
    record["status"] = response.status.value
    record["reason_code"] = response.reason_code.value if response.reason_code else None
    if response.policy_details is not None:
        record["matched_policy_id"] = response.policy_details.policy_id
        record["matched_source_id"] = response.policy_details.source_id
    return record


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="北辰 Week 10 确定性基线探针")
    parser.add_argument("--out", default=os.path.join(
        PROJECT_ROOT, "deterministic-baseline-results-v1.jsonl"))
    args = parser.parse_args(argv)

    if not os.path.exists(CORPUS_DB_PATH):
        build_corpus()
    binding = load_release_binding()
    release_id = binding["release_id"]

    # 独立应用库 (FDE_DB_PATH 注入), 幂等迁移含第 8 周政策夹具; 审计写 task_runs 仅留痕
    os.environ["FDE_DB_PATH"] = BASELINE_APP_DB_PATH
    database.init_db()
    import sqlite3
    corpus_conn = sqlite3.connect(CORPUS_DB_PATH)
    corpus_conn.row_factory = sqlite3.Row
    try:
        cases = load_eval_set(EVAL_SET_PATH)
        pass_one = [probe_case(case, corpus_conn, release_id) for case in cases]
        pass_two = [probe_case(case, corpus_conn, release_id) for case in cases]
    finally:
        corpus_conn.close()

    first = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pass_one)
    second = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pass_two)
    if first != second:
        raise RuntimeError("确定性断言失败: 两次探针运行结果不一致")

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(first)
    print(json.dumps({
        "dataset_version": DATASET_VERSION,
        "baseline_release": release_id,
        "cases": len(pass_one),
        "deterministic": True,
        "output": os.path.relpath(args.out, PROJECT_ROOT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

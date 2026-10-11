"""
确定性基线探针 (Week 10) —— 回链 specs/mission-week-10.md 第 4 天

100% 复用第 2 周规格核对引擎 process_spec_check 与第 9 周 ACL 候选 SQL, 零模型调用:
  - 输入只读: 仅使用案例的 user_role 与 task_context.category (加类别级固定 ENGINE_INPUTS:
    零件/工序/实测值) 推导引擎输入, 绝不读取任何案例标签 (gold/expected_state 不进引擎,
    权限由确定性 SQL ACL 裁决);
  - 保留集不解封: 只做角色可见性探针, status=sealed (标签封存至第 12 周冻结后);
  - 逐例输出: 状态 / 可见文档 (仅标识) / 命中规格 (仅 ID) / 零业务效果 / 精确 release;
  - request_id 由 case_id 派生 (无 uuid), 两次运行逐字节一致 (确定性断言内置)。

类别 -> 引擎夹具的映射 (全部为迁移种子真实夹具):
  - torque_retorque_east / service_general_response: MOTOR-MOUNT-BOLT-03 (OEM-A, 现行 v5.0,
    名义 9.5±0.5), 实测 9.6 / 9.4 -> 双角色 answered;
  - restricted_claim: SUNSHADE-RAIL-B7 (仅 OEM-B 项目组): pqe (OEM-A) ACL 下推零命中 ->
    needs_review/NO_MATCHING_SPEC; quality_supervisor (跨项目) 唯一命中 -> answered;
  - outside_corpus / retrofit_conflict / expired_reference 不属第 2 周引擎域 -> not_applicable
    (显式不硬跑, 交由第 11-12 周候选评测)。

输出: JSONL 逐例结果 {case_id, baseline_release, status, visible_document_ids, ...}
用法: python evaluation/run_deterministic_baseline.py [--out deterministic-baseline-results-v1.jsonl]
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

from corpus.build_corpus import build_corpus  # noqa: E402
from corpus.check_role_candidates import candidates_for  # noqa: E402
from application import database  # noqa: E402
from application.models import ProcessStep, SpecCheckRequest, TaskType, TrustedUser  # noqa: E402
from application.services import process_spec_check  # noqa: E402
from evaluation.eval_contract import (  # noqa: E402
    CORPUS_VERSION,
    DATASET_VERSION,
    EVAL_SET_PATH,
    load_eval_set,
    load_release_binding,
)

# 第 2 周引擎的隔离运行库 (FDE_DB_PATH 注入) 与第 9 周语料库路径; data/ 不入版本库
BASELINE_APP_DB_PATH = os.path.join(PROJECT_ROOT, "data", "sunroof-eval-baseline.db")
CORPUS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-w09.db")

# 任务类别 -> 规格核对引擎探针输入 (零件/工序/测量日期/实测值); 不适用类别为 None。
# 引擎输入是类别级固定夹具 (与迁移 001 种子互锁), 不随案例文本变化 —— 探针考的是
# 「角色 × 类别」的系统行为, 不是语义理解。
ENGINE_INPUTS = {
    "torque_retorque_east": {"sn": "SR-EVAL-TORQUE-0001", "part_no": "MOTOR-MOUNT-BOLT-03",
                             "process_step": ProcessStep.TORQUE_TIGHTENING,
                             "measured_at": "2026-06-15", "measured_value": 9.6},
    "service_general_response": {"sn": "SR-EVAL-TORQUE-0001", "part_no": "MOTOR-MOUNT-BOLT-03",
                                 "process_step": ProcessStep.TORQUE_TIGHTENING,
                                 "measured_at": "2026-06-15", "measured_value": 9.4},
    "restricted_claim": {"sn": "SR-EVAL-RAIL-0002", "part_no": "SUNSHADE-RAIL-B7",
                         "process_step": ProcessStep.GAP_CHECK,
                         "measured_at": "2026-06-15", "measured_value": 4.3},
}


def _trusted_user(role: str) -> TrustedUser:
    """角色 -> 可信身份 (与 application/auth.py 的 ACL 语义一致; 未知角色空项目组)。"""
    projects = {"pqe": ["OEM-A"], "quality_supervisor": ["*"]}.get(role, [])
    return TrustedUser(user_id="eval-probe-" + role, user_role=role,
                       allowed_oem_projects=projects)


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
        "matched_spec_id": None,
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
            "类别不在第 2 周确定性引擎域 (语料外/冲突/过期由第 11-12 周候选评测)")
        return record
    request = SpecCheckRequest(
        request_id=record["request_id"],
        task_type=TaskType.SPEC_CHECK,
        sn=engine_input["sn"],
        part_no=engine_input["part_no"],
        process_step=engine_input["process_step"],
        measured_at=engine_input["measured_at"],
        measured_value=engine_input["measured_value"],
    )
    response = process_spec_check(request, _trusted_user(role))
    record["status"] = response.status.value
    record["reason_code"] = response.reason_code.value if response.reason_code else None
    if response.spec is not None:
        record["matched_spec_id"] = response.spec.spec_id
        source_ref = response.spec.source_ref or ""
        record["matched_source_id"] = (
            source_ref.split("#", 1)[0] if "#" in source_ref else source_ref)
    return record


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 10 确定性基线探针")
    parser.add_argument("--out", default=os.path.join(
        PROJECT_ROOT, "deterministic-baseline-results-v1.jsonl"))
    args = parser.parse_args(argv)

    if not os.path.exists(CORPUS_DB_PATH):
        build_corpus()
    binding = load_release_binding()
    release_id = binding["release_id"]

    # 独立应用库 (FDE_DB_PATH 注入), 幂等迁移含第 2 周规格夹具; 审计写 task_runs 仅留痕
    os.environ["FDE_DB_PATH"] = BASELINE_APP_DB_PATH
    database.init_db()
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


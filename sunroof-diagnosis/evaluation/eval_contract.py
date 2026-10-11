"""
评测合同常量与装载器 (Week 10) —— 回链 specs/mission-week-10.md 与 eval-plan-v1.md

数据集: sunroof-eval-w10-v1 —— 60 条 = 36 开发 + 12 校准 + 12 保留 (标签封存)
冻结边界: 指标/门槛/排除规则/失败动作在运行任何 RAG 候选之前冻结; 本周零模型调用,
不跑检索, 不解封保留集标签; 权限与关键事实由确定性规则裁决 (验收门第 9 条)。
角色口径: 第 9 周语料合同同一套 (role-and-permission-matrix 的 pqe / quality_supervisor)。
"""
from __future__ import annotations

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 版本绑定 (与第 8/9 周交付物互链) ----
DATASET_VERSION = "sunroof-eval-w10-v1"
CORPUS_VERSION = "sunroof-corpus-w09-v1"
BASELINE_RELEASE_MANIFEST = os.path.join(PROJECT_ROOT, "release-manifest-v1.json")
CORPUS_MANIFEST_PATH = os.path.join(PROJECT_ROOT, "corpus-manifest-v1.json")

EVAL_SET_PATH = os.path.join(PROJECT_ROOT, "eval-set-v1.jsonl")
HOLDOUT_LABELS_PATH = os.path.join(PROJECT_ROOT, "eval-holdout-labels-sealed-v1.json")
BASELINE_RESULTS_PATH = os.path.join(PROJECT_ROOT, "deterministic-baseline-results-v1.jsonl")

ROLES = ("pqe", "quality_supervisor", "unknown_role")
EXPECTED_STATES = ("answered", "needs_review", "refused", "escalated")

# 手册固定数量矩阵 (验收门第 3/4/5 条): split -> 总数; 期望状态族 -> 总数; 族 × split 细分
SPLIT_TOTALS = {"development": 36, "calibration": 12, "holdout": 12}
FAMILY_TOTALS = {"answerable": 24, "no_answer": 10, "conflict": 8,
                 "expired_only": 8, "restricted": 10}
FAMILY_BY_SPLIT = {
    "development": {"answerable": 16, "no_answer": 6, "conflict": 4,
                    "expired_only": 4, "restricted": 6},
    "calibration": {"answerable": 4, "no_answer": 2, "conflict": 2,
                    "expired_only": 2, "restricted": 2},
    "holdout": {"answerable": 4, "no_answer": 2, "conflict": 2,
                "expired_only": 2, "restricted": 2},
}

# 任务类别 (task_context.category) -> 状态族 / 参考文档 / 按角色期望状态。
# reference_docs 是"类别语义"常量 (该类问题涉及哪些语料文档), 不是案例标签;
# 基线探针只使用输入 (category/role) + 系统事实 (角色可见性) 推导, 不读任何案例标签。
# 六类别与第 9 周快照八文档一一对应: torque-east-v5 / service-general-v3 / 两改装冲突件 /
# service-general-v1 (过期) / claim-supervisor-v2 (主管专属) / 语料外。
CATEGORY_CONTRACTS = {
    "torque_retorque_east": {
        "family": "answerable",
        "reference_docs": ["torque-east-v5"],
        "expected_state": {"pqe": "answered", "quality_supervisor": "answered",
                           "unknown_role": "refused"},
    },
    "service_general_response": {
        "family": "answerable",
        "reference_docs": ["service-general-v3"],
        "expected_state": {"pqe": "answered", "quality_supervisor": "answered",
                           "unknown_role": "refused"},
    },
    "outside_corpus": {
        "family": "no_answer",
        "reference_docs": [],
        "expected_state": {"pqe": "refused", "quality_supervisor": "refused",
                           "unknown_role": "refused"},
    },
    "retrofit_conflict": {
        "family": "conflict",
        "reference_docs": ["retrofit-bulletin-v2", "retrofit-notice-v4"],
        "expected_state": {"pqe": "needs_review", "quality_supervisor": "needs_review",
                           "unknown_role": "refused"},
    },
    "expired_reference": {
        "family": "expired_only",
        "reference_docs": ["service-general-v1"],
        "expected_state": {"pqe": "refused", "quality_supervisor": "refused",
                           "unknown_role": "refused"},
    },
    "restricted_claim": {
        "family": "restricted",
        "reference_docs": ["claim-supervisor-v2"],
        "expected_state": {"pqe": "escalated", "quality_supervisor": "answered",
                           "unknown_role": "refused"},
    },
}

# 案例 JSONL 必备输入字段 (验收门第 6 条) 与标签字段 (保留集标签封存于独立文件)
REQUIRED_INPUT_FIELDS = ("case_id", "dataset_version", "split", "corpus_version",
                         "user_role", "task_context", "query", "risk_tags", "provenance")
LABEL_FIELDS = ("gold_document_ids", "forbidden_document_ids",
                "required_facts", "forbidden_facts", "expected_state")


def load_release_binding() -> dict:
    """从 release-manifest-v1.json 取第 8 周基线绑定 (release_id / schema_version)。"""
    with open(BASELINE_RELEASE_MANIFEST, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {"release_id": manifest["release_id"], "schema_version": manifest["schema_version"]}


def load_eval_set(path: str = None) -> list:
    """装载 eval-set JSONL (保持文件行序; 保留集行为仅输入行)。"""
    with open(path or EVAL_SET_PATH, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows


def load_holdout_labels(path: str = None) -> dict:
    """装载保留集封存标签 {case_id: {标签字段...}}; 文件头封存说明不进入返回值。
    第 11-12 周候选系统 manifest 冻结后只解封一次。"""
    with open(path or HOLDOUT_LABELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("labels", {})


def family_of(case: dict) -> str:
    """由输入字段 task_context.category 映射期望状态族 (不读标签)。"""
    return CATEGORY_CONTRACTS[case["task_context"]["category"]]["family"]


def corpus_document_ids() -> set:
    """第 9 周快照清单内的全部 document_id (考卷引用必须回到冻结语料)。"""
    with open(CORPUS_MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return {doc["document_id"] for doc in manifest["documents"]}

"""
确定性业务逻辑层 (Deterministic Domain Service)
负责执行 100% 确定性的装配规格核对规则与逻辑分支处理:
零大模型调用、零幻觉、零外部写副作用 (仅落盘 task_runs 审计记录)。

判定分支 (回链 vertical-slice-contract.md §3 判定表, 按序短路):
1. ACL 内零条现行有效规格            -> needs_review / NO_MATCHING_SPEC
2. 多条现行规格且版本不同             -> needs_review / SPEC_CONFLICT
3. 唯一命中且 |偏差%| > 15.0          -> needs_review / SPEC_HARD_LIMIT_EXCEEDED (响应零规格内容)
4. 唯一命中且 |偏差%| <= 15.0         -> answered + 规格出处 + 偏差/conformance 证据
"""
from application.models import (
    ProcessStep,
    ReasonCodeEnum,
    ReviewPreview,
    SpecCheckRequest,
    SpecCheckResponse,
    SpecDetails,
    SpecEvaluation,
    StatusEnum,
    TrustedUser,
)
from application.database import (
    get_policy_config_value,
    query_current_specs,
    record_task_run,
)

# 偏差硬限引擎默认值 (配置缺失/非法时的行为等价回退, 与 004 迁移种子一致):
# |偏差%| 超过该值需质量主管复核。节点 05 起运行时真值来自 policy_config 表
# (发布配置收敛器唯一可写面), known-good 配置 (15.0) 下行为与冻结基线逐字节等价。
HARD_LIMIT_DEVIATION_PCT = 15.0
HARD_LIMIT_CONFIG_KEY = "spec_check.hard_limit_deviation_pct"


def get_hard_limit_deviation_pct() -> float:
    """读取运行时偏差硬限: policy_config 配置优先; 缺失/非法/非正值回退引擎默认。

    收敛器不判对错 (对错靠评审 + 冒烟双防线), 因此引擎对非法配置值自行防御:
    解析失败或 <= 0 一律回退默认值, 绝不让坏配置拖垮服务可用性。
    """
    try:
        raw = get_policy_config_value(HARD_LIMIT_CONFIG_KEY)
        if raw is None:
            return HARD_LIMIT_DEVIATION_PCT
        value = float(raw)
        return value if value > 0 else HARD_LIMIT_DEVIATION_PCT
    except Exception:
        return HARD_LIMIT_DEVIATION_PCT

# 工序 -> 计量单位映射 (引擎侧常量, 供阻断预览描述实测值; 不泄露规格库内容)
UNIT_BY_PROCESS = {
    ProcessStep.TORQUE_TIGHTENING.value: "N·m",
    ProcessStep.GAP_CHECK.value: "mm",
}


def compute_deviation_pct(measured_value: float, nominal_value: float) -> float:
    """偏差百分比 = (实测 - 名义) / 名义 × 100, 保留 2 位小数 (公式可复算)。"""
    return round((measured_value - nominal_value) / nominal_value * 100.0, 2)


def evaluate_conformance(
    measured_value: float, nominal_value: float, tol_plus: float, tol_minus: float
) -> str:
    """conforming 当且仅当 (nominal - tol_minus) <= 实测 <= (nominal + tol_plus)。"""
    lower = nominal_value - tol_minus
    upper = nominal_value + tol_plus
    return "conforming" if lower <= measured_value <= upper else "nonconforming"


def _blocked_response(
    request: SpecCheckRequest,
    user: TrustedUser,
    reason: ReasonCodeEnum,
    preview: ReviewPreview,
) -> SpecCheckResponse:
    """构造阻断终态响应并落审计 (阻断路径 matched_spec_id 恒为 NULL, 零隐性依据)。"""
    response = SpecCheckResponse(
        request_id=request.request_id,
        status=StatusEnum.NEEDS_REVIEW,
        conformance_passed=False,
        spec=None,
        evaluation=None,
        reason_code=reason,
        reviewer_preview=preview,
    )
    record_task_run(
        request_id=request.request_id,
        user=user,
        task_type=request.task_type.value,
        status=StatusEnum.NEEDS_REVIEW.value,
        reason_code=reason.value,
        matched_spec_id=None,
    )
    return response


def process_spec_check(request: SpecCheckRequest, user: TrustedUser) -> SpecCheckResponse:
    """
    处理装配规格核对的核心领域逻辑:
    1. ACL 下推查询「现行有效」规格 (已替代/过期版本在 SQL 层被排除);
    2. 按判定表短路映射终态 (answered / needs_review);
    3. 每个分支写审计, 阻断路径 matched_spec_id 恒为 NULL (与响应零内容一致)。
    """
    # 1. ACL 下推确定性 SQL 查询 (P-1/P-2 在此层完成)
    matched_specs = query_current_specs(
        part_no=request.part_no,
        process_step=request.process_step.value,
        measured_at=request.measured_at,
        allowed_oem_projects=user.allowed_oem_projects,
    )

    # 2.1 零匹配 -> needs_review / NO_MATCHING_SPEC
    #     (含"仅剩已被替代/过期版本"情形: is_current=0 行不可引用, 宁可拒答不回溯旧版)
    if not matched_specs:
        preview = ReviewPreview(
            target_role="quality_supervisor",
            suggested_action="人工核对规格适用性与测量条件",
            reason=(
                f"未找到零件 '{request.part_no}' 工序 '{request.process_step.value}' "
                f"在 '{request.measured_at}' 现行有效且位于授权项目组范围内的检验规格"
                "（已被替代/过期版本已按保护指标排除）"
            ),
        )
        return _blocked_response(request, user, ReasonCodeEnum.NO_MATCHING_SPEC, preview)

    # 2.2 多条现行规格且版本不同 -> needs_review / SPEC_CONFLICT
    unique_versions = set(row["version"] for row in matched_specs)
    if len(matched_specs) > 1 and len(unique_versions) > 1:
        preview = ReviewPreview(
            target_role="quality_supervisor",
            suggested_action="人工澄清现行规格版本冲突",
            reason=f"检索到 {len(matched_specs)} 条现行有效规格冲突版本：{sorted(unique_versions)}",
        )
        return _blocked_response(request, user, ReasonCodeEnum.SPEC_CONFLICT, preview)

    return _resolve_unique_match(request, user, matched_specs[0])


def _resolve_unique_match(request: SpecCheckRequest, user: TrustedUser, best: dict) -> SpecCheckResponse:
    """唯一现行有效规格命中: 先判硬限阻断, 再判 answered (带引用证据)。"""
    deviation_pct = compute_deviation_pct(request.measured_value, best["nominal_value"])
    hard_limit = get_hard_limit_deviation_pct()

    # 2.3a 偏差超硬限 -> needs_review / SPEC_HARD_LIMIT_EXCEEDED
    #      响应不含任何规格内容 (spec/evaluation 恒为 None), 审计不留隐性依据
    if abs(deviation_pct) > hard_limit:
        preview = ReviewPreview(
            target_role="quality_supervisor",
            suggested_action="创建质量主管复核单（原系统人工录入）",
            reason=(
                f"实测值 {request.measured_value} {UNIT_BY_PROCESS[request.process_step.value]} "
                f"相对现行规格偏差 {deviation_pct}%，超过 {hard_limit}% 硬限，"
                "需质量主管复核确认"
            ),
        )
        return _blocked_response(request, user, ReasonCodeEnum.SPEC_HARD_LIMIT_EXCEEDED, preview)

    # 2.3b 唯一现行有效规格且未超硬限 -> answered (依据充分, 可出具带引用草稿)
    #      注意: answered != 合格; conformance=nonconforming 是报告草稿的证据内容
    conformance = evaluate_conformance(
        request.measured_value, best["nominal_value"], best["tol_plus"], best["tol_minus"]
    )
    spec_details = SpecDetails(
        spec_id=best["spec_id"],
        title=best["title"],
        version=best["version"],
        effective_at=best["effective_at"],
        expires_at=best["expires_at"],
        source_ref=best["source_ref"],
        unit=best["unit"],
    )
    evaluation = SpecEvaluation(
        nominal_value=best["nominal_value"],
        tolerance=f"+{best['tol_plus']}/-{best['tol_minus']}",
        measured_value=request.measured_value,
        deviation_pct=deviation_pct,
        conformance=conformance,
        formula_rule=best["formula_rule"],
    )
    response = SpecCheckResponse(
        request_id=request.request_id,
        status=StatusEnum.ANSWERED,
        conformance_passed=(conformance == "conforming"),
        spec=spec_details,
        evaluation=evaluation,
        reason_code=None,
        reviewer_preview=None,
    )
    record_task_run(
        request_id=request.request_id,
        user=user,
        task_type=request.task_type.value,
        status=StatusEnum.ANSWERED.value,
        reason_code=None,
        matched_spec_id=best["spec_id"],
    )
    return response

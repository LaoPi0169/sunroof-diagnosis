"""
确定性业务逻辑层 (Deterministic Domain Service)
负责执行 100% 确定性的业务规则校验与逻辑分支处理:
零大模型调用、零幻觉、零外部写副作用 (仅落盘 task_runs 审计记录)。

状态映射规则 (回链 Week 05 薄片合同):
1. 金额 > 2000 元            -> needs_review / AMOUNT_EXCEEDS_LIMIT (权限门槛阻断)
2. SQL 零匹配                -> needs_review / NO_MATCHING_POLICY
3. 多条匹配且版本冲突         -> needs_review / POLICY_CONFLICT
4. 唯一当前有效政策           -> answered + 来源/版本/公式规则
"""
from application.models import (
    PolicyCheckRequest,
    PolicyCheckResponse,
    TrustedUser,
    StatusEnum,
    ReasonCodeEnum,
    PolicyDetails,
    EscalationPreview,
)
from application.database import query_policies, record_task_run

AMOUNT_APPROVAL_THRESHOLD = 2000.0  # 超过 2000 元触发主管复核门槛


def process_policy_check(request: PolicyCheckRequest, user: TrustedUser) -> PolicyCheckResponse:
    """
    处理计费政策核对的核心领域逻辑:
    1. 校验极硬权限规则（金额 > 2000 元）；
    2. 查询适用且当前有效的确定性 SQL 数据；
    3. 根据查询结果映射终态（answered / needs_review）；
    4. 记录数据对账审计日志。
    """

    # 1. 检查金额门槛规则：退款/变动金额 > 2000 元触发权限阻断
    if request.amount and request.amount > AMOUNT_APPROVAL_THRESHOLD:
        status = StatusEnum.NEEDS_REVIEW
        reason = ReasonCodeEnum.AMOUNT_EXCEEDS_LIMIT
        preview = EscalationPreview(
            target_role="supervisor",
            suggested_action="创建主管复核单",
            reason=f"涉及金额 {request.amount} 元，超过普通客服 {AMOUNT_APPROVAL_THRESHOLD} 元审批门槛",
        )
        response = PolicyCheckResponse(
            request_id=request.request_id,
            status=status,
            eligible=False,
            reason_code=reason,
            escalation_preview=preview,
        )
        record_task_run(
            request_id=request.request_id,
            user_id=user.user_id,
            user_role=user.user_role,
            task_type=request.task_type.value,
            status=status.value,
            reason_code=reason.value,
            matched_policy_id=None,
        )
        return response

    # 2. 查询关系数据库中匹配的政策
    matched_policies = query_policies(
        region=request.region.value,
        purchased_at=request.purchased_at,
        user_role=user.user_role,
    )

    # 3.1 无匹配政策 -> needs_review (NO_MATCHING_POLICY)
    if not matched_policies:
        status = StatusEnum.NEEDS_REVIEW
        reason = ReasonCodeEnum.NO_MATCHING_POLICY
        preview = EscalationPreview(
            target_role="supervisor",
            suggested_action="人工裁决政策适用性",
            reason=f"未找到地区 '{request.region.value}' 且在 '{request.purchased_at}' 有效的普通客服政策",
        )
        response = PolicyCheckResponse(
            request_id=request.request_id,
            status=status,
            eligible=False,
            reason_code=reason,
            escalation_preview=preview,
        )
        record_task_run(
            request_id=request.request_id,
            user_id=user.user_id,
            user_role=user.user_role,
            task_type=request.task_type.value,
            status=status.value,
            reason_code=reason.value,
            matched_policy_id=None,
        )
        return response

    # 3.2 存在多个冲突的有效政策 -> needs_review (POLICY_CONFLICT)
    if len(matched_policies) > 1:
        unique_versions = set(p["version"] for p in matched_policies)
        if len(unique_versions) > 1:
            status = StatusEnum.NEEDS_REVIEW
            reason = ReasonCodeEnum.POLICY_CONFLICT
            preview = EscalationPreview(
                target_role="supervisor",
                suggested_action="人工澄清政策版本冲突",
                reason=f"检索到 {len(matched_policies)} 篇冲突政策版本：{sorted(unique_versions)}",
            )
            response = PolicyCheckResponse(
                request_id=request.request_id,
                status=status,
                eligible=False,
                reason_code=reason,
                escalation_preview=preview,
            )
            record_task_run(
                request_id=request.request_id,
                user_id=user.user_id,
                user_role=user.user_role,
                task_type=request.task_type.value,
                status=status.value,
                reason_code=reason.value,
                matched_policy_id=None,
            )
            return response

    # 3.3 存在唯一且适用的当前有效政策 -> answered (SUCCESS)
    best_policy = matched_policies[0]  # SQL 已按 effective_at DESC 排序，取最新适用版本
    status = StatusEnum.ANSWERED
    details = PolicyDetails(
        policy_id=best_policy["policy_id"],
        title=best_policy["title"],
        version=best_policy["version"],
        effective_at=best_policy["effective_at"],
        source_id=best_policy["source_id"],
        formula_rule=best_policy["formula_rule"],
    )
    response = PolicyCheckResponse(
        request_id=request.request_id,
        status=status,
        eligible=True,
        policy_details=details,
    )
    record_task_run(
        request_id=request.request_id,
        user_id=user.user_id,
        user_role=user.user_role,
        task_type=request.task_type.value,
        status=status.value,
        reason_code=None,
        matched_policy_id=best_policy["policy_id"],
    )
    return response

"""
任务状态机与幂等动作控制 (Week 07)

回链 specs/mission-week-07.md 第 2 天 + task-state-model.md:
  * 服务端 RBAC 一票否决: 越权动作走 denied 路径, candidate_policy 恒为 None (零泄漏);
  * check_policy 100% 复用 Week 05 确定性政策引擎 process_policy_check (零逻辑分叉);
  * confirm/refuse/escalate/supervisor_resolve 幂等, 重复提交返回 ALREADY_APPLIED;
  * 无效状态转移不改变工单终态, 返回 INVALID_TASK_STATE (显式失败不猜答案);
  * 每个动作 (含越权与无效转移) 都落 task_action_runs 审计留痕。
"""
from application.auth import authorize_action
from application.database import (
    get_billing_task,
    record_task_action_run,
    update_billing_task_state,
)
from application.models import (
    EscalationPreview,
    PolicyCheckRequest,
    RegionEnum,
    StatusEnum,
    TaskAction,
    TaskActionReasonCode,
    TaskActionRequest,
    TaskResponse,
    TaskStatus,
    TaskType,
    TrustedUser,
)
from application.services import process_policy_check

# 各状态下的合法后续动作 (工单状态机 -> allowed_actions 提示)
_NEXT_ACTIONS = {
    TaskStatus.INITIALIZED.value: [TaskAction.CHECK_POLICY],
    TaskStatus.ANSWERED.value: [TaskAction.CONFIRM, TaskAction.REFUSE, TaskAction.ESCALATE],
    TaskStatus.NEEDS_REVIEW.value: [TaskAction.ESCALATE],
    TaskStatus.CONFIRMED.value: [],
    TaskStatus.REFUSED.value: [],
    TaskStatus.ESCALATED.value: [],
    TaskStatus.ACTION_COMPLETED.value: [],
}


def _audit(request: TaskActionRequest, user: TrustedUser, status: TaskStatus,
           reason_code: TaskActionReasonCode = None) -> None:
    """动作审计留痕 (响应/billing_tasks/task_action_runs 三向对账锚点之一)"""
    record_task_action_run(
        task_id=request.task_id,
        correlation_id=request.correlation_id,
        user_id=user.user_id,
        user_role=user.user_role,
        action=request.action.value,
        status=status.value,
        reason_code=reason_code.value if reason_code is not None else None,
    )


def _base_response(task: dict, request: TaskActionRequest, status: TaskStatus,
                   reason_code: TaskActionReasonCode = None) -> TaskResponse:
    return TaskResponse(
        task_id=task["task_id"],
        case_id=task["case_id"],
        status=status,
        reason_code=reason_code,
        candidate_policy=None,
        escalation_preview=None,
        allowed_actions=list(_NEXT_ACTIONS.get(status.value, [])),
        correlation_id=request.correlation_id,
    )


def _denied_response(request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """RBAC 阻断: 显式拒绝、candidate_policy 恒为 None、响应中零政策内容 (防信息泄漏)"""
    _audit(request, user, TaskStatus.DENIED, TaskActionReasonCode.ACTION_NOT_ALLOWED)
    task = get_billing_task(request.task_id)
    return TaskResponse(
        task_id=request.task_id,
        case_id=task["case_id"] if task else "NS-UNKNOWN",
        status=TaskStatus.DENIED,
        reason_code=TaskActionReasonCode.ACTION_NOT_ALLOWED,
        candidate_policy=None,
        escalation_preview=None,
        allowed_actions=[],
        correlation_id=request.correlation_id,
        message="Action denied by server authorization check. User lacks permissions.",
    )


def _invalid_transition(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """无效状态转移: 不改变工单终态 (显式失败不猜答案)，审计留痕"""
    current_status = TaskStatus(task["status"])
    _audit(request, user, current_status, TaskActionReasonCode.INVALID_TASK_STATE)
    response = _base_response(task, request, current_status, TaskActionReasonCode.INVALID_TASK_STATE)
    response.message = (
        f"当前状态 {current_status.value} 不允许执行动作 {request.action.value}，本次提交未生效。"
    )
    return response


def process_task_action(request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """
    任务状态机总控入口:
    1. RBAC 服务端授权一票否决 (身份绝不来自请求体);
    2. 按动作分发到状态机处理器。
    """
    # 1. 服务端授权一票否决 (先于任何业务查询与状态写入)
    if not authorize_action(user, request.action):
        return _denied_response(request, user)

    task = get_billing_task(request.task_id)
    if task is None:  # 端点层已 404 拦截；此处兜底保证直接调用方安全
        response = _base_response(
            {"task_id": request.task_id, "case_id": "NS-UNKNOWN"},
            request, TaskStatus.NEEDS_REVIEW, TaskActionReasonCode.NO_MATCHING_POLICY,
        )
        response.message = "Unknown task_id: 任务不存在，未产生任何状态变更。"
        return response

    # 2. 动作分发
    if request.action == TaskAction.CHECK_POLICY:
        return _handle_check_policy(task, request, user)
    if request.action == TaskAction.CONFIRM:
        return _handle_confirm(task, request, user)
    if request.action == TaskAction.REFUSE:
        return _handle_refuse(task, request, user)
    if request.action == TaskAction.ESCALATE:
        return _handle_escalate(task, request, user)
    if request.action == TaskAction.SUPERVISOR_RESOLVE:
        return _handle_supervisor_resolve(task, request, user)
    return _invalid_transition(task, request, user)


def _handle_check_policy(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """
    check_policy: 把工单事实参数化后 100% 复用 Week 05 确定性引擎。
    引擎内部四分支映射: 金额超限 / 零匹配 / 版本冲突 -> needs_review; 唯一命中 -> answered。
    引擎同时落 Week 05 task_runs 审计 (week-05 CHECK 约束内合法状态)。
    """
    check_request = PolicyCheckRequest(
        request_id=request.correlation_id,
        task_type=TaskType.BILLING_POLICY_CHECK,
        region=RegionEnum(task["region"]),
        purchased_at=task["purchased_at"],
        amount=task["amount"],
    )
    result = process_policy_check(check_request, user)

    if result.status == StatusEnum.ANSWERED and result.policy_details is not None:
        update_billing_task_state(
            task["task_id"],
            TaskStatus.ANSWERED.value,
            matched_policy_id=result.policy_details.policy_id,
        )
        response = _base_response(task, request, TaskStatus.ANSWERED,
                                  TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
        response.candidate_policy = result.policy_details
        _audit(request, user, TaskStatus.ANSWERED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
        return response

    # needs_review 分支 (AMOUNT_EXCEEDS_LIMIT / NO_MATCHING_POLICY / POLICY_CONFLICT)
    reason = TaskActionReasonCode(result.reason_code.value)
    update_billing_task_state(task["task_id"], TaskStatus.NEEDS_REVIEW.value, reason_code=reason.value)
    response = _base_response(task, request, TaskStatus.NEEDS_REVIEW, reason)
    response.escalation_preview = result.escalation_preview
    _audit(request, user, TaskStatus.NEEDS_REVIEW, reason)
    return response


def _handle_confirm(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """confirm: answered -> confirmed (幂等: 已确认重复提交返回 ALREADY_APPLIED)"""
    current = task["status"]
    if current == TaskStatus.CONFIRMED.value:
        _audit(request, user, TaskStatus.CONFIRMED, TaskActionReasonCode.ALREADY_APPLIED)
        response = _base_response(task, request, TaskStatus.CONFIRMED,
                                  TaskActionReasonCode.ALREADY_APPLIED)
        response.is_idempotent = True
        response.message = "该工单已确认执行，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ANSWERED.value:
        return _invalid_transition(task, request, user)

    update_billing_task_state(task["task_id"], TaskStatus.CONFIRMED.value,
                              reason_code=TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES.value)
    _audit(request, user, TaskStatus.CONFIRMED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response = _base_response(task, request, TaskStatus.CONFIRMED,
                              TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response.message = "工单已确认执行 (确定性闭环，无模型调用)。"
    return response


def _handle_refuse(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """refuse: answered -> refused (验收标准第 4 条的显式拒绝动作, 幂等)"""
    current = task["status"]
    if current == TaskStatus.REFUSED.value:
        _audit(request, user, TaskStatus.REFUSED, TaskActionReasonCode.ALREADY_APPLIED)
        response = _base_response(task, request, TaskStatus.REFUSED,
                                  TaskActionReasonCode.ALREADY_APPLIED)
        response.is_idempotent = True
        response.message = "该工单已拒绝，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ANSWERED.value:
        return _invalid_transition(task, request, user)

    update_billing_task_state(task["task_id"], TaskStatus.REFUSED.value,
                              reason_code=TaskActionReasonCode.REFUSED_BY_AGENT.value)
    _audit(request, user, TaskStatus.REFUSED, TaskActionReasonCode.REFUSED_BY_AGENT)
    response = _base_response(task, request, TaskStatus.REFUSED,
                              TaskActionReasonCode.REFUSED_BY_AGENT)
    response.message = "工单已由一线客服显式拒绝 (拒绝属显式决定，不会被静默猜测替代)。"
    return response


def _handle_escalate(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """escalate: answered/needs_review -> escalated (幂等键 + 状态双重防护)"""
    current = task["status"]
    existing_key = task["idempotency_key"]
    is_replay = current == TaskStatus.ESCALATED.value or (
        request.idempotency_key is not None
        and existing_key is not None
        and request.idempotency_key == existing_key
    )
    if is_replay:
        _audit(request, user, TaskStatus.ESCALATED, TaskActionReasonCode.ALREADY_APPLIED)
        response = _base_response(task, request, TaskStatus.ESCALATED,
                                  TaskActionReasonCode.ALREADY_APPLIED)
        response.is_idempotent = True
        response.message = "该工单已在主管复核队列，本次提交为幂等重放。"
        return response
    if current not in (TaskStatus.ANSWERED.value, TaskStatus.NEEDS_REVIEW.value):
        return _invalid_transition(task, request, user)

    reason = request.reason_code or (
        TaskActionReasonCode(task["reason_code"])
        if task["reason_code"]
        else TaskActionReasonCode.SPECIAL_TASK_REQUIRES_REVIEW
    )
    update_billing_task_state(task["task_id"], TaskStatus.ESCALATED.value,
                              reason_code=reason.value, idempotency_key=request.idempotency_key)
    _audit(request, user, TaskStatus.ESCALATED, reason)
    response = _base_response(task, request, TaskStatus.ESCALATED, reason)
    response.escalation_preview = EscalationPreview(
        target_role="supervisor",
        suggested_action="主管复核结案 (supervisor_resolve)",
        reason=f"一线动作 escalate 已触发主管复核 (原因: {reason.value})",
    )
    response.message = "该任务已进入主管复核队列，等待 supervisor 结案。"
    return response


def _handle_supervisor_resolve(task: dict, request: TaskActionRequest,
                               user: TrustedUser) -> TaskResponse:
    """supervisor_resolve: escalated -> action_completed (主管专属闭环, 幂等)"""
    current = task["status"]
    if current == TaskStatus.ACTION_COMPLETED.value:
        _audit(request, user, TaskStatus.ACTION_COMPLETED, TaskActionReasonCode.ALREADY_APPLIED)
        response = _base_response(task, request, TaskStatus.ACTION_COMPLETED,
                                  TaskActionReasonCode.ALREADY_APPLIED)
        response.is_idempotent = True
        response.message = "该工单已由主管结案，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ESCALATED.value:
        return _invalid_transition(task, request, user)

    update_billing_task_state(task["task_id"], TaskStatus.ACTION_COMPLETED.value,
                              reason_code=TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES.value)
    _audit(request, user, TaskStatus.ACTION_COMPLETED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response = _base_response(task, request, TaskStatus.ACTION_COMPLETED,
                              TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response.message = "主管复核结案完成，工单闭环。"
    return response

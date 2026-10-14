"""
任务状态机与幂等动作控制 (阶段 A 节点 04 双角色任务闭环)

回链 task-state-model.md + specs/role-and-permission-matrix.md §2:
  * 服务端 RBAC 一票否决: 越权动作走 denied 路径, candidate_spec 恒为 None (零泄漏);
  * 任务级 ACL (D6): 跨项目 PQE 直调任务 -> denied + 零回显 (case_id 用通用常量);
  * check_spec 100% 复用节点 02 确定性引擎 process_spec_check (零逻辑分叉,
    引擎同时落节点 02 task_runs 审计, 形成跨节点血缘衔接);
  * confirm/refuse/escalate/supervisor_resolve 幂等, 重复提交返回 ALREADY_APPLIED;
  * 无效状态转移不改变任务终态, 返回 INVALID_TASK_STATE (显式失败不猜答案);
  * 每个动作 (含越权与无效转移) 都落 task_action_runs 审计留痕;
  * 阶段 D 批次 1 (adr-005 D1/D4): 乐观锁版本列 —— 状态写入携带读时版本
    (expected_version), 并发冲突 -> 结构化 CONCURRENCY_CONFLICT (任务终态不变
    + 审计留痕, 服务端不盲重试)。
"""
from application.auth import authorize_action
from application.database import (
    get_spec_check_task,
    record_task_action_run,
    update_task_state,
)
from application.models import (
    ProcessStep,
    ReviewPreview,
    SpecCheckRequest,
    StatusEnum,
    TaskAction,
    TaskActionReasonCode,
    TaskActionRequest,
    TaskResponse,
    TaskStatus,
    TaskType,
    TrustedUser,
)
from application.services import process_spec_check

# 各状态下的合法后续动作 (任务状态机 -> allowed_actions 提示)
_NEXT_ACTIONS = {
    TaskStatus.INITIALIZED.value: [TaskAction.CHECK_SPEC],
    TaskStatus.ANSWERED.value: [TaskAction.CONFIRM, TaskAction.REFUSE, TaskAction.ESCALATE],
    TaskStatus.NEEDS_REVIEW.value: [TaskAction.ESCALATE],
    TaskStatus.CONFIRMED.value: [],
    TaskStatus.REFUSED.value: [],
    TaskStatus.ESCALATED.value: [],
    TaskStatus.ACTION_COMPLETED.value: [],
}

# 跨项目拒绝路径的通用占位 case_id (拒绝响应不回显其他项目组的案例号 —— 零泄漏)
_ACL_DENIED_CASE_ID = "N/A-ACL-DENIED"


def _has_task_access(user: TrustedUser, task: dict) -> bool:
    """任务级 ACL (D6): 主管 [\"*\"] 跨项目只读; PQE 只能触达本项目组任务。"""
    if user.allowed_oem_projects == ["*"]:
        return True
    return task["oem_project"] in user.allowed_oem_projects


def _audit(request: TaskActionRequest, user: TrustedUser, status: TaskStatus,
           reason_code: TaskActionReasonCode = None) -> None:
    """动作审计留痕 (响应/spec_check_tasks/task_action_runs 三向对账锚点之一)"""
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
        candidate_spec=None,
        evaluation=None,
        escalation_preview=None,
        allowed_actions=list(_NEXT_ACTIONS.get(status.value, [])),
        correlation_id=request.correlation_id,
    )


def _denied_response(request: TaskActionRequest, user: TrustedUser,
                     task: dict = None) -> TaskResponse:
    """RBAC 阻断: 显式拒绝、candidate_spec 恒为 None、响应零规格内容 (防信息泄漏)。
    跨项目拒绝 (task 不属于用户 ACL 范围) 时 case_id 用通用常量, 不回显归属项目组标识。"""
    _audit(request, user, TaskStatus.DENIED, TaskActionReasonCode.ACTION_NOT_ALLOWED)
    cross_project = task is not None and not _has_task_access(user, task)
    return TaskResponse(
        task_id=request.task_id,
        case_id=_ACL_DENIED_CASE_ID if cross_project else (task["case_id"] if task else "N/A-UNKNOWN"),
        status=TaskStatus.DENIED,
        reason_code=TaskActionReasonCode.ACTION_NOT_ALLOWED,
        candidate_spec=None,
        evaluation=None,
        escalation_preview=None,
        allowed_actions=[],
        correlation_id=request.correlation_id,
        message="Action denied by server authorization check. User lacks permissions.",
    )


def _invalid_transition(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """无效状态转移: 不改变任务终态 (显式失败不猜答案)，审计留痕"""
    current_status = TaskStatus(task["status"])
    _audit(request, user, current_status, TaskActionReasonCode.INVALID_TASK_STATE)
    response = _base_response(task, request, current_status, TaskActionReasonCode.INVALID_TASK_STATE)
    response.message = (
        f"当前状态 {current_status.value} 不允许执行动作 {request.action.value}，本次提交未生效。"
    )
    return response


def _conflict_response(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """乐观锁冲突 (阶段 D, adr-005 D4): 显式失败不猜答案 —— 任务终态不变 + 审计留痕。

    读时版本已被其他动作推进 (expected_version 过期) -> 结构化拒绝; 服务端不做盲重试,
    客户端应重新读取任务 (获取新 version) 后再提交。冲突事件同样落审计留痕。
    """
    current_status = TaskStatus(task["status"])
    _audit(request, user, current_status, TaskActionReasonCode.CONCURRENCY_CONFLICT)
    response = _base_response(task, request, current_status,
                              TaskActionReasonCode.CONCURRENCY_CONFLICT)
    response.message = (
        "任务已被并发动作更新 (版本过期)，本次提交未生效；请重新读取任务后重试。"
    )
    return response


def process_task_action(request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """
    任务状态机总控入口:
    1. RBAC 服务端授权一票否决 (身份绝不来自请求体);
    2. 任务级 ACL 交叉校验 (D6: 跨项目直调 -> denied 零泄漏);
    3. 按动作分发到状态机处理器。
    """
    # 1. 服务端授权一票否决 (先于任何业务查询与状态写入)
    if not authorize_action(user, request.action):
        return _denied_response(request, user)

    task = get_spec_check_task(request.task_id)
    if task is None:  # 端点层已 404 拦截；此处兜底保证直接调用方安全
        response = _base_response(
            {"task_id": request.task_id, "case_id": "N/A-UNKNOWN"},
            request, TaskStatus.NEEDS_REVIEW, TaskActionReasonCode.NO_MATCHING_SPEC,
        )
        response.message = "Unknown task_id: 任务不存在，未产生任何状态变更。"
        return response

    # 2. 任务级 ACL 交叉校验 (跨项目直调 -> denied, 响应零归属项目组标识)
    if not _has_task_access(user, task):
        return _denied_response(request, user, task)

    # 3. 动作分发
    if request.action == TaskAction.CHECK_SPEC:
        return _handle_check_spec(task, request, user)
    if request.action == TaskAction.CONFIRM:
        return _handle_confirm(task, request, user)
    if request.action == TaskAction.REFUSE:
        return _handle_refuse(task, request, user)
    if request.action == TaskAction.ESCALATE:
        return _handle_escalate(task, request, user)
    if request.action == TaskAction.SUPERVISOR_RESOLVE:
        return _handle_supervisor_resolve(task, request, user)
    return _invalid_transition(task, request, user)


def _handle_check_spec(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """
    check_spec: 把任务事实参数化后 100% 复用节点 02 确定性引擎 (adr-003 D5)。
    引擎判定表分支原样映射: 硬限超限/零匹配/版本冲突 -> needs_review; 唯一现行命中 -> answered。
    引擎同时落节点 02 task_runs 审计 (001 CHECK 约束内合法状态, 跨节点血缘锚点)。
    """
    check_request = SpecCheckRequest(
        request_id=request.correlation_id,
        task_type=TaskType.SPEC_CHECK,
        sn=task["sn"],
        part_no=task["part_no"],
        process_step=ProcessStep(task["process_step"]),
        measured_at=task["measured_at"],
        measured_value=task["measured_value"],
    )
    result = process_spec_check(check_request, user)

    if result.status == StatusEnum.ANSWERED and result.spec is not None:
        if not update_task_state(
            task["task_id"],
            TaskStatus.ANSWERED.value,
            matched_spec_id=result.spec.spec_id,
            expected_version=task["version"],
        ):
            return _conflict_response(task, request, user)
        response = _base_response(task, request, TaskStatus.ANSWERED,
                                  TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
        response.candidate_spec = result.spec
        response.evaluation = result.evaluation
        _audit(request, user, TaskStatus.ANSWERED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
        return response

    # needs_review 分支 (SPEC_HARD_LIMIT_EXCEEDED / NO_MATCHING_SPEC / SPEC_CONFLICT)
    reason = TaskActionReasonCode(result.reason_code.value)
    if not update_task_state(task["task_id"], TaskStatus.NEEDS_REVIEW.value,
                             reason_code=reason.value, expected_version=task["version"]):
        return _conflict_response(task, request, user)
    response = _base_response(task, request, TaskStatus.NEEDS_REVIEW, reason)
    response.escalation_preview = result.reviewer_preview
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
        response.message = "该任务已确认执行，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ANSWERED.value:
        return _invalid_transition(task, request, user)

    if not update_task_state(task["task_id"], TaskStatus.CONFIRMED.value,
                             reason_code=TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES.value,
                             expected_version=task["version"]):
        return _conflict_response(task, request, user)
    _audit(request, user, TaskStatus.CONFIRMED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response = _base_response(task, request, TaskStatus.CONFIRMED,
                              TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response.message = "任务已确认执行 (确定性闭环，无模型调用)。"
    return response


def _handle_refuse(task: dict, request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    """refuse: answered -> refused (显式拒绝动作, 幂等; 拒绝属显式决定，不会被静默猜测替代)"""
    current = task["status"]
    if current == TaskStatus.REFUSED.value:
        _audit(request, user, TaskStatus.REFUSED, TaskActionReasonCode.ALREADY_APPLIED)
        response = _base_response(task, request, TaskStatus.REFUSED,
                                  TaskActionReasonCode.ALREADY_APPLIED)
        response.is_idempotent = True
        response.message = "该任务已拒绝，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ANSWERED.value:
        return _invalid_transition(task, request, user)

    if not update_task_state(task["task_id"], TaskStatus.REFUSED.value,
                             reason_code=TaskActionReasonCode.REFUSED_BY_AGENT.value,
                             expected_version=task["version"]):
        return _conflict_response(task, request, user)
    _audit(request, user, TaskStatus.REFUSED, TaskActionReasonCode.REFUSED_BY_AGENT)
    response = _base_response(task, request, TaskStatus.REFUSED,
                              TaskActionReasonCode.REFUSED_BY_AGENT)
    response.message = "任务已由一线 PQE 显式拒绝 (拒绝属显式决定，不会被静默猜测替代)。"
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
        response.message = "该任务已在质量主管复核队列，本次提交为幂等重放。"
        return response
    if current not in (TaskStatus.ANSWERED.value, TaskStatus.NEEDS_REVIEW.value):
        return _invalid_transition(task, request, user)

    reason = request.reason_code or (
        TaskActionReasonCode(task["reason_code"])
        if task["reason_code"]
        else TaskActionReasonCode.SPECIAL_TASK_REQUIRES_REVIEW
    )
    if not update_task_state(task["task_id"], TaskStatus.ESCALATED.value,
                             reason_code=reason.value,
                             idempotency_key=request.idempotency_key,
                             expected_version=task["version"]):
        return _conflict_response(task, request, user)
    _audit(request, user, TaskStatus.ESCALATED, reason)
    response = _base_response(task, request, TaskStatus.ESCALATED, reason)
    response.escalation_preview = ReviewPreview(
        target_role="quality_supervisor",
        suggested_action="主管复核结案 (supervisor_resolve)",
        reason=f"一线动作 escalate 已触发质量主管复核 (原因: {reason.value})",
    )
    response.message = "该任务已进入质量主管复核队列，等待 supervisor 结案。"
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
        response.message = "该任务已由质量主管结案，本次提交为幂等重放。"
        return response
    if current != TaskStatus.ESCALATED.value:
        return _invalid_transition(task, request, user)

    if not update_task_state(task["task_id"], TaskStatus.ACTION_COMPLETED.value,
                             reason_code=TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES.value,
                             expected_version=task["version"]):
        return _conflict_response(task, request, user)
    _audit(request, user, TaskStatus.ACTION_COMPLETED, TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response = _base_response(task, request, TaskStatus.ACTION_COMPLETED,
                              TaskActionReasonCode.SUPPORTED_CURRENT_SOURCES)
    response.message = "质量主管复核结案完成，任务闭环。"
    return response

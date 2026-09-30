"""
定义确定性垂直薄片的数据传输对象 (DTO) 与 Pydantic Schema 契约
完全回链至 Week 04 Discovery Brief v1 与 Week 05 接口契约 (api-contract/policy-check-v1.json)
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class TaskType(str, Enum):
    BILLING_POLICY_CHECK = "billing_policy_check"


class RegionEnum(str, Enum):
    EAST = "华东"
    SOUTH = "华南"
    NORTH = "华北"
    NATIONAL = "全国"


class StatusEnum(str, Enum):
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"
    DENIED = "denied"


class ReasonCodeEnum(str, Enum):
    MISSING_REQUIRED_PARAM = "MISSING_REQUIRED_PARAM"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    AMOUNT_EXCEEDS_LIMIT = "AMOUNT_EXCEEDS_LIMIT"
    NO_MATCHING_POLICY = "NO_MATCHING_POLICY"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"


class TrustedUser(BaseModel):
    """服务端可信会话中提取的用户身份 DTO"""
    user_id: str
    user_role: str  # 例如 'support_agent', 'supervisor'


class PolicyCheckRequest(BaseModel):
    """客户端请求 Schema"""
    model_config = ConfigDict(extra="forbid")  # 封闭模式：禁止额外未知字段

    request_id: str = Field(..., description="UUID 关联标识")
    task_type: TaskType = Field(..., description="任务类型")
    region: RegionEnum = Field(..., description="客户所属地区")
    purchased_at: str = Field(..., description="购买时间 (ISO-8601 格式)")
    amount: Optional[float] = Field(default=0.0, ge=0, description="涉及变动金额")


class PolicyDetails(BaseModel):
    """匹配成功的政策详情"""
    policy_id: str
    title: str
    version: str
    effective_at: str
    source_id: str
    formula_rule: str


class EscalationPreview(BaseModel):
    """异常复核申请预览（生成无副作用预览）"""
    target_role: str = "supervisor"
    suggested_action: str
    reason: str


class PolicyCheckResponse(BaseModel):
    """客户端统一响应 Schema"""
    request_id: str
    status: StatusEnum
    eligible: bool = False
    policy_details: Optional[PolicyDetails] = None
    reason_code: Optional[ReasonCodeEnum] = None
    escalation_preview: Optional[EscalationPreview] = None


# ============================================================================
# Week 07 一线任务闭环 DTO (回链 specs/mission-week-07.md)
# 设计要点: TaskActionRequest 使用 extra="forbid" 封闭契约 —— 请求体自报
# role/身份字段会直接 422 失败，身份只允许来自服务端可信会话 (X-Auth-Token)。
# ============================================================================


class TaskStatus(str, Enum):
    """工单任务状态机 (task-state-model.md)"""
    INITIALIZED = "initialized"
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REFUSED = "refused"
    ESCALATED = "escalated"
    DENIED = "denied"                # 仅存在于动作审计 (越权阻断)，不改变工单状态
    ACTION_COMPLETED = "action_completed"


class TaskAction(str, Enum):
    """一线任务动作 (服务端 RBAC 授权的最小单元)"""
    CHECK_POLICY = "check_policy"
    CONFIRM = "confirm"
    REFUSE = "refuse"                # 验收标准第 4 条: 客服可显式拒绝 (adr-003 适配点 3)
    ESCALATE = "escalate"
    SUPERVISOR_RESOLVE = "supervisor_resolve"


class TaskActionReasonCode(str, Enum):
    """任务动作的结构化原因码"""
    SUPPORTED_CURRENT_SOURCES = "SUPPORTED_CURRENT_SOURCES"
    AMOUNT_EXCEEDS_LIMIT = "AMOUNT_EXCEEDS_LIMIT"
    NO_MATCHING_POLICY = "NO_MATCHING_POLICY"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    SPECIAL_TASK_REQUIRES_REVIEW = "SPECIAL_TASK_REQUIRES_REVIEW"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    REFUSED_BY_AGENT = "REFUSED_BY_AGENT"
    INVALID_TASK_STATE = "INVALID_TASK_STATE"


class TaskActionRequest(BaseModel):
    """任务动作请求 Schema (身份绝不来自本请求体)"""
    model_config = ConfigDict(extra="forbid")  # 自报 role 等未知字段直接 422

    task_id: str = Field(..., description="工单标识")
    action: TaskAction = Field(..., description="任务动作")
    correlation_id: str = Field(..., description="全局关联标识 (三向对账键)")
    reason_code: Optional[TaskActionReasonCode] = Field(
        default=None, description="escalate 可选自定义原因码 (缺省用任务当前原因)"
    )
    idempotency_key: Optional[str] = Field(
        default=None, description="升级动作幂等键 (重复提交返回 already_applied)"
    )


class TaskResponse(BaseModel):
    """任务动作统一响应 Schema (拒绝时 candidate_policy 恒为 None —— 零泄漏)"""
    task_id: str
    case_id: str
    status: TaskStatus
    reason_code: Optional[TaskActionReasonCode] = None
    candidate_policy: Optional[PolicyDetails] = None
    escalation_preview: Optional[EscalationPreview] = None
    allowed_actions: List[TaskAction] = []
    correlation_id: str
    message: Optional[str] = None
    is_idempotent: bool = False

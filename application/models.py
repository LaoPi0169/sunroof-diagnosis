"""
定义确定性垂直薄片的数据传输对象 (DTO) 与 Pydantic Schema 契约
完全回链至 Week 04 Discovery Brief v1 与 Week 05 接口契约 (api-contract/policy-check-v1.json)
"""
from enum import Enum
from typing import Optional

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

"""
定义装配规格核对薄片的数据传输对象 (DTO) 与 Pydantic Schema 契约
完全回链至 specs/discovery-brief-v1.md 与 api-contract/spec-check-v1.json (阶段 A 节点 02 冻结)
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class TaskType(str, Enum):
    SPEC_CHECK = "spec_check"


class ProcessStep(str, Enum):
    TORQUE_TIGHTENING = "torque_tightening"
    GAP_CHECK = "gap_check"


class StatusEnum(str, Enum):
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"
    DENIED = "denied"  # 保留值: 越权在身份层即被 401/ACL 排除, 本薄片不产生


class ReasonCodeEnum(str, Enum):
    MISSING_REQUIRED_PARAM = "MISSING_REQUIRED_PARAM"
    SPEC_HARD_LIMIT_EXCEEDED = "SPEC_HARD_LIMIT_EXCEEDED"
    NO_MATCHING_SPEC = "NO_MATCHING_SPEC"
    SPEC_CONFLICT = "SPEC_CONFLICT"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"


class TrustedUser(BaseModel):
    """服务端可信会话中提取的用户身份 DTO (ACL 范围随身份下发)"""
    user_id: str
    user_role: str  # 'pqe' / 'quality_supervisor'
    allowed_oem_projects: List[str]  # 例如 ["OEM-A"]; ["*"] 表示跨项目只读 (质量主管)


class SpecCheckRequest(BaseModel):
    """客户端请求 Schema (封闭契约: 自报 role 等未知字段直接 422)"""
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., description="三向对账键 (API 响应 / DB / 测试)")
    task_type: TaskType = Field(..., description="任务类型")
    sn: str = Field(..., description="天窗总成 SN (追溯标识)")
    part_no: str = Field(..., description="零件业务键 (如 MOTOR-MOUNT-BOLT-03)")
    process_step: ProcessStep = Field(..., description="工序封闭枚举")
    measured_at: str = Field(..., description="测量日期 (ISO-8601, 生效窗口判定键)")
    measured_value: float = Field(..., gt=0, description="实测值 (N·m 或 mm, 按工序)")


class SpecDetails(BaseModel):
    """匹配成功的现行有效规格出处 (answered 时返回, 供报告引用)"""
    spec_id: str
    title: str
    version: str
    effective_at: str
    expires_at: Optional[str] = None
    source_ref: str
    unit: str


class SpecEvaluation(BaseModel):
    """确定性偏差计算结果 (100% 公式可复算, 非模型生成)"""
    nominal_value: float
    tolerance: str  # 例如 "+0.5/-0.5"
    measured_value: float
    deviation_pct: float
    conformance: str  # 'conforming' / 'nonconforming'
    formula_rule: str


class ReviewPreview(BaseModel):
    """人工复核申请预览 (生成无副作用预览; 阻断路径绝不包含规格内容)"""
    target_role: str = "quality_supervisor"
    suggested_action: str
    reason: str


class SpecCheckResponse(BaseModel):
    """客户端统一响应 Schema (阻断路径 spec/evaluation 恒为 None —— 零泄漏)"""
    request_id: str
    status: StatusEnum
    conformance_passed: bool = False
    spec: Optional[SpecDetails] = None
    evaluation: Optional[SpecEvaluation] = None
    reason_code: Optional[ReasonCodeEnum] = None
    reviewer_preview: Optional[ReviewPreview] = None


# ============================================================================
# 节点 04 双角色任务闭环 DTO (回链 specs/role-and-permission-matrix.md §2:
# 冲突/硬限裁决.人工复核 —— PQE 发起 (needs_review), 质量主管原系统人工裁决)
# 设计要点: TaskActionRequest 使用 extra="forbid" 封闭契约 —— 请求体自报
# role/身份字段直接 422，身份只允许来自服务端可信会话 (X-Auth-Token)。
# ============================================================================


class TaskStatus(str, Enum):
    """任务状态机 (task-state-model.md)"""
    INITIALIZED = "initialized"
    ANSWERED = "answered"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    REFUSED = "refused"
    ESCALATED = "escalated"
    DENIED = "denied"                # 仅存在于动作审计 (越权阻断)，不改变任务状态
    ACTION_COMPLETED = "action_completed"


class TaskAction(str, Enum):
    """任务动作 (服务端 RBAC 授权的最小单元)"""
    CHECK_SPEC = "check_spec"        # 100% 复用节点 02 确定性引擎 (adr-003 D5)
    CONFIRM = "confirm"
    REFUSE = "refuse"                # 显式拒绝也是留痕终态 (不猜答案)
    ESCALATE = "escalate"
    SUPERVISOR_RESOLVE = "supervisor_resolve"


class TaskActionReasonCode(str, Enum):
    """任务动作的结构化原因码 (异常分支与节点 02 判定表同码, 零映射分叉)"""
    SUPPORTED_CURRENT_SOURCES = "SUPPORTED_CURRENT_SOURCES"
    SPEC_HARD_LIMIT_EXCEEDED = "SPEC_HARD_LIMIT_EXCEEDED"
    NO_MATCHING_SPEC = "NO_MATCHING_SPEC"
    SPEC_CONFLICT = "SPEC_CONFLICT"
    SPECIAL_TASK_REQUIRES_REVIEW = "SPECIAL_TASK_REQUIRES_REVIEW"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    REFUSED_BY_AGENT = "REFUSED_BY_AGENT"
    INVALID_TASK_STATE = "INVALID_TASK_STATE"


class TaskActionRequest(BaseModel):
    """任务动作请求 Schema (身份绝不来自本请求体)"""
    model_config = ConfigDict(extra="forbid")  # 自报 role 等未知字段直接 422

    task_id: str = Field(..., description="任务标识")
    action: TaskAction = Field(..., description="任务动作")
    correlation_id: str = Field(..., description="全局关联标识 (三向对账键)")
    reason_code: Optional[TaskActionReasonCode] = Field(
        default=None, description="escalate 可选自定义原因码 (缺省继承任务当前原因)"
    )
    idempotency_key: Optional[str] = Field(
        default=None, description="升级动作幂等键 (重复提交返回 ALREADY_APPLIED)"
    )


class TaskResponse(BaseModel):
    """任务动作统一响应 Schema (denied/needs_review 时 candidate_spec 恒为 None —— 零泄漏)"""
    task_id: str
    case_id: str
    status: TaskStatus
    reason_code: Optional[TaskActionReasonCode] = None
    candidate_spec: Optional[SpecDetails] = None
    evaluation: Optional[SpecEvaluation] = None
    escalation_preview: Optional[ReviewPreview] = None
    allowed_actions: List[TaskAction] = []
    correlation_id: str
    message: Optional[str] = None
    is_idempotent: bool = False

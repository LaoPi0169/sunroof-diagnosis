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


# ============================================================================
# 阶段 C 节点 ⑮: 只读 RAG 问答 + 会话身份 DTO (回链 generation/contract.py
# Week-12 冻结的 13 字段记录结构; 身份仍只来自服务端可信会话)
# ============================================================================


class RagAnswerRequest(BaseModel):
    """RAG 问答请求 Schema (封闭契约: 自报 role 等未知字段直接 422)"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=500,
                       description="自然语言问句 (门控决策树 + 抽取式替身, 零 LLM)")
    case_id: Optional[str] = Field(default=None, max_length=64,
                                   description="可选案例标识 (缺省 WB-MANUAL)")


class RagCitation(BaseModel):
    """引用条目 (Week-12 冻结四字段; answered 时非空)"""

    document_id: str
    chunk_id: str
    source_version: str
    effective_status: str


class RagAnswerResponse(BaseModel):
    """RAG 问答响应 (与 week-12 逐例结果逐字段同构):

    state 为三态 (answered/abstained/escalate), expected_task_state 为预注册的
    四态映射 (answered/needs_review/refused/escalated); business_effects 恒 0。
    """
    case_id: str
    user_role: str
    state: str
    expected_task_state: str
    answer_text: Optional[str] = None
    citations: List[RagCitation] = []
    reason_code: Optional[str] = None
    user_next_step: Optional[str] = None
    confidence_top1: float = 0.0
    restricted_candidate_count: int = 0
    business_effects: int = 0
    request_id: str
    generator_mode: str


class SessionIdentity(BaseModel):
    """当前可信会话身份回显 (调试用; 绝不回显令牌本身)"""

    user_id: str
    user_role: str
    allowed_oem_projects: List[str]


# ============================================================================
# 阶段 C 节点 ⑰: MES 生产记录抓取 (只读 MCP 工具) DTO —— 回链 api-contract/
# mes-lookup-v1.json 与 mcptool/mes_contract.py (封闭合同, 同值互锁;
# discovery-brief-v1.md In-Scope 第 1 条的阶段 C 兑现)。
# ============================================================================


class MesField(str, Enum):
    """MES 抓取字段封闭枚举 (与 mcptool.mes_contract.FIELD_ENUM 同值)"""
    ASSEMBLY_TORQUE = "assembly_torque"
    ECU_FLASH = "ecu_flash"
    SENSOR_WAVEFORM = "sensor_waveform"


class MesLookupRequest(BaseModel):
    """MES 抓取请求 Schema (封闭契约: 自报 role/身份等未知字段直接 422)"""

    model_config = ConfigDict(extra="forbid")

    sn: str = Field(..., pattern=r"^ROOF-\d{5}$",
                    description="天窗总成 SN (冻结语法 ^ROOF-\\d{5}$, 退返件条码教学格式)")
    fields: List[MesField] = Field(..., min_length=1, max_length=3,
                                   description="请求字段封闭枚举, 1..3 个 (重复由工具层 invalid_argument 拒绝)")
    segment: Optional[int] = Field(default=None, ge=0, le=3,
                                   description="波形分页段 (R-2 收口): 缺省 0; 每段 32 点 × 4 段")


class MesLookupResponse(BaseModel):
    """MES 抓取响应封套 (失败封套静态 —— 跨项目/未知 SN/角色不足零存在性泄漏)"""

    tool: str
    version: str
    effect: str
    run_id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[dict] = None
    business_effects: int = 0


# ============================================================================
# 阶段 C 节点 ⑱: PLM 工程记录查询 (只读 MCP 工具) DTO —— 回链 api-contract/
# plm-lookup-v1.json 与 mcptool/plm_contract.py (封闭合同, 同值互锁;
# discovery-brief-v1.md In-Scope 第 2 条 PLM 半边的阶段 C 兑现)。
# ============================================================================


class PLMField(str, Enum):
    """PLM 查询字段封闭枚举 (与 mcptool.plm_contract.FIELD_ENUM 同值)"""
    BOM_SNAPSHOT = "bom_snapshot"
    ECO_HISTORY = "eco_history"
    DRAWING_REGISTRY = "drawing_registry"


class PLMLookupRequest(BaseModel):
    """PLM 查询请求 Schema (封闭契约: 自报 role/身份等未知字段直接 422)"""

    model_config = ConfigDict(extra="forbid")

    part_no: str = Field(..., pattern=r"^SR-[A-Z]{3,10}-[AB]\d{2}$",
                         description="天窗零件号 (冻结语法 ^SR-[A-Z]{3,10}-[AB]\\d{2}$, 教学格式)")
    fields: List[PLMField] = Field(..., min_length=1, max_length=3,
                                   description="请求字段封闭枚举, 1..3 个 (重复由工具层 invalid_argument 拒绝)")
    eco_offset: Optional[int] = Field(default=None, ge=0, le=12,
                                      description="ECO 历史分页偏移: 缺省 0; 每页最多 4 条 (冻结上限)")


class PLMLookupResponse(BaseModel):
    """PLM 查询响应封套 (失败封套静态 —— 跨项目/未知件号/角色不足零存在性泄漏)"""

    tool: str
    version: str
    effect: str
    run_id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[dict] = None
    business_effects: int = 0


# ============================================================================
# 阶段 C 节点 ⑲: QMS 检验标准查询 (只读 MCP 工具) DTO —— 回链 api-contract/
# qms-lookup-v1.json 与 mcptool/qms_contract.py (封闭合同, 同值互锁;
# discovery-brief-v1.md In-Scope 第 2 条 QMS 半边的阶段 C 兑现, P-2 现行过滤)。
# ============================================================================


class QMSField(str, Enum):
    """QMS 查询字段封闭枚举 (与 mcptool.qms_contract.FIELD_ENUM 同值)"""
    SOP_CURRENT = "sop_current"
    QUALITY_STANDARD = "quality_standard"
    INSPECTION_CHECKLIST = "inspection_checklist"


class QMSLookupRequest(BaseModel):
    """QMS 查询请求 Schema (封闭契约: 自报 role/身份等未知字段直接 422)"""

    model_config = ConfigDict(extra="forbid")

    part_no: str = Field(..., pattern=r"^SR-[A-Z]{3,10}-[AB]\d{2}$",
                         description="天窗零件号 (冻结语法, 教学格式)")
    process_step: str = Field(..., pattern=r"^(torque_tightening|gap_check)$",
                              description="工序封闭枚举 (specs/data-contract-v1.md 计量约定)")
    fields: List[QMSField] = Field(..., min_length=1, max_length=3,
                                   description="请求字段封闭枚举, 1..3 个 (重复由工具层 invalid_argument 拒绝)")


class QMSLookupResponse(BaseModel):
    """QMS 查询响应封套 (失败封套静态 —— 跨项目/未知件号/角色不足零存在性泄漏)"""

    tool: str
    version: str
    effect: str
    run_id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[dict] = None
    business_effects: int = 0


# ============================================================================
# 阶段 C 节点 ⑳: 根因分析报告草稿 (只读聚合 MCP 工具) DTO —— 回链 api-contract/
# report-draft-v1.json 与 mcptool/report_contract.py (封闭合同, 同值互锁;
# discovery-brief-v1.md In-Scope 第 3 条的阶段 C 兑现: 结构化带出处引用草稿)。
# ============================================================================


class ReportDraftRequest(BaseModel):
    """报告草稿请求 Schema (封闭契约: 自报 role/身份等未知字段直接 422)"""

    model_config = ConfigDict(extra="forbid")

    sn: str = Field(..., pattern=r"^ROOF-\d{5}$",
                    description="天窗总成 SN (与 MES 合同同值, 冻结语法)")
    part_no: str = Field(..., pattern=r"^SR-[A-Z]{3,10}-[AB]\d{2}$",
                         description="天窗零件号 (与 QMS/PLM 合同同值, 冻结语法)")
    process_step: str = Field(..., pattern=r"^(torque_tightening|gap_check)$",
                              description="聚焦工序 (封闭枚举)")


class ReportDraftResponse(BaseModel):
    """报告草稿响应封套 (失败封套静态 —— 三因一致零存在性泄漏)"""

    tool: str
    version: str
    effect: str
    run_id: str
    ok: bool
    result: Optional[dict] = None
    error: Optional[dict] = None
    business_effects: int = 0


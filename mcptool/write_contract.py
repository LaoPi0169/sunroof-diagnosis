"""ticket.* 写工具合同 (Week 14 + Week 15 对抗增量) —— 回链 week-15/ticket-write-contract-v2.json。

唯一允许的外部效果: 在隔离模拟工单服务为指定工单创建 1 张主管复核单
(effect = create_one_review_ticket, 等级 write_once_reversible)。
硬门: 无审批 / 审批过期/撤销 / 能力隔离 / 身份或参数不匹配 -> 外部效果数必须为 0。

Week 15 增量 (A-01..A-07, specs/business-background.md 3438-3665):
  - 审批执行前可撤销: approval_revoked 错误码 + ApprovalService.revoke (追加式);
  - 工具描述/参数模式摘要门: capability_quarantined 错误码 + capability_manifest 模块;
  - 写后断连: result_unknown 编排状态 + reconciliation_pending (先查后说, 禁止伪造终态);
  - 运营补偿: cancel_ticket 仅 ops_admin, 不进模型目录 (见 ticket_service)。

三条物理边界 (模块切分, 手册 3360-3364):
  - write_contract (本模块): 参数封闭 schema + 单一规范化函数 —— 预览/审批/执行
    三处共用同一函数, 规范化前后必须指向同一动作 (大小写/空格/顺序不产生新审批);
  - ticket_service: 隔离模拟服务独立写 store (与只读 case_store 物理分离);
  - approval + policy: 结构化审批记录 (禁止读自然语言当批准) + 模型外策略组件。

模型只能提议 (arguments 四键), 不能产生效果; approval_id 由编排层/执行器传入,
不是模型可提供的参数 (模型携带审批 ID 也是走私, schema 层拒绝)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import contract as C

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def iso_plus_seconds(iso_str: str, seconds: int) -> str:
    """ISO-8601 Z 定长时间 + 秒 (审批有效期计算; 字符串定长可直接比较)。"""
    moment = datetime.strptime(iso_str, ISO_FORMAT)
    return (moment + timedelta(seconds=seconds)).strftime(ISO_FORMAT)

# ---- 三件写工具 (Host 允许列表 1 -> 4; 读工具仍走原通道; cancel 不入列) ----
TOOL_PREVIEW = "ticket.preview_create"
TOOL_CREATE = "ticket.create"
TOOL_GET = "ticket.get"
WRITE_TOOLS = (TOOL_PREVIEW, TOOL_CREATE, TOOL_GET)
TOOL_VERSION = "1"

EFFECT = "create_one_review_ticket"
EFFECT_CLASS = "write_once_reversible"
SERVER_IDENTITY = "northstar-mcptool-ticket-server@local"

# 合同冻结互锁 (week-15/ticket-write-contract-v2.json 字节级 sha256;
# 任一方改动而另一方未同步, 互锁测试即失败; week-14 v1 冻结物一字节不动,
# 其摘要 486b9362…b1b9b4 由 tests/test_mcp_adversarial.py 锁定)
CONTRACT_SHA256 = "f9afabdc2efdab5e10c20dda23a2d64b16223b88cc443bdcabffa1a65fe931f4"

# ---- 参数封闭 schema (四键; additional_properties=reject) ----
WRITE_ARGUMENT_KEYS = ("case_id", "reason_code", "source_refs", "target_queue")
REASON_CODES = ("POLICY_CONFLICT",)
# 队列语法枚举 (schema 层封闭): executive-review 是世界里的真实队列, 合法语法;
# 策略层只放行 billing-supervisor-review —— 因此「审批后把队列改成 executive-review」
# 越过 schema 到达审批比对层, 得到 approval_mismatch (手册 W-03 期望), 而非 invalid_argument。
QUEUE_SYNTAX_ENUM = ("billing-supervisor-review", "executive-review")
SOURCE_REFS_MIN, SOURCE_REFS_MAX = 1, 2

# ---- 上限与时效 ----
RESULT_LIMIT_TICKETS_PER_APPROVAL = 1
DEADLINE_MS = 800  # 写路径 deadline 宽于读路径 500ms (写含核验再读)

# ---- 结果封套状态 (状态机见 week-15/ticket-write-contract-v2.json terminals) ----
STATUS_PREVIEW = "action_preview"        # 0 个外部效果
STATUS_DENIED = "approval_denied"        # 安全终态, 0 个外部效果
STATUS_COMPLETED = "action_completed"    # 已由 ticket.get 独立核验, 1 个效果
STATUS_UNKNOWN = "result_unknown"        # 写后断连: 已提交、结果未核验 (A-06)
STATUS_RECONCILIATION_PENDING = "reconciliation_pending"  # 对账未完成: 禁止伪造终态
RECONCILIATION_PENDING_MESSAGE = ("写请求已提交但结果未确认，系统保持对账状态；"
                                  "确认前不会重试创建，也不会伪造完成")

# ---- 第 17 周降级停驻 (用户面封套增补; 回链 week-17/failure-behavior-contract-v1.md) ----
# MCP 不可用 (演练注入或 MCP_OFF 关闭开关) 时写编排停在预览: 同一规范化参数原样
# 保留, 外部效果恒 0, 审批不在此消耗 (深层缝除外, 见 client._degraded_preview),
# 用户可复制预览走人工流程, 或稍后用同一幂等键续跑 (不自动重试, 决策留给用户)。
REASON_MCP_UNAVAILABLE = "mcp_unavailable"                # 用户面细分原因码 (唯一)
ALLOWED_NEXT_ACTIONS = ("copy_preview", "submit_manually", "retry_later")
DEGRADED_PREVIEW_MESSAGE = ("写通道暂不可用：工单预览已保留但尚未创建；"
                            "可复制预览走人工流程提交，或稍后重试")

# ---- 错误十类 (与合同 JSON error_taxonomy 同值互锁; v1 八类 + v2 两类) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_DEADLINE_EXCEEDED = "deadline_exceeded"
ERROR_APPROVAL_REQUIRED = "approval_required"
ERROR_APPROVAL_EXPIRED = "approval_expired"
ERROR_APPROVAL_MISMATCH = "approval_mismatch"
ERROR_CONTRACT_VIOLATION = "contract_violation"
ERROR_VERSION_MISMATCH = "version_mismatch"
ERROR_APPROVAL_REVOKED = "approval_revoked"                # v2: 执行前撤销 (A-04)
ERROR_CAPABILITY_QUARANTINED = "capability_quarantined"    # v2: 描述/模式摘要隔离 (A-02/A-05)
ERROR_CODES = (
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_AVAILABLE,
    ERROR_DEADLINE_EXCEEDED,
    ERROR_APPROVAL_REQUIRED,
    ERROR_APPROVAL_EXPIRED,
    ERROR_APPROVAL_MISMATCH,
    ERROR_CONTRACT_VIOLATION,
    ERROR_VERSION_MISMATCH,
    ERROR_APPROVAL_REVOKED,
    ERROR_CAPABILITY_QUARANTINED,
)

# ---- 用户面安全文案 (细分原因只进受限审计; 不泄露审批内部细节) ----
SAFE_MESSAGES = {
    ERROR_INVALID_ARGUMENT: "写请求参数不允许",
    ERROR_NOT_AVAILABLE: "对象不可用或无权访问",
    ERROR_DEADLINE_EXCEEDED: "写操作超时，外部效果未确认，请先对账再决定下一步",
    ERROR_APPROVAL_REQUIRED: "需要有效审批后才能执行",
    ERROR_APPROVAL_EXPIRED: "审批已过期，请重新预览并确认",
    ERROR_APPROVAL_MISMATCH: "审批与本次动作不匹配，已拒绝执行",
    ERROR_CONTRACT_VIOLATION: "结果超出合同，已整体拒绝并记录安全事件",
    ERROR_VERSION_MISMATCH: "工具版本不兼容，本次调用已停用",
    ERROR_APPROVAL_REVOKED: "审批已被撤销，本次执行已停止",
    ERROR_CAPABILITY_QUARANTINED: "工具能力与冻结清单不符，已隔离停用",
}

# ---- 审批绑定字段 (执行前逐项重读比对; 任一实质变化即原审批失效) ----
APPROVAL_BINDING_FIELDS = (
    "requester", "tenant_id", "run_id", "tool", "tool_version",
    "approved_parameters", "resource_scope", "policy_version",
)


def idempotency_key(run_id: str) -> str:
    """稳定幂等键: {run_id}:{tool}:{version} (手册 3289 同构)。"""
    return f"{run_id}:{TOOL_CREATE}:{TOOL_VERSION}"


def resource_scope(case_id: str) -> str:
    """审批资源范围: 一次审批只对一张工单有效。"""
    return f"case:{case_id}"


def validate_write_arguments(arguments):
    """封闭校验 + 单一规范化 (Host 与服务端各调一次, 纵深防御)。

    规则: arguments 必须是对象; 可信会话键走私即拒绝; 四键之外 (含 approval_id/
    metadata/role 等) 一律拒绝; reason_code 大写化、target_queue 小写化、
    source_refs 逐项去空格去重排序 —— 规范化在策略判断/展示/审批之前完成。
    返回 (canonical, None) 或 (None, detail)。
    """
    if not isinstance(arguments, dict):
        return None, "arguments 必须是对象"
    for key in C.TRUSTED_CONTEXT_KEYS:
        if key in arguments:
            return None, f"{key} 属于可信会话，不得作为调用参数"
    extra = sorted(set(arguments) - set(WRITE_ARGUMENT_KEYS))
    if extra:
        return None, "写请求参数不允许: " + ", ".join(extra)
    missing = sorted(set(WRITE_ARGUMENT_KEYS) - set(arguments))
    if missing:
        return None, "缺少必填参数: " + ", ".join(missing)

    case_id = arguments["case_id"]
    if not isinstance(case_id, str) or not case_id.strip():
        return None, "case_id 必须是非空字符串"

    reason_code = arguments["reason_code"]
    if not isinstance(reason_code, str) or reason_code.strip().upper() not in REASON_CODES:
        return None, "原因代码不在封闭枚举"

    refs = arguments["source_refs"]
    if not isinstance(refs, list) or not (SOURCE_REFS_MIN <= len(refs) <= SOURCE_REFS_MAX):
        return None, f"source_refs 数量必须在 {SOURCE_REFS_MIN}..{SOURCE_REFS_MAX}"
    cleaned_refs = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            return None, "source_refs 必须是非空字符串"
        cleaned_refs.append(ref.strip())
    if len(set(cleaned_refs)) != len(cleaned_refs):
        return None, "source_refs 不得重复"

    queue = arguments["target_queue"]
    if not isinstance(queue, str) or queue.strip().lower() not in QUEUE_SYNTAX_ENUM:
        return None, "目标队列不在封闭枚举"

    canonical = {
        "case_id": case_id.strip(),
        "reason_code": reason_code.strip().upper(),
        "source_refs": sorted(cleaned_refs),
        "target_queue": queue.strip().lower(),
    }
    return canonical, None
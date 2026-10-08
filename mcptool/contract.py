"""case.lookup_context 只读工具合同 (Week 13) —— 回链 week-13/mcp-read-contract-v1.json。

MCP 风格 Host/Client/Server 三方切分 (教学实现, 进程内确定性, 无网络无 SDK):
  - 身份永远来自可信会话 (tenant/user/role 由 Host 从已认证会话注入);
  - 调用方只能给 arguments (case_id + fields), 走私身份参数一律 invalid_argument;
  - 服务端对每次调用二次鉴权 (租户 x 角色 x 对象 x 字段), 不信任 Host 转述;
  - 结果上限 1 记录 / 3 字段, 客户端按合同校验结果封套 (超合同整体拒绝);
  - 五类错误与 JSON 合同同值互锁;
  - 外部写效果恒为 0 (只读; 仓库无任何变更入口)。
"""
from __future__ import annotations

TOOL_NAME = "case.lookup_context"
TOOL_VERSION = "1"
EFFECT = "read_only"
SERVER_IDENTITY = "northstar-mcptool-case-server@local"
HOST_IDENTITY = "northstar-mcptool-host@local"

# ---- 字段封闭枚举 (1..3 个; 禁止字段永不在枚举内) ----
FIELD_ENUM = ("task_type", "region", "purchased_at")
FIELDS_MIN, FIELDS_MAX = 1, 3
FORBIDDEN_FIELDS = ("supervisor_note", "customer_name", "payment_detail")

# ---- 可信会话 (身份来源; 不得作为调用参数) ----
TRUSTED_CONTEXT_KEYS = ("tenant_id", "user_id", "role")
ALLOWED_ROLES = ("support_agent", "supervisor")

# ---- 结果与时效上限 ----
RESULT_LIMIT_RECORDS = 1
RESULT_LIMIT_FIELDS = 3
DEADLINE_MS = 500

# ---- 五类错误 (与合同 JSON error_taxonomy 同值互锁) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_DEADLINE_EXCEEDED = "deadline_exceeded"
ERROR_CONTRACT_VIOLATION = "contract_violation"
ERROR_VERSION_MISMATCH = "version_mismatch"
ERROR_CODES = (
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_AVAILABLE,
    ERROR_DEADLINE_EXCEEDED,
    ERROR_CONTRACT_VIOLATION,
    ERROR_VERSION_MISMATCH,
)

# ---- 用户面安全文案 (不含对象存在性/越权细节; 细分原因只进受限审计) ----
SAFE_MESSAGES = {
    ERROR_INVALID_ARGUMENT: "请求字段不允许",
    ERROR_NOT_AVAILABLE: "对象不可用或无权访问",
    ERROR_DEADLINE_EXCEEDED: "暂不可用，请稍后重试或转人工",
    ERROR_CONTRACT_VIOLATION: "结果超出合同，已整体拒绝并记录安全事件",
    ERROR_VERSION_MISMATCH: "工具版本不兼容，本次调用已停用",
}

# ---- 工单系统 task_type 词表 <-> 评测集 task_context.category 冻结映射 ----
# (回归对账桥: 工具取回 task_type 经此映射还原 category, 与 eval-set 转写逐字段对账)
TASK_TYPE_TO_CATEGORY = {
    "billing_general_inquiry": "billing_general",
    "plan_upgrade_price_difference": "price_difference_east",
    "out_of_scope_inquiry": "outside_corpus",
    "upgrade_effective_date_conflict": "upgrade_conflict",
    "legacy_policy_reference_check": "expired_reference",
    "special_refund_request": "restricted_refund",
}


def validate_arguments(arguments):
    """封闭校验 (Host 与服务端各做一次, 纵深防御)。

    规则: additional_properties=reject; trusted 键走私即拒绝;
    fields 取自封闭枚举且数量 1..3、不重复; case_id 非空字符串。
    返回 (case_id, fields, None) 或 (None, None, (error_code, detail))。
    """
    if not isinstance(arguments, dict):
        return None, None, (ERROR_INVALID_ARGUMENT, "arguments 必须是对象")
    for key in TRUSTED_CONTEXT_KEYS:
        if key in arguments:
            return None, None, (ERROR_INVALID_ARGUMENT,
                                f"{key} 属于可信会话，不得作为调用参数")
    allowed_keys = {"case_id", "fields"}
    extra = sorted(set(arguments) - allowed_keys)
    if extra:
        return None, None, (ERROR_INVALID_ARGUMENT,
                            "请求字段不允许: " + ", ".join(extra))
    if "case_id" not in arguments or "fields" not in arguments:
        return None, None, (ERROR_INVALID_ARGUMENT, "缺少必填参数 case_id/fields")
    case_id = arguments["case_id"]
    fields = arguments["fields"]
    if not isinstance(case_id, str) or not case_id.strip():
        return None, None, (ERROR_INVALID_ARGUMENT, "case_id 必须是非空字符串")
    if not isinstance(fields, list) or not (FIELDS_MIN <= len(fields) <= FIELDS_MAX):
        return None, None, (ERROR_INVALID_ARGUMENT,
                            f"fields 数量必须在 {FIELDS_MIN}..{FIELDS_MAX}")
    if len(set(fields)) != len(fields):
        return None, None, (ERROR_INVALID_ARGUMENT, "fields 不得重复")
    for field in fields:
        if field not in FIELD_ENUM:
            return None, None, (ERROR_INVALID_ARGUMENT, "请求字段不允许")
    return case_id, list(fields), None

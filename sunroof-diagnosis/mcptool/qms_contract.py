"""qms.lookup_inspection_standard 只读工具合同（阶段 C 节点 ⑲）—— 回链 api-contract/qms-lookup-v1.json。

discovery-brief-v1.md In-Scope 第 2 条 QMS 半边的阶段 C 兑现: 按零件号 + 工序经只读
MCP 通道匹配 QMS 中当前有效的检验 SOP / 质量标准 / 检验要点清单。设计沿用节点
⑰⑱ mes/plm 只读工具既定模式 (身份只来自可信会话 / 封闭字段枚举 / 用户面失败封套
逐字节一致 / 只读零效果), 不改冻结的 billing 工具语义 (仅追加工具):
  * 身份永远来自可信会话 (tenant/user/role/allowed_projects 由网关从 X-Auth-Token
    注入); 参数走私身份键一律 invalid_argument;
  * part_no 语法封闭: ^SR-[A-Z]{3,10}-[AB]\\d{2}$ (教学格式), 不做大小写归一;
  * process_step 封闭枚举: torque_tightening / gap_check (specs/data-contract-v1.md
    工序计量约定, 与阶段 A 节点 ② spec-check 的 ProcessStep 枚举同值);
  * P-2 保护指标 (引用过期/已被替代 SOP = 0 次): store 中被替代行 (is_current=0)
    在取数层被排除 (SQL WHERE is_current=1 语义), 被替代版本标识永不出现在响应;
    仅存在被替代行时返回字段级结构化 not_available (reason_code=NO_CURRENT_SOP);
  * P-3: 已知记录的缺失字段返回字段级结构化 not_available (reason_code=FIELD_NOT_
    AVAILABLE), 绝不编造数据;
  * P-1: 跨项目组与未知件号的用户面封套逐字节一致 (零存在性泄漏), 细分原因只进
    受限审计 (由调用方注入 audit_sink 记录, 不进用户面响应);
  * 外部写效果恒 0: 本模块与 qms_store.py 均无任何写入口 (测试以 store 指纹前后
    对照复验只读性质, 呼应 ci-evidence §9.3 教训)。

与 JSON 合同同值互锁: FIELD_ENUM / PART_NO_PATTERN / PROCESS_STEP_ENUM / 错误词表
与 api-contract/qms-lookup-v1.json tool_binding 逐项一致 (tests/test_qms_readonly_mcp.py
test_12 断言)。
"""
from __future__ import annotations

import re

TOOL_NAME = "qms.lookup_inspection_standard"
TOOL_VERSION = "1"
EFFECT = "read_only"
SERVER_IDENTITY = "sunroof-qms-server@local"

# ---- 字段封闭枚举 (1..3 个, 不得重复) ----
FIELD_ENUM = ("sop_current", "quality_standard", "inspection_checklist")
FIELDS_MIN, FIELDS_MAX = 1, 3

# ---- part_no 语法 (封闭; 不归一化) ----
PART_NO_PATTERN = r"^SR-[A-Z]{3,10}-[AB]\d{2}$"
_PART_RE = re.compile(PART_NO_PATTERN)

# ---- 工序封闭枚举 (specs/data-contract-v1.md 计量约定) ----
PROCESS_STEP_ENUM = ("torque_tightening", "gap_check")

# ---- P-2 取数纪律标记 (is_current=0 行在取数层排除) ----
CURRENT_ONLY_POLICY = "current_only"

# ---- 可信会话 (身份来源; 不得作为调用参数) ----
TRUSTED_CONTEXT_KEYS = ("tenant", "user", "role", "allowed_projects")
ALLOWED_ROLES = ("pqe", "quality_supervisor")

# ---- 错误词表 (与 JSON 合同 error_taxonomy 同值互锁) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_CODES = (ERROR_INVALID_ARGUMENT, ERROR_NOT_AVAILABLE)

# ---- 字段级缺失的结构化原因 (P-3 / P-2) ----
FIELD_NOT_AVAILABLE = "FIELD_NOT_AVAILABLE"
NO_CURRENT_SOP = "NO_CURRENT_SOP"  # 仅存在被替代版本行 -> 结构化拒引 (P-2)

ALLOWED_ARGUMENT_KEYS = ("part_no", "process_step", "fields")


def validate_arguments(arguments):
    """封闭校验 (网关与服务端共用一份; 纵深防御)。

    规则: additional_properties=reject; trusted 键走私即拒绝; part_no 语法封闭;
    process_step 取封闭枚举; fields 取自封闭枚举且数量 1..3、不重复。
    返回 (part_no, process_step, fields, None) 或 (None, None, None, (error_code, detail))。
    """
    if not isinstance(arguments, dict):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "arguments 必须是对象")
    for key in TRUSTED_CONTEXT_KEYS:
        if key in arguments:
            return None, None, None, (ERROR_INVALID_ARGUMENT,
                                      f"{key} 属于可信会话, 不得作为调用参数")
    extra = sorted(set(arguments) - set(ALLOWED_ARGUMENT_KEYS))
    if extra:
        return None, None, None, (ERROR_INVALID_ARGUMENT, "请求字段不允许: " + ", ".join(extra))
    if ("part_no" not in arguments or "process_step" not in arguments
            or "fields" not in arguments):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "缺少必填参数 part_no/process_step/fields")
    part_no = arguments["part_no"]
    process_step = arguments["process_step"]
    fields = arguments["fields"]
    if not isinstance(part_no, str) or not _PART_RE.match(part_no):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "part_no 语法不符合冻结合同 ^SR-[A-Z]{3,10}-[AB]\\d{2}$")
    if process_step not in PROCESS_STEP_ENUM:
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "process_step 必须取封闭枚举 torque_tightening/gap_check")
    if not isinstance(fields, list) or not (FIELDS_MIN <= len(fields) <= FIELDS_MAX):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  f"fields 数量必须在 {FIELDS_MIN}..{FIELDS_MAX}")
    if len(set(fields)) != len(fields):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "fields 不得重复")
    for field in fields:
        if field not in FIELD_ENUM:
            return None, None, None, (ERROR_INVALID_ARGUMENT, "请求字段不允许")
    return part_no, process_step, list(fields), None
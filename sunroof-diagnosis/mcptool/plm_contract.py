"""plm.lookup_engineering_record 只读工具合同（阶段 C 节点 ⑱）—— 回链 api-contract/plm-lookup-v1.json。

discovery-brief-v1.md In-Scope 第 2 条 PLM 半边的阶段 C 兑现: 按零件号经只读
MCP 通道调取 PLM 中的 BOM 快照 / ECO 工程变更历史 / 图纸登记元数据。设计沿用
节点 ⑰ mes.lookup_production_record 既定模式 (身份只来自可信会话 / 封闭字段枚举 /
用户面失败封套逐字节一致 / 只读零效果), 不改冻结的 billing 工具语义 (仅追加工具):
  * 身份永远来自可信会话 (tenant/user/role/allowed_projects 由网关从 X-Auth-Token
    注入); 参数走私身份键一律 invalid_argument;
  * part_no 语法封闭: ^SR-[A-Z]{3,10}-[AB]\\d{2}$ (教学格式), 不做大小写归一;
  * 载料上限冻结 (呼应 R-2 分页纪律): ECO 历史每页最多 4 条 (eco_offset 0..12),
    响应携带 total_changes/returned_changes/truncated/next_offset, 绝不全量回传;
  * P-1 图纸硬门槛 (权限矩阵 §2『跨 OEM 敏感图纸.展示』): 本工具只返回图纸登记
    元数据 (图号/版本/标题/状态) 且 content_disclosure=denied_by_policy ——
    图纸内容/文件/链接/渲染数据永不出工具 (主管亦不展示);
  * P-3: 已知记录的缺失字段返回字段级结构化 not_available (reason_code=FIELD_NOT_
    AVAILABLE), 绝不编造数据;
  * P-1: 跨项目组与未知件号的用户面封套逐字节一致 (零存在性泄漏), 细分原因只进
    受限审计 (由调用方注入 audit_sink 记录, 不进用户面响应);
  * 外部写效果恒 0: 本模块与 plm_store.py 均无任何写入口 (测试以 store 指纹前后
    对照复验只读性质, 呼应 ci-evidence §9.3 教训)。

与 JSON 合同同值互锁: FIELD_ENUM / PART_NO_PATTERN / ECO 上限 / 错误词表 与
api-contract/plm-lookup-v1.json tool_binding 逐项一致 (tests/test_plm_readonly_mcp.py
test_11 断言)。
"""
from __future__ import annotations

import re

TOOL_NAME = "plm.lookup_engineering_record"
TOOL_VERSION = "1"
EFFECT = "read_only"
SERVER_IDENTITY = "sunroof-plm-server@local"

# ---- 字段封闭枚举 (1..3 个, 不得重复) ----
FIELD_ENUM = ("bom_snapshot", "eco_history", "drawing_registry")
FIELDS_MIN, FIELDS_MAX = 1, 3

# ---- part_no 语法 (封闭; 不归一化) ----
PART_NO_PATTERN = r"^SR-[A-Z]{3,10}-[AB]\d{2}$"
_PART_RE = re.compile(PART_NO_PATTERN)

# ---- ECO 历史分页冻结上限 (呼应 R-2 载料纪律: 绝不全量回传) ----
ECO_MAX_RETURN = 4
ECO_OFFSET_MAX = 12

# ---- 可信会话 (身份来源; 不得作为调用参数) ----
TRUSTED_CONTEXT_KEYS = ("tenant", "user", "role", "allowed_projects")
ALLOWED_ROLES = ("pqe", "quality_supervisor")

# ---- 错误词表 (与 JSON 合同 error_taxonomy 同值互锁) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_CODES = (ERROR_INVALID_ARGUMENT, ERROR_NOT_AVAILABLE)

# ---- 字段级缺失的结构化原因 (P-3) ----
FIELD_NOT_AVAILABLE = "FIELD_NOT_AVAILABLE"

# ---- 图纸内容披露策略 (权限矩阵 §2 硬门槛: 元数据可查, 内容永不出工具) ----
DRAWING_CONTENT_DISCLOSURE = "denied_by_policy"

ALLOWED_ARGUMENT_KEYS = ("part_no", "fields", "eco_offset")


def validate_arguments(arguments):
    """封闭校验 (网关与服务端共用一份; 纵深防御)。

    规则: additional_properties=reject; trusted 键走私即拒绝; part_no 语法封闭;
    fields 取自封闭枚举且数量 1..3、不重复; eco_offset 缺省 0, 必须落在 0..12。
    返回 (part_no, fields, eco_offset, None) 或 (None, None, None, (error_code, detail))。
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
    if "part_no" not in arguments or "fields" not in arguments:
        return None, None, None, (ERROR_INVALID_ARGUMENT, "缺少必填参数 part_no/fields")
    part_no = arguments["part_no"]
    fields = arguments["fields"]
    if not isinstance(part_no, str) or not _PART_RE.match(part_no):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "part_no 语法不符合冻结合同 ^SR-[A-Z]{3,10}-[AB]\\d{2}$")
    if not isinstance(fields, list) or not (FIELDS_MIN <= len(fields) <= FIELDS_MAX):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  f"fields 数量必须在 {FIELDS_MIN}..{FIELDS_MAX}")
    if len(set(fields)) != len(fields):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "fields 不得重复")
    for field in fields:
        if field not in FIELD_ENUM:
            return None, None, None, (ERROR_INVALID_ARGUMENT, "请求字段不允许")
    eco_offset = arguments.get("eco_offset", 0)
    if isinstance(eco_offset, bool) or not isinstance(eco_offset, int):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "eco_offset 必须是整数")
    if not (0 <= eco_offset <= ECO_OFFSET_MAX):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  f"eco_offset 必须在 0..{ECO_OFFSET_MAX}")
    return part_no, list(fields), eco_offset, None
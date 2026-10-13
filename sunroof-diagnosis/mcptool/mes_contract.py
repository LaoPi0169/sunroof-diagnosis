"""mes.lookup_production_record 只读工具合同（阶段 C 节点 ⑰）—— 回链 api-contract/mes-lookup-v1.json。

discovery-brief-v1.md In-Scope 第 1 条的阶段 C 兑现: 按天窗 SN 经只读 MCP 通道
抓取 MES 生产记录 (装配扭矩 / ECU 刷写 / 传感器波形)。设计沿用 Week-13
case.lookup_context 的既定模式 (身份只来自可信会话 / 封闭字段枚举 / 用户面失败
封套逐字节一致 / 只读零效果), 不改冻结的 billing 工具语义 (仅追加工具):
  * 身份永远来自可信会话 (tenant/user/role/allowed_projects 由网关从 X-Auth-Token
    注入); 参数走私身份键一律 invalid_argument;
  * SN 语法封闭: ^ROOF-\\d{5}$ (退返件条码教学格式), 不做大小写归一 —— 语法不对即拒;
  * R-2 收口 (波形分页/裁剪): 波形按冻结上限降采样分页 —— 全量 128 点, 每段 32 点
    × 4 段, 响应携带 segment/truncated/next_segment; 任何波形载荷永不超过单段上限;
  * P-3: 已知记录的缺失字段返回字段级结构化 not_available (reason_code=FIELD_NOT_
    AVAILABLE), 绝不编造数据;
  * P-1: 跨项目组与未知 SN 的用户面封套逐字节一致 (零存在性泄漏), 细分原因只进
    受限审计 (由调用方注入 audit_sink 记录, 不进用户面响应);
  * 外部写效果恒 0: 本模块与 mes_store.py 均无任何写入口 (测试以 store 指纹前后
    对照复验只读性质, 呼应 ci-evidence §9.3 根因 5 教训: 自愈/读取类改动以冻结物
    指纹前后对照为标准回归检查)。

与 JSON 合同同值互锁: FIELD_ENUM / SN_PATTERN / 波形上限 / 错误词表 与
api-contract/mes-lookup-v1.json tool_binding 逐项一致 (tests/test_mes_readonly_mcp.py
test_11 断言)。
"""
from __future__ import annotations

import re

TOOL_NAME = "mes.lookup_production_record"
TOOL_VERSION = "1"
EFFECT = "read_only"
SERVER_IDENTITY = "sunroof-mes-server@local"

# ---- 字段封闭枚举 (1..3 个, 不得重复) ----
FIELD_ENUM = ("assembly_torque", "ecu_flash", "sensor_waveform")
FIELDS_MIN, FIELDS_MAX = 1, 3

# ---- SN 语法 (封闭; 不归一化) ----
SN_PATTERN = r"^ROOF-\d{5}$"
_SN_RE = re.compile(SN_PATTERN)

# ---- R-2 波形分页冻结上限 ----
WAVEFORM_TOTAL_POINTS = 128
WAVEFORM_SEGMENT_POINTS = 32
WAVEFORM_SEGMENTS = WAVEFORM_TOTAL_POINTS // WAVEFORM_SEGMENT_POINTS  # = 4

# ---- 可信会话 (身份来源; 不得作为调用参数) ----
TRUSTED_CONTEXT_KEYS = ("tenant", "user", "role", "allowed_projects")
ALLOWED_ROLES = ("pqe", "quality_supervisor")

# ---- 错误词表 (与 JSON 合同 error_taxonomy 同值互锁) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_CODES = (ERROR_INVALID_ARGUMENT, ERROR_NOT_AVAILABLE)

# ---- 字段级缺失的结构化原因 (P-3) ----
FIELD_NOT_AVAILABLE = "FIELD_NOT_AVAILABLE"

ALLOWED_ARGUMENT_KEYS = ("sn", "fields", "segment")


def validate_arguments(arguments):
    """封闭校验 (网关与服务端共用一份; 纵深防御)。

    规则: additional_properties=reject; trusted 键走私即拒绝; sn 语法封闭;
    fields 取自封闭枚举且数量 1..3、不重复; segment 缺省 0, 必须落在 0..3。
    返回 (sn, fields, segment, None) 或 (None, None, None, (error_code, detail))。
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
    if "sn" not in arguments or "fields" not in arguments:
        return None, None, None, (ERROR_INVALID_ARGUMENT, "缺少必填参数 sn/fields")
    sn = arguments["sn"]
    fields = arguments["fields"]
    if not isinstance(sn, str) or not _SN_RE.match(sn):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "SN 语法不符合冻结合同 ^ROOF-\\d{5}$")
    if not isinstance(fields, list) or not (FIELDS_MIN <= len(fields) <= FIELDS_MAX):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  f"fields 数量必须在 {FIELDS_MIN}..{FIELDS_MAX}")
    if len(set(fields)) != len(fields):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "fields 不得重复")
    for field in fields:
        if field not in FIELD_ENUM:
            return None, None, None, (ERROR_INVALID_ARGUMENT, "请求字段不允许")
    segment = arguments.get("segment", 0)
    if isinstance(segment, bool) or not isinstance(segment, int):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "segment 必须是整数")
    if not (0 <= segment < WAVEFORM_SEGMENTS):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  f"segment 必须在 0..{WAVEFORM_SEGMENTS - 1}")
    return sn, list(fields), segment, None

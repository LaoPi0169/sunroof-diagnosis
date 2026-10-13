"""report.draft_root_cause 只读聚合工具合同（阶段 C 节点 ⑳）—— 回链 api-contract/report-draft-v1.json。

discovery-brief-v1.md In-Scope 第 3 条的阶段 C 兑现: 输出结构化、带出处引用的
《天窗总成异常根因分析报告草稿》。设计沿用节点 ⑰⑱⑲ 只读工具既定模式 (身份只
来自可信会话 / 用户面失败封套逐字节一致 / 只读零效果), 不改冻结的 billing 工具
语义 (仅追加工具):
  * 身份永远来自可信会话; 参数走私身份键一律 invalid_argument;
  * 输入三键封闭: sn (^ROOF-\\d{5}$, 与 MES 合同同值) + part_no (^SR-[A-Z]{3,10}-
    [AB]\\d{2}$, 与 QMS/PLM 合同同值) + process_step (torque_tightening/gap_check);
  * 聚合只读三源: MES ⑰ (装配扭矩/ECU 刷写) + QMS ⑲ (现行 SOP/质量标准) +
    PLM ⑱ (BOM/ECO) —— 服务端内部直调三工具 server 层, 同一可信上下文透传,
    任一源 ACL/RBAC 拒绝则整体封套 not_available (三因一致零存在性泄漏, P-1);
  * 偏差计算 100% 确定性可复算 (measured vs 现行质量标准 target/tolerance),
    偏差超硬限 15% -> 结论 needs_review/SPEC_HARD_LIMIT_EXCEEDED (与阶段 A
    spec-check 同值原因码, 呼应 discovery-brief §6 成功判据);
  * P-2/P-3 贯通: 无现行 SOP -> gap (NO_CURRENT_SOP) 且结论转人工; 无对接标准
    的拧紧工序 -> gap (NO_STANDARD_FOR_STEP); 缺失字段 -> 结构化 gap, 绝不编造;
  * R-2 载料纪律: 传感器波形绝不嵌入报告载荷 (reason_code=
    OMITTED_BY_R2_PAGINATION_CONTRACT, 需要时经 MES 工具分页取数), 响应零
    波形点列 —— 绝不全量回传;
  * 草稿语义: draft_ready = 「依据充分、可出具带引用的草稿」, 不是「合格」;
    报告仅供 PQE 参考核对 (disclaimer 静态声明);
  * 外部写效果恒 0: 本模块零写入口, 三源 store 指纹不变 (测试以前后对照复验)。

与 JSON 合同同值互锁: SN_PATTERN / PART_NO_PATTERN / PROCESS_STEP_ENUM /
HARD_LIMIT_PCT / 错误词表 与 api-contract/report-draft-v1.json tool_binding
逐项一致 (tests/test_report_draft.py 断言)。
"""
from __future__ import annotations

import re

TOOL_NAME = "report.draft_root_cause"
TOOL_VERSION = "1"
EFFECT = "read_only"
SERVER_IDENTITY = "sunroof-report-server@local"

# ---- 输入语法 (与三源工具合同同值; 不归一化) ----
SN_PATTERN = r"^ROOF-\d{5}$"
PART_NO_PATTERN = r"^SR-[A-Z]{3,10}-[AB]\d{2}$"
PROCESS_STEP_ENUM = ("torque_tightening", "gap_check")
_SN_RE = re.compile(SN_PATTERN)
_PART_RE = re.compile(PART_NO_PATTERN)

# ---- 偏差硬限 (discovery-brief §6 / 阶段 A spec-check 同口径) ----
HARD_LIMIT_PCT = 15.0
REASON_HARD_LIMIT = "SPEC_HARD_LIMIT_EXCEEDED"

# ---- 结构化 gap 原因码 (P-2 / P-3 / R-2 贯通) ----
FIELD_NOT_AVAILABLE = "FIELD_NOT_AVAILABLE"
NO_CURRENT_SOP = "NO_CURRENT_SOP"
NO_STANDARD_FOR_STEP = "NO_STANDARD_FOR_STEP"
WAVEFORM_OMITTED_REASON = "OMITTED_BY_R2_PAGINATION_CONTRACT"

# ---- 结论状态封闭枚举 ----
CONCLUSION_DRAFT_READY = "draft_ready"
CONCLUSION_NEEDS_REVIEW = "needs_review"

# ---- 可信会话 (身份来源; 不得作为调用参数) ----
TRUSTED_CONTEXT_KEYS = ("tenant", "user", "role", "allowed_projects")
ALLOWED_ROLES = ("pqe", "quality_supervisor")

# ---- 错误词表 (与 JSON 合同 error_taxonomy 同值互锁) ----
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_NOT_AVAILABLE = "not_available"
ERROR_CODES = (ERROR_INVALID_ARGUMENT, ERROR_NOT_AVAILABLE)

# ---- 报告固定免责声明 (静态; 草稿语义) ----
DISCLAIMER = ("本报告为确定性规则聚合生成的草稿 (零大模型、零业务写效果), "
              "仅供 PQE 参考核对, 不构成合格判定或放行依据。")

ALLOWED_ARGUMENT_KEYS = ("sn", "part_no", "process_step")


def validate_arguments(arguments):
    """封闭校验 (网关与服务端共用一份; 纵深防御)。

    返回 (sn, part_no, process_step, None) 或 (None, None, None, (error_code, detail))。
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
    missing = [k for k in ALLOWED_ARGUMENT_KEYS if k not in arguments]
    if missing:
        return None, None, None, (ERROR_INVALID_ARGUMENT, "缺少必填参数: " + ", ".join(missing))
    sn = arguments["sn"]
    part_no = arguments["part_no"]
    process_step = arguments["process_step"]
    if not isinstance(sn, str) or not _SN_RE.match(sn):
        return None, None, None, (ERROR_INVALID_ARGUMENT, "sn 语法不符合冻结合同 ^ROOF-\\d{5}$")
    if not isinstance(part_no, str) or not _PART_RE.match(part_no):
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "part_no 语法不符合冻结合同 ^SR-[A-Z]{3,10}-[AB]\\d{2}$")
    if process_step not in PROCESS_STEP_ENUM:
        return None, None, None, (ERROR_INVALID_ARGUMENT,
                                  "process_step 必须取封闭枚举 torque_tightening/gap_check")
    return sn, part_no, process_step, None
"""运行期遥测与 SLI 归约 (Week 16) —— 「两种体温计」的运行期半边。

回链: week-16/runtime-sli-spec-v1.md (SLI 口径) + week-16/telemetry-contract-v1.md
(记录面) + week-16/trace-map-v1.md (两个追踪断点)。核心纪律 (手册 3667-3888):
  - 离线考卷金丝雀与运行期 SLI 是两种体温计: 分母永不共享, 两个数永不合并
    (canary 60 例 vs 运行窗口内 task_run_id 数);
  - 运行期只记录「发生了什么」, 不证明「内容正确」—— answered 终态在运行视图
    计入 allowed, 在考卷视图可能正是硬门失败 (NS-HOLD-06 活证据, 见回放 O-10);
  - result_unknown / reconciliation_pending 不是终态, 绝不折进通过率分子,
    单独按「未确认」口径计数 (禁止伪造终态的 SLI 化表述)。

组件:
  1) TaskTrace: 一个 task_run_id 的九阶段 span 链 —— 每段只记 状态码/错误类别/
     版本链/安全引用 (ID 与计数)/not_applicable 标记/观测时长; query 原文、答案
     正文、文档正文、审批载荷、客户字段、凭据永不入遥测 (scan_spans_for_violations
     机械自检);
  2) classify_terminal: 内部封套状态 -> 六类「可见终态」(answered/abstained/
     escalated/approval_denied/approval_revoked/action_completed/system_failed);
     返回 (可见终态或 None, 是否允许终态); None = 非终态 (预览/未知/对账中/写超时);
  3) reduce_runtime_metrics: trace 流 -> SLI 汇总 (allowed_terminal_rate /
     forbidden_effect_count / unconfirmed_effects / 终态分布 / 阶段覆盖),
     附口径警示 (denominator_note/content_note) 防两视图合并成一个数。

边界: 本模块不产生任何外部效果 (纯观测); 细分受限原因 (host.audit 的
reason_typed) 不进遥测 —— 遥测 error_class 只存错误类别码。
"""
from __future__ import annotations

import json
import time

from . import write_contract as W

# ---- 九阶段词汇表 (固定顺序 = 剧本执行顺序; 单任务通常有 not_applicable 段) ----
STAGE_REQUEST = "request_received"
STAGE_IDENTITY = "identity_and_scope"
STAGE_RETRIEVAL = "retrieval"
STAGE_GENERATION = "generation"
STAGE_VERIFICATION = "citation_and_policy_verification"
STAGE_APPROVAL = "approval"
STAGE_MCP_CALL = "mcp_call"
STAGE_RECONCILIATION = "reconciliation"
STAGE_RESPONSE = "response_presented"
STAGES = (
    STAGE_REQUEST, STAGE_IDENTITY, STAGE_RETRIEVAL, STAGE_GENERATION,
    STAGE_VERIFICATION, STAGE_APPROVAL, STAGE_MCP_CALL, STAGE_RECONCILIATION,
    STAGE_RESPONSE,
)

# ---- 六类可见终态 (week-16/runtime-sli-spec-v1.md 状态对照表的输出词表) ----
# v1.1 增补 (第 17 周): TERMINAL_ACTION_PREVIEW —— 降级停驻终态 (手册 3924:
# MCP 不可用 -> action_preview)。增补详见 week-17/failure-behavior-contract-v1.md
# 「SLI 口径增补」; 既有六类语义一字未动 (week-16 基线/回放不受影响)。
TERMINAL_ANSWERED = "answered"
TERMINAL_ABSTAINED = "abstained"
TERMINAL_ESCALATED = "escalated"
TERMINAL_APPROVAL_DENIED = "approval_denied"
TERMINAL_APPROVAL_REVOKED = "approval_revoked"
TERMINAL_ACTION_COMPLETED = "action_completed"
TERMINAL_SYSTEM_FAILED = "system_failed"
TERMINAL_ACTION_PREVIEW = "action_preview"
VISIBLE_TERMINALS = (
    TERMINAL_ANSWERED, TERMINAL_ABSTAINED, TERMINAL_ESCALATED,
    TERMINAL_APPROVAL_DENIED, TERMINAL_APPROVAL_REVOKED,
    TERMINAL_ACTION_COMPLETED, TERMINAL_SYSTEM_FAILED,
    TERMINAL_ACTION_PREVIEW,
)
# 允许终态 = 业务上「正常走完或安全停住」; system_failed 诚实记录但不计入通过率分子
ALLOWED_TERMINALS = (
    TERMINAL_ANSWERED, TERMINAL_ABSTAINED, TERMINAL_ESCALATED,
    TERMINAL_APPROVAL_DENIED, TERMINAL_APPROVAL_REVOKED,
    TERMINAL_ACTION_COMPLETED, TERMINAL_ACTION_PREVIEW,
)

# 遥测层的降级停驻 outcome (与用户面封套 status=action_preview 同义区分:
# 正常流程中 finish("action_preview") 从不出现, 降级停驻用独立词避免歧义)
TRACE_OUTCOME_PREVIEW_HELD = "action_preview_held"

# week-12 四态期望词表 -> 六类可见终态 (refused/needs_review 用户可见都是「拒答」)
RAG_STATE_TO_VISIBLE = {
    "answered": TERMINAL_ANSWERED,
    "refused": TERMINAL_ABSTAINED,
    "needs_review": TERMINAL_ABSTAINED,
    "escalated": TERMINAL_ESCALATED,
}

# 写路径: 审批门三类错误同属「审批被拒」终态; 其余错误归系统失败
_WRITE_DENIED_ERRORS = (W.ERROR_APPROVAL_REQUIRED, W.ERROR_APPROVAL_EXPIRED,
                        W.ERROR_APPROVAL_MISMATCH)
_WRITE_SYSTEM_ERRORS = (W.ERROR_INVALID_ARGUMENT, W.ERROR_NOT_AVAILABLE,
                        W.ERROR_VERSION_MISMATCH, W.ERROR_CONTRACT_VIOLATION)

ISO_FORMAT = W.ISO_FORMAT


def default_clock():
    """缺省真实 UTC 时钟 (与 client/write_contract 同格式; 回放用固定时钟注入)。"""
    return time.strftime(ISO_FORMAT, time.gmtime())


def classify_terminal(kind: str, outcome):
    """内部封套状态 -> (可见终态或 None, 是否允许终态)。

    kind ∈ {"read", "write"}; outcome = RAG mapped_state / 封套 status / 错误码。
    返回 None = 非终态: result_unknown、reconciliation_pending、写超时
    (外部效果未确认 -> 先对账, 不是失败也不是成功); 正常流程的预览也是非终态。
    v1.1 (第 17 周): 降级停驻 outcome=action_preview_held -> 允许终态
    action_preview (手册 3924: MCP 不可用停在预览是合格结果, 不是失败)。
    """
    if kind == "write":
        if outcome == W.STATUS_COMPLETED:
            return TERMINAL_ACTION_COMPLETED, True
        if outcome == TRACE_OUTCOME_PREVIEW_HELD:
            return TERMINAL_ACTION_PREVIEW, True
        if outcome == W.STATUS_DENIED:
            return TERMINAL_APPROVAL_DENIED, True
        if outcome == W.ERROR_APPROVAL_REVOKED:
            return TERMINAL_APPROVAL_REVOKED, True
        if outcome in _WRITE_DENIED_ERRORS:
            return TERMINAL_APPROVAL_DENIED, True
        if outcome in (W.STATUS_PREVIEW, W.STATUS_UNKNOWN,
                       W.STATUS_RECONCILIATION_PENDING, W.ERROR_DEADLINE_EXCEEDED):
            return None, False
        if outcome in _WRITE_SYSTEM_ERRORS:
            return TERMINAL_SYSTEM_FAILED, False
        return TERMINAL_SYSTEM_FAILED, False
    # read 路径: RAG 四态映射 + 其余 (错误码/None) -> system_failed
    visible = RAG_STATE_TO_VISIBLE.get(outcome)
    if visible is not None:
        return visible, True
    return TERMINAL_SYSTEM_FAILED, False


class TaskTrace:
    """一个 task_run_id 的遥测轨迹: 九阶段 span + 任务级安全引用/版本链/效果增量。

    记录面 (telemetry-contract-v1): 只存 状态码/错误类别/版本串/引用 ID/计数/时长;
    禁存 query 原文、答案与文档正文、审批载荷、客户字段、凭据 (见
    scan_spans_for_violations; 违规键/标记由测试与工作台「脱敏检查」机械化复核)。
    """

    def __init__(self, task_run_id: str, *, kind: str, clock=None):
        if kind not in ("read", "write"):
            raise ValueError("kind 必须是 read 或 write")
        self.task_run_id = task_run_id
        self.kind = kind
        self._clock = clock or default_clock
        self.started_at = self._clock()
        self.finished_at = None
        self.outcome = None      # 封套状态/错误码/RAG mapped_state (无正文)
        self.effects = 0         # 本任务外部效果增量 (合同唯一允许效果)
        self.safe_refs = {}      # 任务级安全引用 (approval/ticket/idempotency/版本)
        self.versions = {}       # 版本链 (corpus/dataset/retriever/candidate/policy)
        self.spans = []
        self._last_perf = time.perf_counter()

    def span(self, stage: str, *, result, error_class=None, versions=None,
             safe_refs=None, not_applicable=False, duration_ms=None) -> dict:
        """追加一段 span; duration_ms 缺省按相邻 span 间隔观测 (只作观测, 不设门)。"""
        if stage not in STAGES:
            raise ValueError(f"未知阶段: {stage}")
        if duration_ms is None:
            now = time.perf_counter()
            duration_ms = round((now - self._last_perf) * 1000.0, 2)
            self._last_perf = now
        record = {
            "task_run_id": self.task_run_id,
            "kind": self.kind,
            "stage": stage,
            "at": self._clock(),
            "duration_ms": duration_ms,
            "result": result,
            "error_class": error_class,
            "not_applicable": not_applicable,
            "versions": dict(versions or {}),
            "safe_refs": dict(safe_refs or {}),
        }
        self.spans.append(record)
        for key, value in (versions or {}).items():
            self.versions[key] = value
        for key, value in (safe_refs or {}).items():
            self.safe_refs[key] = value
        return record

    def finish(self, outcome, *, effects=0) -> "TaskTrace":
        self.outcome = outcome
        self.effects = effects
        self.finished_at = self._clock()
        return self

    @property
    def terminal(self):
        """(可见终态或 None, 是否允许终态) —— 由 classify_terminal 派生。"""
        return classify_terminal(self.kind, self.outcome)

    def as_task_row(self) -> dict:
        """任务级一行摘要 (工作台任务列表用; 不含任何正文)。"""
        visible, allowed = self.terminal
        return {
            "task_run_id": self.task_run_id,
            "kind": self.kind,
            "outcome": self.outcome,
            "visible_terminal": visible,
            "allowed_terminal": allowed,
            "external_effects": self.effects,
            "stage_count": len(self.spans),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ---- 口径警示常量 (硬编码进每份指标输出; 防止「两个数」被合并成「一个数」) ----
DENOMINATOR_NOTE = ("分母 = 本窗口内的 task_run_id (运行期回放/真实流量); "
                    "与离线考卷金丝雀的冻结 60 例分母无关; 两视图分子分母永不共享、"
                    "两数永不合并成一个数")
CONTENT_NOTE = ("answered/abstained/escalated 只说明「呈现了什么终态」, 不证明内容"
                "正确 —— 内容正确性只由考卷金丝雀度量 (活证据: NS-HOLD-06 在运行"
                "视图是 answered, 在考卷视图是硬门失败)")


def reduce_runtime_metrics(traces) -> dict:
    """trace 流 -> 运行期 SLI 汇总 (week-16/runtime-sli-spec-v1.md 的机械实现)。

    forbidden_effect_count 与 allowed_terminal_rate 相互独立: approval_denied/
    approval_revoked 是允许终态 (安全停住), 但这些路径携带任何效果增量都是
    合同违规 (第 14/15 周硬门: 拒/撤/失败路径效果恒 0) —— 照抓, 不漏。
    """
    terminal_counts = {name: 0 for name in VISIBLE_TERMINALS}
    non_terminal_counts = {}
    allowed_numerator = 0
    forbidden_effect_count = 0   # 拒/撤/系统失败终态出现的效果增量 (合同禁止)
    unconfirmed_effects = 0      # 非终态任务携带的效果增量 (未确认, 单独口径)
    stage_coverage = {stage: {"emitted": 0, "not_applicable": 0}
                      for stage in STAGES}
    for trace in traces:
        for sp in trace.spans:
            stats = stage_coverage.get(sp["stage"])
            if stats is not None:
                if sp.get("not_applicable"):
                    stats["not_applicable"] += 1
                else:
                    stats["emitted"] += 1
        visible, allowed = classify_terminal(trace.kind, trace.outcome)
        if visible is None:
            key = str(trace.outcome)
            non_terminal_counts[key] = non_terminal_counts.get(key, 0) + 1
            unconfirmed_effects += trace.effects
            continue
        terminal_counts[visible] += 1
        if allowed:
            allowed_numerator += 1
        if visible in (TERMINAL_APPROVAL_DENIED, TERMINAL_APPROVAL_REVOKED,
                       TERMINAL_SYSTEM_FAILED, TERMINAL_ACTION_PREVIEW):
            # v1.1 (第 17 周): 降级停驻 (action_preview) 携带任何效果增量同样是
            # 合同违规 —— 「停在预览」的定义就是 0 外部效果, 违规必须被 SLI 抓住。
            forbidden_effect_count += trace.effects
    total = len(traces)
    return {
        "view": "runtime",
        "tasks_total": total,
        "allowed_terminal_rate": {
            "numerator": allowed_numerator,
            "denominator": total,
            "value": (round(allowed_numerator / total, 4) if total else None),
        },
        "terminal_distribution": terminal_counts,
        "non_terminal_distribution": non_terminal_counts,
        "forbidden_effect_count": forbidden_effect_count,
        "unconfirmed_effects": unconfirmed_effects,
        "stage_coverage": stage_coverage,
        "denominator_note": DENOMINATOR_NOTE,
        "content_note": CONTENT_NOTE,
    }


# ---- 遥测脱敏自检 (week-16/telemetry-redaction-test-v1.md 的机械底稿) ----
# 普通遥测流禁止出现的键 (白名单精神: 这些字段只存在于受限审计/系统内部)
FORBIDDEN_SPAN_KEYS = (
    "query", "answer", "prompt", "approved_parameters", "proposed_parameters",
    "canonical_parameters", "supervisor_note", "customer_name", "payment_detail",
    "token", "password", "credential",
)
# 已知敏感内容标记 (客户字段/政策正文; 与 ci/redaction_scan.py 同源)。
# 凭据字面量 (token_agent_valid 等) 的检测职责在 STAGE-3 全范围字面量扫描 +
# 下方 FORBIDDEN_SPAN_KEYS 的 "token" 键名规则 —— 本清单不持有凭据字面量
# (扫描器白名单语义: 凭据常量只允许出现在授权文件, 见 ci/redaction_scan.py)。
SENSITIVE_SPAN_MARKERS = (
    "supervisor_note", "customer_name", "payment_detail", "X-Auth-Token",
    "2026 华东套餐差价计费政策", "2025 华东套餐差价计费政策",
    "2026 特殊退款政策",
)


def scan_spans_for_violations(events) -> list:
    """扫描遥测事件流; 返回违规列表 (空 = 通过)。只报键名/标记前缀, 不回显内容。"""
    violations = []
    for index, event in enumerate(events):
        text = json.dumps(event, ensure_ascii=False, sort_keys=True)
        for key in FORBIDDEN_SPAN_KEYS:
            if f'"{key}"' in text:
                violations.append({"event_index": index, "rule": "forbidden_key",
                                   "key": key})
        for marker in SENSITIVE_SPAN_MARKERS:
            if marker in text:
                violations.append({"event_index": index,
                                   "rule": "sensitive_marker",
                                   "marker_prefix": marker[:4]})
    return violations




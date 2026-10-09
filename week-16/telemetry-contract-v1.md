# 遥测合同 v1（telemetry-contract-v1，第 16 周）

- 日期: 2026-10-09 · 回链: `week-16/trace-map-v1.md`（记录面）+
  `week-16/telemetry-redaction-test-v1.md`（验证结果）·
  实现: `mcptool/telemetry.py` · 扫描底稿: `scan_spans_for_violations`

## 一、立场

遥测是给**工程与业务用户看的普通面**（工作台页面、SLI 报表）。它必须有用
（能还原链路、算出 SLI），也必须**天然装不下敏感内容**——不是「小心别写」，
而是「没有字段可写」。正文类信息一旦需要排查，走受限审计面（host.audit，
restricted），不走遥测。

## 二、span 结构（九阶段 × 每段固定字段）

```
task_run_id   任务 ID（幂等键/审批 ID 的派生根）
kind          read | write
stage         九阶段之一（见 trace-map-v1 §一）
at            ISO-8601 Z 时刻（回放为固定时钟 2026-10-09T00:00:00Z）
duration_ms   观测时长（perf_counter 间隔; 只作展示, 不设门）
result        状态码/安全结果串（无正文）
error_class   错误类别码（细分 reason_typed 留在受限审计, 不进遥测）
not_applicable 布尔（缺席段留痕, 注明原因）
versions      版本链（corpus/dataset/retriever/candidate/prompt/policy/tool/contract）
safe_refs     安全引用（case_id/文档 ID/审批 ID/幂等键/工单号/计数/布尔标记）
```

## 三、允许字段（白名单精神）

状态码、错误类别码、版本串、引用 ID（case/document/approval/ticket/
idempotency）、计数（效果增量/引用问题数/泄漏数）、布尔标记、观测时长。

## 四、禁止内容（遥测流中零出现）

| 类别 | 例 | 替代 |
| --- | --- | --- |
| 提问原文 | 用户 query | case_id + query 不入遥测 |
| 生成正文 | answer/摘要文本 | 三态 state + 引用文档 ID |
| 文档/政策正文 | 政策标题、条款 | document_id@version |
| 审批载荷 | approved_parameters 全文 | approval_id + 决定枚举 |
| 客户字段 | supervisor_note/customer_name/payment_detail | 永不出现（枚举层已禁） |
| 凭据 | 任何 token/密码 | 永不出现（键名 "token" 即违规） |

## 五、restricted 边界（与第 14/15 周同口径）

细分拒绝原因（reason_typed）、审批载荷、撤销元数据（revoked_by/at/reason）只存
受限审计（host.audit / approval 记录），仅在受控页面回显给本人；遥测只保留
错误**类别**与**决定枚举**。工作台「脱敏检查」按钮即对本合同做机械化复核。

## 六、验证方法与节奏

1. **机械自检**：`scan_spans_for_violations(spans)` —— FORBIDDEN_SPAN_KEYS
   （12 个禁用键名）+ SENSITIVE_SPAN_MARKERS（客户字段/政策正文标记）；
   违规只报 键名/标记前缀，不回显内容（避免扫描器自己变成泄漏源）；
2. **扫描器有效性**：注入污染样本必须被抓到（test_07）；
3. **节奏**：每次回放/工作台脱敏检查即时执行；CI STAGE-3 对全部运行时文件
   做字面量扫描（44 文件，与本周同源扩容 39→44）；
4. **保留策略**：本地教学实现不落盘轮转，样例工件
   `week-16/telemetry-span-sample-v1.jsonl`（90 行）入库前过扫描（test_11）；
   真实系统的保留窗口/采样率 [U]（无真实流量，不定死）。

## 七、红线

1. 遥测组件（telemetry/run_canary/run_telemetry_replay）为纯观测，零外部效果；
2. 不得为「排查方便」给遥测加正文/载荷字段——需要时升 v2 并重走脱敏验证；
3. 「普通遥测干净」不等于「系统无敏感数据」——受限审计面按其自身口径管理。

# 遥测脱敏测试报告 v1（telemetry-redaction-test-v1，第 16 周）

- 日期: 2026-10-09 · 对象: `week-16/telemetry-span-sample-v1.jsonl`（90 段 span，
  10 任务 × 9 阶段）+ 工作台实时流 · 工具: `mcptool/telemetry.py
  scan_spans_for_violations` + `ci/redaction_scan.py`（STAGE-3）·
  自动化: `tests/test_telemetry_observability.py` test_07/test_11

## 一、测什么

验证「普通遥测流装不下敏感内容」不是口头承诺：对回放产生的全部遥测事件做
键名级 + 内容标记级机械扫描，并反向验证扫描器本身有效（污染样本必被抓）。

## 二、扫描规则（两层）

1. **禁用键名**（FORBIDDEN_SPAN_KEYS，12 项）：`query` / `answer` / `prompt` /
   `approved_parameters` / `proposed_parameters` / `canonical_parameters` /
   `supervisor_note` / `customer_name` / `payment_detail` / `token` /
   `password` / `credential` —— 遥测事件序列化后出现任一键名即违规；
2. **敏感内容标记**（SENSITIVE_SPAN_MARKERS）：客户字段名（supervisor_note/
   customer_name/payment_detail）、`X-Auth-Token`、三份政策正文标题标记
   （与 ci/redaction_scan.py BODY_MARKERS 同源）—— 内容级兜底。

凭据字面量（token_agent_valid / token_supervisor_secret）**不在本清单持有**：
其检测职责在 STAGE-3 全范围字面量扫描（授权文件白名单口径，44 文件），
遥测层的键名规则 `token` 同时兜底「凭据值进遥测」的场景。

## 三、结果

| 检查 | 对象 | 结果 |
| --- | --- | --- |
| 禁用键名 × 敏感标记 全扫描 | 回放 90 段 span | **0 违规** |
| 序列化全文人工标记复核 | `supervisor_note` / `customer_name` / `payment_detail` / 政策标题 | 均未出现 |
| 工作台「脱敏检查」按钮 | 实时回放流（90 事件） | passed=true, 0 违规 |
| 扫描器有效性（反向） | 注入 `{"query":…, "answer":…}` 污染样本 | 命中 forbidden_key ×2，**只报键名不回显内容** |
| CI STAGE-3 字面量扫描 | 全部 44 个运行时文件 | 0 违规（emissions_checked=53） |
| 样例工件门 | week-16/telemetry-span-sample-v1.jsonl 90 行 | 逐行可解析 + 0 违规（test_11） |

## 四、为什么敢说「装不下」

记录面按 `telemetry-contract-v1.md` §二 的固定字段构造：span 只有
状态码/错误类别/版本串/引用 ID/计数/布尔/观测时长七个信息面，**结构上没有**
承载正文与载荷的字段；扫描是「合同被违反时的报警器」，不是唯一的防线。

## 五、边界（诚实不夸大）

1. 扫描覆盖键名与已知标记；任意新构的敏感字符串（不在标记清单内）若被未来
   代码塞进 safe_refs 自由文本，靠 STAGE-3 输出点扫描 + 评审兜底——标记清单
   升级属合同 v2 事项；
2. 受限审计面（host.audit / 审批记录）**有意**含载荷与细分原因，按其自身
   restricted 口径管理，不在本报告范围；
3. duration_ms 为观测值；本报告不声明遥测系统已达到生产级采集/存储标准
   （保留策略 [U]）。

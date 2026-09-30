# 数据合同 (Data Contract v1)

- **场景名称**：北辰 B2B 客服计费与升级政策数据摄取 (Ingestion)
- **关联 Brief**：`discovery-brief-v1.0` (第 4 周获准版本) 与 Week 05 接口契约 `api-contract/policy-check-v1.json`
- **版本**：`data-contract-v1.0`
- **所有者**：客服知识库管理员 (Knowledge Admin) & FDE 交付团队
- **代码载体**：`ingestion/schema.py` (DTO) + `ingestion/service.py::validate_row` (校验执行)

---

## 1. 字段 Schema 与校验规则

| 字段名 | 类型 | 是否必填 | 默认值 | 校验规则 / 约束 | 违规时的处理终态 |
| --- | --- | --- | --- | --- | --- |
| `policy_id` | String | 是 | - | 非空，业务键前半段 | 隔离 (`MISSING_BUSINESS_KEY`) |
| `title` | String | 是 | - | 非空，摘要字段，可进入 API 响应 | 隔离 (`MISSING_BUSINESS_KEY`) |
| `version` | String | 是 | - | 非空，政策自身版本 (如 `v3`) | 隔离 (`MISSING_BUSINESS_KEY`) |
| `region` | String | 是 | - | 枚举：`华东` / `华南` / `华北` / `全国` | 隔离 (`INVALID_REGION`) |
| `access_level` | String | 是 | **无默认值** | 枚举：`support_agent` / `supervisor`；**缺失或未知一律默认隔离 (Default Deny)** | 隔离 (`UNKNOWN_ACCESS_LEVEL`) |
| `is_deprecated` | Int | 否 | 0 | 0 或 1，入库前强转 | 非法值强转 0 |
| `effective_at` | Date | 是 | - | ISO-8601 (`YYYY-MM-DD`) | 隔离 (`INVALID_SCHEMA`) |
| `expires_at` | Date | 否 | NULL | ISO-8601；**必须严格晚于 `effective_at`** (与主表 CHECK 约束一致) | 隔离 (`INVALID_EFFECTIVE_RANGE`) |
| `formula_rule` | String | 是 | - | 非空，确定性公式规则标识 | 隔离 (`MISSING_BUSINESS_KEY`) |
| `source_id` | String | 是 | - | 非空，数据源标识 (血缘) | 隔离 (`MISSING_BUSINESS_KEY`) |
| `source_version` | String | 是 | - | 非空，上游文档版本 (血缘，业务键后半段) | 隔离 (`MISSING_BUSINESS_KEY`) |
| `owner_id` | String | 是 | **无默认值** | 非空，知识所有者；缺失无法定责 | 隔离 (`MISSING_OWNER`) |

校验顺序即诊断优先级：业务键 → 所有者 → 权限 (默认拒绝) → 地区 → 时间边界。

## 2. 业务唯一键与幂等策略

- **业务主键 (Business Key)**：`policy_id` + `source_version`（稳定语义键，**绝不用行号**——文件重排后行号会变化）。
- **重复检测 (`duplicate`)**：批内 `seen` 集合 + 主表 `policy_exists` 查重 + 主键约束兜底三层防线。重复行**只计数并落隔离表 (`DUPLICATE_BUSINESS_KEY`) 用于对账，绝不向主表写入，绝不产生第二次业务效果**。
- **同批次重跑幂等 (`batch_id`)**：`ingestion_batches.batch_id` 是幂等键。已达终态 (`completed` / `completed_with_quarantine`) 的批次再次提交时，**直接返回既有对账结果并置 `is_idempotent_replay=true`**，主表、隔离区与原审计行（含 `created_at`/计数）零写副作用。
- **批次原子性**：整批处理在**单个 SQLite 事务**内完成（登记批次 → 逐行分流 → 写终态 → commit），进程崩溃不会留下部分提交的批次。

## 3. 批次状态

| 状态 | 含义 |
| --- | --- |
| `processing` | 事务进行中（崩溃回滚后不应残留该终态） |
| `completed` | 全部行 accepted/duplicate，无隔离 |
| `completed_with_quarantine` | 有行进入隔离区（批次完成但需人工跟进） |
| `failed` | 事务失败整体回滚，无部分提交 |

## 4. 隔离区 (Quarantine) 原因码说明

| 原因码 | 根因 | 下一步动作 (运营者看到即知道怎么做) |
| --- | --- | --- |
| `MISSING_OWNER` | 缺少知识所有者 | 联系知识库管理员补齐 `owner_id` 后定向重放 |
| `UNKNOWN_ACCESS_LEVEL` | 访问级别缺失/非标 | **默认安全阻断**；找权限所有者核对后重放 |
| `INVALID_EFFECTIVE_RANGE` | 失效时间早于等于生效时间 | 修正日期字符串后重放，或拒绝来源 |
| `DUPLICATE_BUSINESS_KEY` | 业务主键重复 | 核对上游是否重复推送；已有正常记录无需重放 |
| `MISSING_BUSINESS_KEY` | 业务键/核心标识字段缺失 | 补齐标识字段后重放 |
| `INVALID_REGION` | 地区不在枚举范围 | 纠正地区后重放 |
| `INVALID_SCHEMA` | 日期格式非法等解析失败 | 修正为 ISO-8601 后重放 |

隔离记录携带 `safe_source_ref`（`{source_id}#R{行号}`，可进入日志的安全来源引用）与 `raw_payload`（原始行 JSON，**仅存于隔离区证据存储，绝不进入普通日志**）。状态只会 `quarantined → resolved` 流转，**绝不物理删除**。

## 5. 定向重放 (Targeted Replay)

- 隔离记录修复后，通过 `ingestion.service.execute_targeted_replay(ReplayRequest)` 发起定向重放；**禁止直接手工 SQL 修改 `policies` 表**。
- `fixed_payload` 支持两种形态：完整修复行，或**部分补丁**（仅含修复字段，系统自动与隔离区 `raw_payload` 合并后重新过数据合同校验）。
- 成功 = 原子事务三件事：主表新增业务版本（血缘 `batch_id = replay-{replay_id}`，可追溯到重放事件）+ 隔离记录置 `resolved` 并回填 `replay_id` + `ingestion_replay_logs` 落审计（成功与失败的尝试都落）。
- 重放后业务键若已存在（重复修复）或仍未通过校验 → 拒绝，隔离记录保持原状，不产生假成功。

## 6. 访问级别与默认拒绝 (Default Deny)

- `access_level` 是唯一可见性依据：`support_agent`（普通客服可见）与 `supervisor`（主管可见，含全部普通内容）。
- **缺失或未知的 `access_level` 一律进入隔离区**，绝不自动赋予默认可见范围；supervisor 数据入库后保持 `supervisor` 不降级。

## 7. 日志内容边界 (Security Boundary)

普通结构化日志（logger `fde.ingestion`）字段白名单：事件名、`correlation_id`、`batch_id`、`source_id`、行引用 `row_ref`、计数（`input_rows` / `accepted_rows` / `duplicate_rows` / `quarantined_rows`）、`reason_code`、`replay_id` / `quarantine_id`、`result`。

**绝不写入**：政策标题/正文、公式规则、所有者 ID、政策 ID、客户信息、密钥、原始行 JSON。需要原始行调试时，请查隔离区证据存储（`quarantine_records.raw_payload`），其访问受数据库权限与保留规则约束。

## 8. 存量 Week 05 数据的血缘口径

Week 05 种子夹具（`POL-2026-BIL-01` / `POL-2025-BIL-01` / `POL-2026-REF-09`）先于摄取管道存在。迁移 `002` 幂等回填其血缘：`batch_id = week05-baseline-seed`、`source_version = version`。因此主表**每一个**业务版本都可追溯 `source_id` / `source_version` / `batch_id`；后续批次若推送与种子相同的业务键（如 `POL-2026-BIL-01:v3`）会被正确判为 `duplicate`。

## 9. 结论边界 (诚实不夸大)

本合同仅约束「结构合法、语义合规的数据进入可解释终态」。批次 `completed` **不等于**数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态，隔离率是待跟进信号而非失败。


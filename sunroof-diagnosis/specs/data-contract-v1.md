# 数据合同 (Data Contract v1) —— 天窗检验规格摄取

- **场景名称**：天窗总成只读诊断 —— QMS/PLM 检验规格数据摄取 (Ingestion)
- **关联 Brief**：`specs/discovery-brief-v1.md`（获准版本）与接口契约 `api-contract/spec-check-v1.json`
- **版本**：`data-contract-v1.0`
- **所有者**：QMS 规格知识管理员 & FDE 交付团队
- **代码载体**：`ingestion/schema.py`（DTO）+ `ingestion/service.py::validate_row`（校验执行）
- **回链**：原仓库 Week 06 惯例（`ingestion/` 三件套 + 迁移 002 + 三处一致对账）

---

## 1. 字段 Schema 与校验规则

| 字段名 | 类型 | 是否必填 | 默认值 | 校验规则 / 约束 | 违规时的处理终态 |
| --- | --- | --- | --- | --- | --- |
| `spec_id` | String | 是 | - | 非空，跨版本稳定标识；**不同版本必须换发新 spec_id**（如 SPEC-SR-A-V5 → SPEC-SR-A-V6） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `title` | String | 是 | - | 非空，摘要字段，可进入 API 响应 | 隔离 (`MISSING_BUSINESS_KEY`) |
| `version` | String | 是 | - | 非空，规格自身版本（如 `v6.0`） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `part_no` | String | 是 | - | 非空，零件业务键（如 `MOTOR-MOUNT-BOLT-03`） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `process_step` | String | 是 | - | 枚举：`torque_tightening` / `gap_check` | 隔离 (`INVALID_PROCESS_STEP`) |
| `oem_project` | String | 是 | - | 枚举：`OEM-A` / `OEM-B` | 隔离 (`INVALID_OEM_PROJECT`) |
| `is_current` | Int | 否 | **0** | 0/1；**缺失默认 0，绝不自动赋予现行效力**（Default-Deny） | 非法值隔离 (`INVALID_VALUE_RANGE`) |
| `effective_at` | Date | 是 | - | ISO-8601（`YYYY-MM-DD`） | 隔离 (`INVALID_SCHEMA`) |
| `expires_at` | Date | 否 | NULL | ISO-8601；**必须严格晚于 `effective_at`**（与主表 CHECK 一致） | 隔离 (`INVALID_EFFECTIVE_RANGE`) |
| `nominal_value` | Number | 是 | - | > 0 | 隔离 (`INVALID_VALUE_RANGE`) |
| `tol_plus` / `tol_minus` | Number | 是 | - | ≥ 0 | 隔离 (`INVALID_VALUE_RANGE`) |
| `unit` | String | 是 | - | 必须与工序计量约定一致：`torque_tightening`→`N·m`，`gap_check`→`mm` | 隔离 (`UNIT_PROCESS_MISMATCH`) |
| `formula_rule` | String | 是 | - | 非空，确定性公式规则标识 | 隔离 (`MISSING_BUSINESS_KEY`) |
| `source_ref` | String | 是 | - | 非空，可追溯来源引用（如 `QMS-SOP-SR-04-v5#Section-3.2`） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `source_id` | String | 是 | - | 非空，数据源标识（血缘） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `source_version` | String | 是 | - | 非空，上游文档版本（血缘，业务键后半段） | 隔离 (`MISSING_BUSINESS_KEY`) |
| `owner_id` | String | 是 | **无默认值** | 非空，知识所有者；缺失无法定责 | 隔离 (`MISSING_OWNER`) |
| `allowed_oem_projects` | JSON 数组 | 是 | **无默认值** | 必须为**单元素数组且与 `oem_project` 一致**；缺失/空/越权/多元素一律隔离 | 隔离 (`ACL_SCOPE_MISMATCH`) |

校验顺序即诊断优先级：业务键 → 所有者 → 工序 → 项目组 → ACL 声明（默认隔离）→ 数值 → 单位 → 时间边界。

## 2. 业务唯一键与幂等策略

- **业务主键 (Business Key)**：`spec_id` + `source_version`（稳定语义键，**绝不用行号**——文件重排后行号会变化）。
- **重复检测 (`duplicate`)**：批内 `seen` 集合 + 主表 `spec_key_exists` 查重 + `spec_id_taken`（同 spec_id 不同 source_version 视为上游修订异常，**必须换发新 spec_id**）+ 主键约束兜底，四层防线。重复行**只计数并落隔离表 (`DUPLICATE_BUSINESS_KEY`) 用于对账，绝不向主表写入，绝不产生第二次业务效果**。
- **同批次重跑幂等 (`batch_id`)**：`ingestion_batches.batch_id` 是幂等键。已达终态（`completed` / `completed_with_quarantine` / `failed`）的批次再次提交时，**直接返回既有对账结果并置 `is_idempotent_replay=true`**，主表、隔离区与批次行零写副作用；`failed` 是已消费的幂等键，重试必须换新 `batch_id`。
- **批次原子性**：整批处理在**单个 SQLite 事务**内完成（登记批次 → 逐行分流 → 写终态 → commit），进程崩溃不会留下部分提交的批次；基础设施错误整批回滚，仅保留 `failed` 标记行（`total_rows=0` 表示零持久化效果，对账公式仍成立）。
## 3. 批次状态

| 状态 | 含义 |
| --- | --- |
| `processing` | 事务进行中（崩溃回滚后不应残留该终态） |
| `completed` | 全部行 accepted/duplicate，无隔离 |
| `completed_with_quarantine` | 有行进入隔离区（批次完成但需人工跟进） |
| `failed` | 事务失败整体回滚，无部分提交；幂等键已消费，重试换新 `batch_id` |

## 4. 隔离区 (Quarantine) 原因码说明

| 原因码 | 根因 | 下一步动作（运营者看到即知道怎么做） |
| --- | --- | --- |
| `MISSING_OWNER` | 缺少知识所有者 | 联系规格发布人补齐 `owner_id` 后定向重放 |
| `ACL_SCOPE_MISMATCH` | ACL 声明缺失/越权/多元素 | **默认安全阻断**；找权限所有者核对为单元素且与 `oem_project` 一致后重放 |
| `INVALID_PROCESS_STEP` | 工序不在枚举范围 | 纠正为 `torque_tightening` / `gap_check` 后重放 |
| `INVALID_OEM_PROJECT` | 项目组不在枚举范围 | 纠正为 `OEM-A` / `OEM-B` 后重放 |
| `INVALID_VALUE_RANGE` | 名义值/公差/`is_current` 非法 | 名义值 > 0、公差 ≥ 0、`is_current` ∈ {0,1}；核对后重放 |
| `UNIT_PROCESS_MISMATCH` | 单位与工序约定不符 | 扭矩 `N·m`、间隙 `mm`；纠正后重放 |
| `INVALID_EFFECTIVE_RANGE` | 失效时间早于等于生效时间 | 修正日期后重放，或拒绝来源 |
| `DUPLICATE_BUSINESS_KEY` | 业务主键重复 | 核对上游是否重复推送；已有正常记录无需重放 |
| `MISSING_BUSINESS_KEY` | 业务键/核心标识字段缺失 | 补齐标识字段后重放 |
| `INVALID_SCHEMA` | 日期格式非法等解析失败 | 修正为 ISO-8601 后重放 |

隔离记录携带 `safe_source_ref`（`{source_id}#R{行号}`，可进入日志的安全来源引用）与 `raw_payload`（原始行 JSON，**仅存于隔离区证据存储，绝不进入普通日志**）。状态只会 `quarantined → resolved` 流转，**绝不物理删除**。

## 5. 定向重放 (Targeted Replay)

- 隔离记录修复后，通过 `ingestion.service.execute_targeted_replay(ReplayRequest)` 发起定向重放；**禁止直接手工 SQL 修改 `specs` 表**（操作步骤见 `ingestion-runbook-v0.md`）。
- `fixed_payload` 支持两种形态：完整修复行，或**部分补丁**（仅含修复字段，系统自动与隔离区 `raw_payload` 合并后重新过数据合同校验）。
- 成功 = 原子事务三件事：主表新增业务版本（血缘 `batch_id = replay-{replay_id}`，可追溯到重放事件）+ 隔离记录置 `resolved` 并回填 `replay_id` + `ingestion_replay_logs` 落审计（成功与失败的尝试都落）。
- 重放后业务键若已存在（重复修复）或仍未通过校验 → 拒绝，隔离记录保持原状，不产生假成功；`rep-err-` 前缀重放 ID 表示记录不存在或已 resolved。

## 6. 项目组可见性与入口 Default-Deny (保护指标 P-1)

- 可见性由两层决定：入口的行级 `allowed_oem_projects` 声明（必须与 `oem_project` 一致）与查询层 `oem_project IN (allowed_oem_projects)` SQL 下推；`["*"]` 仅限质量主管跨项目只读。
- **缺失或越权的 ACL 声明一律进入隔离区**，绝不自动收窄或扩大可见范围；`is_current` 缺失默认 0，**绝不自动赋予现行效力**（未声明的现行资格需显式重新提交）。
- 摄取管道**绝不自动 supersede** 已有版本：同零件同工序出现多条现行规格时由 `spec-check` 判定表以 `SPEC_CONFLICT` 显式拒答（宁可拒答，不假装知道 —— 保护指标 P-3）。

## 7. 日志内容边界 (Security Boundary)

普通结构化日志（logger `sunroof.ingestion`）字段白名单：事件名、`correlation_id`、`batch_id`、`source_id`、行引用 `row_ref`、计数（`input_rows` / `accepted_rows` / `duplicate_rows` / `quarantined_rows`）、`reason_code`、`replay_id` / `quarantine_id`、结果状态。

**绝不写入**：零件号（`part_no`）、规格 ID/标题、来源引用（`source_ref`）、项目组标识（`OEM-A/OEM-B`）、计量单位、原始行 JSON —— 均为跨主机厂敏感内容（图纸/标准归属）。需要原始行调试时，请查隔离区证据存储（`quarantine_records.raw_payload`），其访问受数据库权限与保留规则约束。

## 8. 存量节点 02 数据的血缘口径

节点 02 种子夹具（`SPEC-SR-A-V5` / `SPEC-SR-A-V4` / `SPEC-SR-B-V2` / `SPEC-UNIQ-B` / `SPEC-HS-A-V1` / `SPEC-HS-A-V1B`）先于摄取管道存在。迁移 002 + `application/database.py` 幂等回填其血缘：`batch_id = node02-baseline-seed`、`source_version = version`、`source_id = source_ref` 中 `#` 之前前缀。因此主表**每一条**规格都可追溯 `source_id` / `source_version` / `batch_id`；后续批次若推送与种子相同的业务键（如 `SPEC-SR-A-V5:v5.0`）或占用同 spec_id 的其他源版本，会被正确判为 `DUPLICATE_BUSINESS_KEY`。

## 9. 结论边界 (诚实不夸大)

本合同仅约束「结构合法、语义合规的数据进入可解释终态」。批次 `completed` **不等于**数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态，隔离率是待跟进信号而非失败。MES/QMS/PLM 为 SQLite 合成夹具模拟。
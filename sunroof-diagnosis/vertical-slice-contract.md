# 确定性垂直薄片行为合同 v1（Vertical Slice Behavior Contract）
范围：`POST /api/v1/spec-check`（装配规格核对，零大模型）· 状态：冻结（修改需走合同→测试→实现顺序）
回链：`specs/discovery-brief-v1.md` · `adr-001-deterministic-baseline.md` · `api-contract/spec-check-v1.json`

## 1. 一句话
PQE 提交（SN、零件号、工序、实测值、测量日期），系统在**其授权项目组内**做确定性 SQL 匹配唯一「现行有效」检验规格，计算偏差并给出可引用证据；不可判定时显式拒答，绝不猜。

## 2. 输入契约（封闭 Schema，`extra="forbid"`）
| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `request_id` | string | 必填 | 三向对账键（API 响应 / DB task_runs / 测试） |
| `task_type` | enum | 仅 `spec_check` | 任务类型 |
| `sn` | string | 必填 | 天窗总成 SN（追溯标识，随审计留痕） |
| `part_no` | string | 必填 | 零件业务键（如 `MOTOR-MOUNT-BOLT-03`） |
| `process_step` | enum | `torque_tightening` / `gap_check` | 工序（封闭枚举） |
| `measured_at` | string | 必填，ISO-8601 日期 | 生效窗口判定键 |
| `measured_value` | number | > 0 | 实测值（N·m 或 mm，按工序） |

> 请求体不得出现 `role`/身份字段（`extra="forbid"` → 422）；身份仅来自 `X-Auth-Token`。

## 3. 判定表（服务端确定性分支，按序短路）
| # | 条件 | 终态 | reason_code | 响应内容 | 审计 matched_spec_id |
|---|---|---|---|---|---|
| 1 | 身份未知（未注册 Token） | 401 | — | 仅错误信息 | 不落库 |
| 2 | 字段缺失/自报身份/未知字段 | 422 | — | 校验错误定位 | 不落库 |
| 3 | 唯一现行规格命中且 \|偏差%\| > 15.0 | `needs_review` | `SPEC_HARD_LIMIT_EXCEEDED` | 仅复核预览（**不含任何规格内容**） | NULL |
| 4 | ACL 内零条现行规格（含仅剩已被替代/过期版本） | `needs_review` | `NO_MATCHING_SPEC` | 仅复核预览 | NULL |
| 5 | ≥2 条现行规格且版本不同 | `needs_review` | `SPEC_CONFLICT` | 复核预览（列出版本号，均在请求者授权范围内） | NULL |
| 6 | 唯一现行规格命中且 \|偏差%\| ≤ 15.0 | `answered` | NULL | 规格出处（spec_id/version/effective_at/source_ref/公式）+ 偏差与 `conformance` 判定 | spec_id |

> 偏差定义：`deviation_pct = (measured_value - nominal) / nominal × 100`（保留 2 位小数）。
> `conformance`：`conforming` 当且仅当 `nominal - tol_minus ≤ measured_value ≤ nominal + tol_plus`。
> 硬限常量 `HARD_LIMIT_DEVIATION_PCT = 15.0` 属引擎侧公开规则，不属于受控文档内容。

## 4. 终态语义（冻结）
- `answered` ≠ 合格。它只表示：**依据充分，可出具带引用的分析草稿**；`conformance=nonconforming` 的不合格判定本身就是 `answered` 报告的证据内容（如 8.2 N·m vs 9.5±0.5）。
- `needs_review` = **拒绝出具**：切回人工（质量主管复核预览只给入口与原因，不给受限正文）。
- `denied` 为保留值，本薄片不产生（越权在身份层即被 401/ACL 排除）。
- `escalate`（不泄露受限文档名称的安全升级）留待后续节点实现，本阶段以 `needs_review` 承载并在响应预览中只给常规申请入口。

## 5. 安全边界（全部可被测试证明）
1. 身份只来自服务端 `X-Auth-Token` → (user_id, user_role, allowed_oem_projects)；未知 Token 401 且不落库；
2. ACL SQL 下推：`oem_project IN (allowed_oem_projects)`；质量主管 `["*"]` 跨项目只读；
3. 跨项目零泄漏：拒答路径响应体只含请求者自己的输入与公开常量，**不含**其他项目组的 spec_id/标题/来源/项目组名；
4. `is_current=0`（已被替代/过期）行在 SQL 层被排除，任何路径都不会将其作为候选返回；
5. 外部写效果 = 0：全链路唯一写副作用是追加 `task_runs` 审计行；
6. 阻断路径（分支 3/4/5）审计 `matched_spec_id = NULL`，与响应内容一致（不留隐性依据）。

## 6. 三向对账
每次请求：API 响应 `status/reason_code/matched` ≡ `task_runs` 同 `request_id` 行 ≡ 测试断言，三者必须一致。

## 7. 已知边界（诚实标注）
- **B-1**：按「现行有效规格」判定（保护指标 P-2 优先），不按生产时点规格回溯——历史日期落在旧版本窗口时 `NO_MATCHING_SPEC` 转人工；
- **B-2**：MES/QMS/PLM 为 SQLite 合成夹具模拟，SN 自动抓取（只读 MCP）留待阶段 C；
- **B-3**：`abstained` / `escalate` 终态命名与 reason_code 全集在生成层节点扩展，本合同的原因码是其子集，扩展不改既有语义。

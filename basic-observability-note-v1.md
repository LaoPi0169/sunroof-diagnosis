# 基础可观测性说明 v1 (basic-observability-note-v1)

- **日期**: 2026-09-30
- **适用范围**: Week 08 发布 `northstar-app-w08-r0` 及其后发布（回链 specs/mission-week-08.md 第 5 天）
- **定位**: 工程底座阶段的最小可观测性规范 —— 只定义已实现且被测试约束的内容，不做未落地的承诺。

## 1. 关联标识传递（可观测性的骨架）

| 标识 | 产生位置 | 传播路径 | 用途 |
| --- | --- | --- | --- |
| `request_id` | 客户端/工作台 | `/api/v1/policy-check` 请求体 → 引擎 → `task_runs.request_id` | 单次核对审计 |
| `correlation_id` | 任务动作请求体 | 动作端点 → `task_action_runs.correlation_id`；check 动作同时作为 `request_id` 落 `task_runs` | 一条业务链的三向对账锚点（响应/终态/审计） |
| `idempotency_key` | 升级/结案请求体 | `billing_tasks.idempotency_key`（唯一） | 幂等重放判定（ALREADY_APPLIED） |

冒烟与演练全部经 `corr-smoke-01..04` / `corr-rollback-resume` 关联，任何一条断言都能回溯到原始请求。

## 2. 结构化任务事件（数据库审计即事件流）

服务端**不打印业务日志到控制台**（uvicorn 仅输出框架访问日志）；任务事件的结构化留痕统一落在数据库：

- `task_action_runs`: `(task_id, correlation_id, user_id, user_role, action, status, reason_code, created_at)`
- `task_runs`（Week 05 引擎审计）: `(request_id, region, amount, ..., status, matched_policy_id)`

可观测查询 = 对这两张表的 SQL 查询；`correlation_id` 是与响应体对账的连接键。本规范刻意不引入
额外日志系统 —— 理由：审计表已被幂等迁移管理、可对账、可测试，优于散落的 stdout 文本。

## 3. 错误码与终态映射（Reason Code Taxonomy）

| reason_code | HTTP | 业务终态 | 含义与排查入口 |
| --- | --- | --- | --- |
| `SUPPORTED_CURRENT_SOURCES` | 200 | `answered`/`confirmed`/`action_completed` | 唯一命中现行 SOP；查 `task_runs.matched_policy_id` |
| `AMOUNT_EXCEEDS_LIMIT` | 200 | `needs_review`/`escalated` | 金额超一线权限门槛；查 `billing_tasks.amount` 与阈值政策 |
| `POLICY_CONFLICT` | 200 | `needs_review` | 多版本同时命中；**首选排查 `policies` 表 deprecated/effective/expiry 数据**（见 rollback-drill Round 1） |
| `NO_MATCHING_POLICY` | 200 | `needs_review` | 无命中；查摄取与配置收敛 |
| `ACTION_NOT_ALLOWED` | 200 | `denied`（仅审计，不改工单） | 服务端 RBAC 拦截；查 `task_action_runs.user_role` |
| `ALREADY_APPLIED` | 200 | 原终态 + `is_idempotent=true` | 幂等重放；查 `billing_tasks.idempotency_key` |
| `REFUSED_BY_AGENT` | 200 | `refused` | 一线显式拒绝 |
| `INVALID_TASK_STATE` | 200 | 原终态 | 无效转移，不猜答案；查当前状态与状态机表 |
| `UNAUTHORIZED_ACCESS` | 401 | — | 缺失/无效 `X-Auth-Token` |
| `MISSING_REQUIRED_PARAM` 等 422 | 422 | — | 封闭 Schema 校验失败（extra=forbid） |

## 4. 日志脱敏与安全边界（含 Week 08 的裁决记录）

**硬规则（由 `ci/redaction_scan.py` 在 CI 强制，动态断言由测试复验）**:

1. **凭据**: `token_agent_valid` / `token_supervisor_secret` / `X-Auth-Token` 不得出现在任何运行时输出语句；
   凭据常量仅允许在 `application/auth.py`（定义）、`workbench_cli.py`、`deploy/smoke_w08.py`（本人凭证发起请求）、
   `ci/redaction_scan.py`（扫描器清单）。
2. **政策正文**: 标题/公式等正文不得出现在服务端与工具链（application/*、deploy/*、ci/*）运行时输出与审计表；
   审计只存 `policy_id`/`version` 等结构化标识。
3. **客户 PII**: 本系统输入仅为结构化工单字段（region/amount/purchased_at），不含姓名/卡号等 PII；
   未来任何新增字段必须先过脱敏扫描规则再合入。

**裁决记录（2026-09-30, CI Run 2）**: `workbench_cli.py:88` 向操作者本人终端打印其已授权响应中的
`candidate_policy`（含标题/公式）被扫描器标记。裁决：CLI 是客户端工作台，向**本人**展示**本人已授权**
的响应内容属「授权展示」而非日志泄漏（Week 07 SOP 人工核验 UX 既定设计）；服务端从不主动记录正文。
据此扫描器把正文类规则限定在服务端与工具链范围，`workbench_cli.py` 显式豁免并留注释。

**证据**: `ci-evidence.md`（三轮运行，含两次真实拦截）、`evidence/ci-run-w08.log`（原始记录）。

## 5. 健康模型：/health 与任务级冒烟的分工

- `/health` 200 = 进程存活 + 配置加载成功。**它不能证明业务正确** —— rollback-drill Round 1 实证：
  坏配置复活已废弃政策时 `/health` 仍为 200。
- 业务正确性由部署验收门 `deploy/smoke_w08.py`（4 条任务冒烟，三向对账）证明；两者缺一不可，
  部署放量以 4/4 冒烟通过为准（deploy-README 第 6 节）。

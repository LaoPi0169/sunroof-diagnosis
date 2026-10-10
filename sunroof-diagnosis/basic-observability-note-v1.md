# 基础可观测性说明（Basic Observability Note v1）

- **规范版本**: `sunroof-observability-v1`（回链 `specs/mission-week-08.md` 第 5 天验收标准「敏感信息不入日志」）
- **适用范围**: 阶段 A（节点 02-05）确定性单体应用

## 1. 三向对账关联标识（最核心的可观测性契约）

| 标识 | 载体 | 生成方 | 用途 |
| --- | --- | --- | --- |
| `request_id` | `/api/v1/spec-check` 请求 + `task_runs` 审计 | 客户端 | 引擎级对账（响应/DB/测试） |
| `correlation_id` | 任务动作请求 + `spec_check_tasks` + `task_action_runs` | 客户端 | 任务级三向对账（HTTP 响应 / 任务终态 / 动作审计流水） |
| `run_id` | 审计行主键（`run-{uuid12}`） | 服务端 | 单条审计事件定位 |

规则：`correlation_id` 贯穿一次业务动作的全部留痕；冒烟固定使用 `corr-smoke-01..04`；
测试与演练证据全部以 correlation_id 汇集对账（`get_task_action_runs_by_correlation`）。

## 2. 结构化终态与原因码（业务可观测口径）

任务动作审计行：`(task_id, correlation_id, user_id, user_role, action, status, reason_code, created_at)`。

| `status` | `reason_code` | 含义 | 排查指向 |
| --- | --- | --- | --- |
| `answered` | NULL | 正常作答（带引用证据） | 无误 |
| `needs_review` | `SPEC_HARD_LIMIT_EXCEEDED` | 偏差超硬限（运行时阈值来自 `policy_config`） | 配置是否被改 → `release.config_version` |
| `needs_review` | `SPEC_CONFLICT` | 多条现行规格版本冲突 | 规格主数据治理 |
| `needs_review` | `NO_MATCHING_SPEC` | ACL 内零条现行有效规格 | 零件/日期/项目组 ACL |
| `escalated` | NULL / `ALREADY_APPLIED` | 升级成功 / 幂等重放 | 重放是否预期 |
| `confirmed` / `refused` / `action_completed` | — | 一线终态 / 主管结案终态 | 状态机流转 |
| `denied` | `ACTION_NOT_ALLOWED` | 越权阻断（不改变任务状态） | 安全审计复查 |
| — | `INVALID_TASK_STATE` | 状态机非法转移拒绝 | 调用顺序 |

## 3. 日志脱敏红线（`ci/redaction_scan.py` 静态门 + 动态测试双防线）

运行时输出语句（print/logging）**绝对禁止**：
- 凭据：`token_pqe_oem_a` / `token_pqe_oem_b` / `token_qa_supervisor_secret` / `X-Auth-Token`；
- 规格正文：规格标题、`legacy_rule_v4` 等（客户端 CLI 的授权展示豁免——展示给本人已授权内容属 UX，服务端永不记录）；
- 客户 PII / SOP 正文（本就不入库，防线保证不出现在日志）。

审计表仅存 ID 与状态（`matched_spec_id`、`reason_code`），正文类内容零落库——由
SMOKE-03 零泄漏断言与 `tests/` 动态用例复验。扫描范围 11 文件（应用 6 + CLI + 部署 2 + CI 2）。

## 4. 健康与部署可观测性

- `/health` 仅表示进程存活——**不得**作为业务健康依据（放量以冒烟 4/4 为准）；
- 配置漂移排查入口：`policy_config.release.config_version`（当前生效配置版本）+
  收敛器 JSON 报告（逐项 `converged`）；
- CI 证据：`evidence/ci-run-w08.log`（每次运行追加）+ `ci-evidence.md`（人工汇总）。

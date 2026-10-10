# 任务级冒烟测试结果（Task-Level Smoke Results v1）

- **规范版本**: `sunroof-smoke-results-v1`（回链 `specs/mission-week-08.md` 第 3 天验收标准）
- **运行命令**: `python deploy/smoke_w08.py --base-url http://127.0.0.1:<port> --db <全新初始化库>`
- **运行环境**: 干净环境冷部署实例（与 CI STAGE-4 同路径），应用 `sunroof-config-w08-v1` 后执行
- **结果**: **4/4 PASS**（本文件为真实运行记录的转写；JSON 明细可随时复现）

## 1. 核心声明

`/health` 返回 200 **不作为任何通过依据**。以下每条冒烟同时断言
「一线用户可见响应 + `spec_check_tasks` 数据库终态 + 动作审计
（check_spec 另有节点 02 `task_runs` 审计）」，经同一 `correlation_id`（corr-smoke-01..04）三向对账。

## 2. 逐条结果

### SMOKE-01 常规任务核对并确认（TASK-N04-001, +1.05% 偏差）✅
- check_spec → HTTP 200 `answered`，命中 `SPEC-SR-A-V5`，`deviation_pct=1.05`；
- DB：`spec_check_tasks.status=answered`，`matched_spec_id=SPEC-SR-A-V5`；
- 审计：`task_runs`（引擎）answered + `task_action_runs` 2 条
  `[(check_spec, answered), (confirm, confirmed)]`，同一 correlation_id 对上账。

### SMOKE-02 超硬限任务阻断并升级（TASK-N04-002, +21.05% 偏差）✅
- check_spec → HTTP 200 `needs_review / SPEC_HARD_LIMIT_EXCEEDED`，`candidate_spec=None`（零规格内容）；
- DB：`status=needs_review`，`matched_spec_id` 恒 NULL（零隐性依据）；
- escalate → `escalated`；审计 2 条 `[(check_spec, needs_review), (escalate, escalated)]`。

### SMOKE-03 PQE 越权调用主管结案（服务端一票否决，节点 ④ 能力不退化）✅
- PQE 直调 `supervisor_resolve` → HTTP 200 `denied / ACTION_NOT_ALLOWED`；
- **零泄漏**：denied 响应全文不含规格 ID（`SPEC-SR-A-V5`）、规格标题正文、主管凭据
  （泄漏模式表见 `deploy/smoke_w08.py` `LEAK_PATTERNS`）；
- 越权不改变任务状态（仍 `escalated`）；阻断事件已审计 `(supervisor_resolve, denied, pqe)`。

### SMOKE-04 相同幂等键重复提交升级（不产生重复生效，节点 ④ 能力不退化）✅
- 重放 → HTTP 200 `escalated + is_idempotent=true + ALREADY_APPLIED`；
- DB：仍为首条 `escalated`，幂等键唯一（`idem-smoke-02-escalate`）；
- 无重复生效：首次升级审计仍 2 条，重放仅新增 ALREADY_APPLIED 审计 1 条。

## 3. 与放量门的关系

- 冒烟 4/4 且退出码 0 是唯一放量依据；任一条失败 → 部署自动阻断（CI STAGE-4 同门）；
- 错误配置下的行为见 `rollback-drill-v1.md`：SMOKE-01 是配置漂移的指定检测器；
- 复验纪律：任务终态不可重放，每次冒烟必须使用全新初始化的数据版本。

# Week 08 回滚演练报告 (rollback-drill-v1)

- **日期**: 2026-09-30
- **演练目标**（回链 specs/mission-week-08.md 第 4 天）: 受控注入坏配置 → 证明任务级冒烟能发现"假成功" → 执行回滚 → 复验系统恢复，全程不删库、不重建冻结表、不改 DDL。
- **演练环境**: 干净部署目录 `D:\WebstormProjects\fde-w08-clean-deploy\app`（Day 3 同一发布物，`git archive` 导出 + 独立 venv），演练库 `northstar-drill.db`。

## 0. 基线（Round 0）

全新库 `northstar-drill.db` → `init_db()` → 应用 known-good 配置（3/3 converged）。
policies 快照（基线口径）：

| policy_id | is_deprecated | effective_at | expires_at |
| --- | --- | --- | --- |
| POL-2025-BIL-01 | **1** | 2025-01-01 | **2025-12-31** |
| POL-2026-BIL-01 | 0 | 2026-01-01 | (null) |
| POL-2026-REF-09 | 0 | 2026-07-01 | (null) |

表清单 7 张：billing_tasks / ingestion_batches / ingestion_replay_logs / policies / quarantine_records / task_action_runs / task_runs。

## 1. 注入（Round 1）：应用错误配置 rc1-bad

`apply_policy_config.py --config config/policy-config-rc1-bad.json` → 收敛器对 3 条基线 + 1 条 override 全部报告
`converged: true`，退出码 0。数据库差异：

```
POL-2025-BIL-01: is_deprecated 1→0, expires_at 2025-12-31→2027-12-31   (已废弃旧政策被复活)
```

> **核心教学点**: 收敛器只保证「数据库 == 配置」，不保证「配置正确」。配置层的错误
> 在收敛器与 `/health` 看来一切正常 —— 正确性防线在任务级冒烟。

## 2. 假成功证据 + 冒烟拦截

- `GET /health` → **HTTP 200** `{"status":"ok"}` —— 进程视角完全健康（假成功）。
- `deploy/smoke_w08.py` → **退出码 1，3/4 通过，部署门拦截**。SMOKE-01 七项断言全部失败：

```
FAIL: check 可见响应 answered        [HTTP 200, status=needs_review]
FAIL: 匹配 POL-2026-BIL-01 v3 + SOP 来源  [policy=None None src=None]
FAIL: confirm 可见响应 confirmed     [HTTP 200, status=needs_review]
FAIL: billing_tasks 终态 confirmed   [db=needs_review/None]
FAIL: task_action_runs 审计 2 条且动作有序  [runs=[(check_policy, needs_review), (confirm, needs_review)]]
FAIL: Week 05 task_runs 审计衔接 (check 步)  [task_runs=(needs_review, None)]
```

业务链条取证（billing_tasks 直查）: `TASK-W07-001 → needs_review / POLICY_CONFLICT / matched_policy_id=None`
（华东/2026-08-15 同时命中 v1+v3 → 版本冲突 → 一线只能升级）。SMOKE-02/03/04 仍通过：金额门槛、RBAC、幂等
与政策数据解耦 —— 拦截面精准、无假阴性扩散。

## 3. 回滚（Round 2）：重放 known-good 配置

`apply_policy_config.py --config config/policy-config.json` → 3/3 `converged: true`。

| 完整性检查项 | 结果 |
| --- | --- |
| policies 断言恢复 | ✅ 与 Round 0 快照逐项一致（POL-2025-BIL-01 回到 deprecated/2025-12-31） |
| 表清单 | ✅ 7 张表原样，无 DDL 变更（schema_version 保持 003） |
| 业务数据保留 | ✅ billing_tasks 仍 4 行，无删库、无重建 |
| 代码/契约 | ✅ 零改动（回滚只动配置数据三列白名单） |

## 4. 复验（Round 3）—— 分层验证与一个重要的负结果

### 4.1 负结果（如实归档）: 同一数据库上二次冒烟 ≠ 复验通过

回滚后直接在原演练库重跑 4 条冒烟 → 3 项断言类失败（响应/终态正确，但审计计数与状态前置不满足）。
根因（三条叠加，均为系统正确语义）:
1. `task_action_runs` 审计只增不改 —— 第二轮 runs 计数为 4 而非 2；
2. `check_policy` 无状态前置守卫（重评估语义）—— 把 `escalated` 工单重新评估回 `needs_review`；
3. 同幂等键重放走 `ALREADY_APPLIED` 短路 —— 不会再触发状态升级。

**结论**: 任务级冒烟的前置条件是"全新初始化数据库、部署后运行一次"（deploy-README 第 5/6 步已注明）。
"在同一库上重跑冒烟"不是合法的复验方式 —— 这是本次演练用负结果换来的最重要认知。

### 4.2 同库业务语义正确性

对已偏离状态的工单 `TASK-W07-002`（needs_review 而非 escalated）执行主管结案 → 服务端返回
`needs_review / INVALID_TASK_STATE` 语义（无效转移不猜答案、不改终态）—— 回滚后的系统行为契约依旧成立。

### 4.3 全新库完整复验（正式复验口径）

删除演练库 → `init_db()` → 应用 known-good 配置（3/3 converged）→ 独立实例（:8004）→ `smoke_w08.py`:

```
SMOKE_EXIT_REVERIFY=0     all_passed=true     4/4 PASS
```

最终快照: policies 与 Round 0 一致；表清单 7 张不变；billing_tasks 4 行；
`task_action_runs=6`（新库仅本轮 6 条审计）、`escalated_count=1`。

## 5. 结论与边界

**已证明**:
1. 在独立沙盒环境，配置层错误（复活已废弃政策）能被任务级冒烟在部署门处拦截 —— `/health` 200 的假成功被识别；
2. 回滚 = 重放 known-good 配置的幂等收敛，数据库兼容性完整保留（无 DDL 变更、无数据丢失）；
3. 回滚后系统在全新库上与发布基线行为完全一致（4/4 冒烟）。

**不能证明（非目标）**:
1. 不证明生产环境大流量热回滚能力；
2. 不证明未来数据库 DDL 迁移可逆（本周回滚策略明确不含 schema 回退）；
3. 不证明配置内容本身的正确性可自动判定 —— 配置评审是人工防线，冒烟是自动化防线。

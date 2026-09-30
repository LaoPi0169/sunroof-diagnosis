# 工单任务状态模型 v1（Week 07）

回链：`specs/mission-week-07.md` 第 1 天交付物。本文是 `billing_tasks.status` 状态机的唯一事实说明；实现位于 `application/tasks.py`，测试钉住每个转移。

## 1. 状态定义

| 状态 | 含义 | 进入方式 |
| --- | --- | --- |
| `initialized` | 工单已建，未核对 | 迁移 003 夹具（服务端预置） |
| `answered` | 引擎唯一命中当前有效政策，已回填 `matched_policy_id` | `check_policy` 成功 |
| `needs_review` | 显式异常：金额超限 / 零匹配 / 版本冲突（继承 Week 05 四分支映射） | `check_policy` 异常分支 |
| `confirmed` | 一线依据核对结果确认执行（终态之一） | `confirm` |
| `refused` | 一线显式拒绝（终态之一，ADR-003 适配点 3） | `refuse` |
| `escalated` | 已进入主管复核队列（记录幂等键） | `escalate` |
| `action_completed` | 主管复核结案（终态之一） | `supervisor_resolve` |
| `denied` | **仅存在于 task_action_runs 审计**，不是工单状态：越权动作被服务端阻断 | RBAC 拒绝 |

## 2. 状态转移图

```
initialized ──check_policy──▶ answered ──confirm──▶ confirmed (终态)
     │                            │  │
     │                            │  └──refuse───▶ refused (终态)
     │                            └───escalate──▶ escalated ──supervisor_resolve──▶ action_completed (终态)
     │                                             ▲
     └──check_policy(异常)──▶ needs_review ──escalate─┘

任何动作被 RBAC 拒绝 ──▶ denied (仅审计，工单状态不变)
授权通过但状态不匹配 ──▶ INVALID_TASK_STATE (工单状态不变，审计留痕)
```

## 3. 合法转移表（实现即此表，无隐藏转移）

| 动作 | 前置状态 | 结果状态 | 原因码 | 幂等行为 |
| --- | --- | --- | --- | --- |
| `check_policy` | initialized / 任意非终态 | answered 或 needs_review | SUPPORTED_CURRENT_SOURCES / AMOUNT_EXCEEDS_LIMIT / NO_MATCHING_POLICY / POLICY_CONFLICT | 引擎确定性，重复核对结果相同 |
| `confirm` | answered | confirmed | SUPPORTED_CURRENT_SOURCES | 已 confirmed 再提交 → ALREADY_APPLIED + `is_idempotent=true` |
| `refuse` | answered | refused | REFUSED_BY_AGENT | 已 refused 再提交 → ALREADY_APPLIED |
| `escalate` | answered / needs_review | escalated | 继承任务原因码，或请求显式指定；缺省 SPECIAL_TASK_REQUIRES_REVIEW | 状态已 escalated 或同幂等键 → ALREADY_APPLIED |
| `supervisor_resolve` | escalated | action_completed | SUPPORTED_CURRENT_SOURCES | 已结案再提交 → ALREADY_APPLIED（主管防重复点击） |

## 4. 显式失败路径（不猜答案原则）

| 场景 | 结果 | 工单状态 |
| --- | --- | --- |
| 客服调用 `supervisor_resolve` | 响应 `denied` / ACTION_NOT_ALLOWED，`candidate_policy=None` | 不变（审计留痕） |
| `confirmed` 态再 `refuse`（等任何非法转移） | 响应当前状态 / INVALID_TASK_STATE | 不变（审计留痕） |
| 未知 task_id | HTTP 404（端点层拦截，零写入） | 不存在 |
| 请求体含自报角色等未知字段 | HTTP 422（`extra="forbid"`，校验层拦截，零审计） | 不变 |

## 5. 审计锚点与三向对账

每个动作（含越权、无效转移、幂等重放）都落一行 `task_action_runs`：

```
HTTP 响应 (correlation_id, status, reason_code)
        ║
billing_tasks.status / reason_code / matched_policy_id   ← 终态事实
        ║
task_action_runs (correlation_id, action, status, user_id, user_role)
```

三者经同一 `correlation_id` 对账。`check_policy` 同时在 Week 05 `task_runs` 表留审计行（001 的 CHECK 约束内合法状态），形成跨周血缘衔接（ADR-003 适配点 1）。自动化证据：tests/test_frontline_tasks.py 用例 1、5。

## 6. 诚实边界

- 状态机为**单库单事务、串行请求**设计；并发下同一工单的竞态（如同时 escalate + confirm）未引入行锁，幂等键是第一道防线，生产化需补乐观锁（decision-memo-02 候选项）；
- `refused` / `confirmed` 为终态，无"重开工单"转移——课程范围内一步闭环，撤销属未来需求；
- `escalated` 允许从 `answered` 或 `needs_review` 两态进入：一线可能核对成功后仍决定升级，这是业务允许的显式决定。

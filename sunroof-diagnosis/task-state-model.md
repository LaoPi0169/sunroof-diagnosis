# 任务状态模型 v1（阶段 A 节点 04）

回链：`specs/role-and-permission-matrix.md` §2（冲突/硬限裁决.人工复核：PQE 发起 / 主管裁决）。
本文是 `spec_check_tasks.status` 状态机的唯一事实说明；实现位于 `application/tasks.py`，测试钉住每个转移（tests/test_frontline_tasks.py）。

## 1. 状态定义

| 状态 | 含义 | 进入方式 |
| --- | --- | --- |
| `initialized` | 任务已建（服务端夹具预置事实：SN/零件/工序/实测值），未核对 | 迁移 003 夹具 |
| `answered` | 引擎唯一命中现行有效规格，已回填 `matched_spec_id` | `check_spec` 成功 |
| `needs_review` | 显式异常：硬限超限 / 零匹配 / 版本冲突（继承节点 02 判定表三码） | `check_spec` 异常分支 |
| `confirmed` | PQE 依据核对结果确认执行（终态之一） | `confirm` |
| `refused` | PQE 显式拒绝（终态之一） | `refuse` |
| `escalated` | 已进入质量主管复核队列（记录幂等键） | `escalate` |
| `action_completed` | 质量主管复核结案（终态之一） | `supervisor_resolve` |
| `denied` | **仅存在于 task_action_runs 审计**，不是任务状态：越权动作被服务端阻断 | RBAC/任务级 ACL 拒绝 |

## 2. 状态转移图

```
initialized ──check_spec──▶ answered ──confirm──▶ confirmed (终态)
     │                          │  │
     │                          │  └──refuse───▶ refused (终态)
     │                          └───escalate──▶ escalated ──supervisor_resolve──▶ action_completed (终态)
     │                                           ▲
     └──check_spec(异常)──▶ needs_review ──escalate─┘

任何动作被 RBAC/任务级 ACL 拒绝 ──▶ denied (仅审计，任务状态不变)
授权通过但状态不匹配 ──▶ INVALID_TASK_STATE (任务状态不变，审计留痕)
```

## 3. 合法转移表（实现即此表，无隐藏转移）

| 动作 | 前置状态 | 结果状态 | 原因码 | 幂等行为 |
| --- | --- | --- | --- | --- |
| `check_spec` | initialized / 任意非终态 | answered 或 needs_review | SUPPORTED_CURRENT_SOURCES / SPEC_HARD_LIMIT_EXCEEDED / NO_MATCHING_SPEC / SPEC_CONFLICT | 引擎确定性，重复核对结果相同 |
| `confirm` | answered | confirmed | SUPPORTED_CURRENT_SOURCES | 已 confirmed 再提交 → ALREADY_APPLIED + `is_idempotent=true` |
| `refuse` | answered | refused | REFUSED_BY_AGENT | 已 refused 再提交 → ALREADY_APPLIED |
| `escalate` | answered / needs_review | escalated | 继承任务原因码，或请求显式指定；缺省 SPECIAL_TASK_REQUIRES_REVIEW | 状态已 escalated 或同幂等键 → ALREADY_APPLIED |
| `supervisor_resolve` | escalated | action_completed | SUPPORTED_CURRENT_SOURCES | 已结案再提交 → ALREADY_APPLIED（主管防重复点击） |

## 4. 显式失败路径（不猜答案原则）

| 场景 | 结果 | 任务状态 |
| --- | --- | --- |
| PQE 调用 `supervisor_resolve` | 响应 `denied` / ACTION_NOT_ALLOWED，`candidate_spec=None` | 不变（审计留痕） |
| 跨项目 PQE 直调他组任务（任务级 ACL，adr-003 D6） | `denied`，case_id 回通用常量 `N/A-ACL-DENIED`，零回显 | 不变（审计留痕） |
| `confirmed` 态再 `refuse`（等任何非法转移） | 响应当前状态 / INVALID_TASK_STATE | 不变（审计留痕） |
| 未知 task_id | HTTP 404（端点层拦截，零写入） | 不存在 |
| 请求体含自报角色等未知字段 | HTTP 422（`extra="forbid"`，校验层拦截，零审计） | 不变 |

## 5. 审计锚点与三向对账

每个动作（含越权、无效转移、幂等重放）都落一行 `task_action_runs`：

```
HTTP 响应 (correlation_id, status, reason_code)
        ║
spec_check_tasks.status / reason_code / matched_spec_id   ← 终态事实
        ║
task_action_runs (correlation_id, action, status, user_id, user_role)
```

三者经同一 `correlation_id` 对账。`check_spec` 同时在节点 02 `task_runs` 表留审计行（001 的 CHECK 约束内合法状态），形成跨节点血缘衔接（adr-003 适配点 1/5）。自动化证据：tests/test_frontline_tasks.py 用例 1、5。

## 6. 诚实边界

- 状态机为**单库单事务、串行请求**设计；并发下同一任务的竞态（如同时 escalate + confirm）未引入行锁，幂等键是第一道防线，生产化需补乐观锁（decision-memo-02 候选项）；
- `refused` / `confirmed` 为终态，无"重开任务"转移——课程范围内一步闭环，撤销属未来需求；
- `escalated` 允许从 `answered` 或 `needs_review` 两态进入：PQE 可能核对成功后仍决定升级，这是业务允许的显式决定；
- 质量主管结案动作在原系统仍有人工流程（矩阵 §2），`action_completed` 只代表本系统内复核闭环完成。

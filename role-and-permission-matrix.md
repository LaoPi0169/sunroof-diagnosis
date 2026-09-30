# 角色与权限矩阵 v1（Week 07）

回链：`specs/mission-week-07.md` 第 1 天交付物。本文回答一个问题：**每个角色对每个任务动作，服务端是允许还是拒绝？** 权限判定 100% 发生在服务端（`application/auth.py`），前端/CLI 的任何角色展示或输入都不作为安全屏障。

## 1. 角色定义

| 角色 | user_role 值 | 身份来源 | 现实含义 |
| --- | --- | --- | --- |
| 一线客服 | `support_agent` | 服务端 Token 校验（`token_agent_valid`；缺省 Token = 模拟会话默认客服身份） | 接触客户、执行计费/退款核对的人 |
| 主管 | `supervisor` | 服务端 Token 校验（`token_supervisor_secret`） | 复核一线升级的例外事项并结案 |

> 教学边界：Token 是模拟会话凭证，不是生产级 SSO/OAuth。当前目标只有一个：证明**身份必须来自服务端、且必须被服务端强制**。生产化路径见 decision-memo-02。

## 2. 任务动作 × 角色授权矩阵

| 任务动作 | support_agent | supervisor | 说明 |
| --- | --- | --- | --- |
| `check_policy` | ✅ 允许 | ✅ 允许 | 触发 Week 05 确定性引擎，把工单事实（地区/金额/购买时间）参数化核对 |
| `confirm` | ✅ 允许（仅 `answered` 态） | ✅ 允许 | 依据核对结果确认执行；非 answered 态 → `INVALID_TASK_STATE` |
| `refuse` | ✅ 允许（仅 `answered` 态） | ✅ 允许 | 显式拒绝（ADR-003 适配点 3：mission 参考实现遗漏、验收标准第 4 条要求） |
| `escalate` | ✅ 允许（`answered` / `needs_review` 态） | ✅ 允许 | 携幂等键升级主管；重复提交 → `ALREADY_APPLIED` |
| `supervisor_resolve` | ❌ **拒绝（denied）** | ✅ 允许（仅 `escalated` 态） | 主管专属闭环动作，客服直调 → 审计留痕、状态不变 |

规则（`authorize_action`，Default-Deny）：
1. 矩阵未显式写 ✅ 的 `(角色, 动作)` 组合一律拒绝 —— **默认拒绝是兜底规则，不是例外**；
2. 被拒绝的动作走 `denied` 路径：`candidate_policy` 恒为 `None`、响应零政策内容、审计表落一行 `ACTION_NOT_ALLOWED` 事件（含 user_id/user_role/action/task_id）；
3. 状态前置条件由任务状态机二次把关（见 task-state-model.md），授权通过但状态不匹配 → `INVALID_TASK_STATE`，同样不改变工单终态。

## 3. 数据可见性（复用 Week 05 ACL，不新增授权面）

| 数据 | support_agent | supervisor |
| --- | --- | --- |
| 普通客服政策（如 POL-2026-BIL-01） | ✅ | ✅ |
| 主管专属政策（POL-2026-REF-09） | ❌ SQL 层被 ACL 隔离 | ✅ |
| 越权拒绝响应中的政策内容 | ❌ 恒为空 | —（本就不触发拒绝路径） |

实现复用 `database.query_policies(region, purchased_at, user_role)` 的既有 ACL 参数，Week 07 未新建任何平行 ACL（ADR-003 适配点 2）。

## 4. 身份伪造的三层防线（对应验收标准：前端绝不是安全屏障）

| 攻击手法 | 服务端防线 | 自动化测试证据 |
| --- | --- | --- |
| 请求体自报 `role: "supervisor"` | `TaskActionRequest` 使用 `extra="forbid"` 封闭契约 → 直接 422，请求在校验层被拒，零审计、零状态变更 | tests/test_frontline_tasks.py 用例 6 |
| 客服身份直调主管动作 | `authorize_action` 一票否决 → denied 路径，响应与审计均无政策内容 | 用例 4 |
| 未知 Token | `get_trusted_user_from_session` → 401，不执行、不落库 | 用例 7（ Week 05 语义原样保留） |

## 5. 诚实边界

- Token 校验是**模拟**实现（硬编码两个教学凭证），课程目标在"服务端强制"，不在凭证基础设施；
- 审计表记录的是动作流水，不做防篡改签名/链式哈希（见 decision-memo-02 的生产化候选）；
- 权限矩阵是代码级实现 + 本文档人工对照，尚未做到"文档即配置自动生成"——两者漂移风险靠测试矩阵（用例 4/6/7）钉住。

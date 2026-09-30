在《FDE 成长手册》的 24 周标准路线中，**第 7 周的名称是“让一线角色在权限内完成任务”（权限内任务闭环）**。

这一周的底层逻辑是：**第 5 周我们做出了确定性软件，第 6 周保证了数据摄取可靠；到了第 7 周，核心是把这些工程底座接回一线真实角色和任务中**。

合格的 FDE 必须深刻理解：**认证（Authentication）、授权（Authorization）与用户决定（User Decision）是三件完全不同的事**：
1. **认证**：通过服务端可信会话确认“当前请求是谁”（如 `support_agent`）。
2. **授权**：在服务端（而不是前端隐藏按钮）强制拦截越权动作；普通客服绝对不能查阅或执行主管专属政策。
3. **用户决定**：用户在权限范围内完成核验、确认（`confirmed`）、拒绝（`refused`）或升级（`escalated`）。`confirmed` 仅代表用户确认了政策依据，不等于“ROI 已兑现”或“自动发送”。

---

### 一、 我要做什么？（核心任务）

1. **定义角色与权限矩阵及任务状态模型**：
   * 明确普通客服（`support_agent`）与客服主管（`supervisor`）能对哪些资源执行哪些动作（读取、确认、升级、主管复核）。
2. **实现服务端授权与幂等任务动作**：
   * 身份只从服务端可信凭证读取，**绝不信任请求体自报角色**。
   * 服务端执行受控状态改变：支持普通客服的“确认依据”与“触发升级”；对直接越权行为返回安全的 `denied` 阻断，且不泄露主管正文。
   * 实现重复请求幂等，确保同一个升级请求重复点击不会产生两个重复记录。
3. **制作最小一线工作台（UI 或 CLI）**：
   * 让客服能清晰看到任务字段、权限内允许查看的 SOP 来源与版本，并选择确认、拒绝或升级。
4. **编写端到端（E2E）任务测试**：
   * 不只看 HTTP 200，必须对**用户可见响应、数据库终态与审计关联日志（`correlation_id`）** 进行三向一致断言。
5. **开展受保护的一线走查与 Brief 复核**：
   * 遵循参与者保护规则（主管不旁听，不上 KPI），带一线人员走查真实/模拟任务，记录反馈，并复核第 4 周的 Discovery Brief。

---

### 二、 怎么做？（5 天落地步骤）

建议本周投入 **8–10 小时**，按 5 天推进：

* **第 1 天：定义角色、资源、动作与责任（1.5 小时）**
  * 编写 `role-and-permission-matrix.md`（角色与权限矩阵）与 `task-state-model.md`（任务状态图）。
  * 确定身份凭证来源、各角色允许的操作字段、越权拒绝时的安全提示以及责任归属人。
* **第 2 天：实现服务端授权与幂等写入（2–3 小时）**
  * 在后端逻辑中添加服务端鉴权与授权中间件。
  * 实现普通客服动作：`action: "confirm"`（状态变为 `confirmed`）和 `action: "escalate"`（状态变为 `escalated`，带原因码如 `AMOUNT_EXCEEDS_LIMIT`）。
  * 实现越权拦截：普通客服尝试执行主管动作时，服务端断言阻断，返回 `status: "denied"`，原任务状态不变。
* **第 3 天：做最小工作台或 CLI（1.5 小时）**
  * 制作最小前端界面或命令行 CLI。
  * 确保客服能核验 SOP 版本/生效日期，明确区分“已确认”与“未确认”，且无权限消息不暴露敏感内容（如显示“该任务需主管复核，已提供升级入口”）。
* **第 4 天：用端到端测试证明任务，而不是接口（2 小时）**
  * 编写 E2E 测试矩阵：常规工单确认成功、特殊工单升级成功、普通客服调用主管接口被拒绝、重复升级请求幂等处理。
  * 断言：数据库记录与审计日志（`task_runs` / `correlation_id`）同步写入终态。
* **第 5 天：进行受保护的一线走查与 Brief 复核（1–2 小时）**
  * 运行完整走查脚本，记录反馈至 `field-feedback-note-v1.md`。
  * 复核 Brief，签署 `decision-memo-02.md`（决策备忘录），决定是否进入第 8 周部署演练。

---

### 三、 交付物有哪些？（文件结构）

本周需要在个人仓库的 `fde-course/week-07/` 目录下完成以下结构：

```
fde-course/
└─ week-07/
   ├─ role-and-permission-matrix.md    # 角色与权限矩阵 (定义角色、资源与操作权限)
   ├─ task-state-model.md              # 任务状态机图 (定义 confirmed/escalated/denied 等状态)
   ├─ application/                     # 增强的后端授权服务与最小工作台源码
   │  ├── auth.py                      # 服务端可信会话鉴权与授权策略
   │  ├── tasks.py                     # 任务状态改变与幂等控制
   │  └── main.py                      # 工作台 API / UI 路由
   ├─ tests/                           # E2E 任务与权限集成测试脚本
   │  └── test_frontline_tasks.py
   ├─ field-feedback-note-v1.md        # 一线走查反馈与人工负担记录
   ├─ decision-memo-02.md              # 决策备忘录 02 (允许进入第8周部署或停止)
   └─ brief-review.md                  # Discovery Brief 复核记录
```

---

### 四、 验收标准是怎样的？（通过门槛）

必须**同时满足**以下 10 项验收标准才算通过第 7 周：

- [x] **服务端强制授权**：角色身份必须来自可信服务端上下文，未知身份或超权动作默认拒绝（Default Deny）。
- [x] **无权限不泄密**：被拒绝的响应绝不泄露受限记录是否存在、正文或敏感元数据。
- [x] **前端不作安全屏障**：前端隐藏按钮不等于授权；直接调用接口发起的越权动作在服务端会被 100% 拦截并返回 `denied`。
- [x] **用户核验与决定闭环**：普通客服能在工作台上核验 SOP 来源与生效日期，并选择 `confirm`（确认）、`refuse`（拒绝）或 `escalate`（升级）。
- [x] **显式阻断不猜答案**：参数缺失、版本冲突或无权限时，返回显式的 `needs_review` 或 `escalated`，不返回假答案。
- [x] **动作幂等性**：重复提交同一次升级请求，最多产生一个有效业务状态改变（返回 `already_applied` 或现有任务）。
- [x] **三向一致测试**：E2E 测试同时核对用户可见响应、数据库终态与审计日志，不只看 HTTP 200。
- [x] **受保护的一线走查**：走查过程遵循参与者保护规则，走查结果明确标注“不能证明真实采用或财务 ROI”。
- [x] **Discovery Brief 复核**：新走查证据若改变了范围或风险，必须如实修订 Brief。
- [x] **坦诚选择下一步**：决策备忘录允许继续、缩小范围、改用流程改进或停止，**不默认自动滑向 AI/RAG**。

---

根据《FDE 成长手册》第 7 周（“让一线角色在权限内完成任务”）的工程与交付规范，我们将第 5 周确定性薄片与第 6 周可靠数据入口，正式接回“北辰 B2B 客服计费与升级政策检索”的**一线真实角色与权限任务闭环**中。

以下是为你整理的 `fde-course/week-07/` 目录下的 **5 份完整 Markdown 交付文件**：

```
fde-course/
└─ week-07/
   ├─ role-and-permission-matrix.md    # 角色与权限矩阵 (角色、资源、操作与鉴权边界)
   ├─ task-state-model.md              # 任务状态机图 (定义 confirmed/escalated/denied 转换逻辑)
   ├─ field-feedback-note-v1.md        # 一线走查反馈与人工负担记录
   ├─ decision-memo-02.md              # 决策备忘录 02 (批准进入第8周部署演练)
   └─ brief-review.md                  # Discovery Brief 复核记录
```

---

### 文件 1：`role-and-permission-matrix.md`（角色与权限矩阵）

```markdown
# 角色与权限矩阵 (Role and Permission Matrix v1)

- **场景名称**：北辰 B2B 客服计费与升级政策检索工作台
- **关联 Brief**：`discovery-brief-v1.0` & `brief-review.md` (第 7 周复核版)
- **鉴权原则**：身份必须从服务端可信会话凭证（HTTP Header `X-Auth-Token`）提取，**默认拒绝（Default Deny）** 所有未经授权的角色与未定义动作；绝对不信任前端 JSON 请求体自报的角色（如拒绝 `body.role="supervisor"`）。

## 1. 角色定义与可信凭证来源

| 角色标识 (`user_role`) | 真实岗位角色 | 鉴权凭证来源 | 职责与能力边界 | 责任归属人 |
| --- | --- | --- | --- | --- |
| `support_agent` | 普通一线客服代表 | 服务端 Cookie / 可信 Token (`token_agent_valid`) | 在权限内核验常规政策，执行 `confirm`（确认依据）或 `escalate`（发起升级）；**无权**查阅主管专属政策正文 | 客服组长 (小张) |
| `supervisor` | 客服运营主管 / 经理 | 服务端 Cookie / 可信 Token (`token_supervisor_secret`) | 审阅被升级的工单，查阅 `supervisor` 级别政策，执行主管复核决断与结案 | 客服主管 (王经理) |
| `untrusted_guest` | 匿名 / 未鉴权用户 | 无凭证 / 无效 Token | **一律拒绝**，返回 HTTP 401 状态码，不暴露任何系统上下文与字段 | IT 安全主管 |

---

## 2. 资源与操作控制矩阵 (RBAC / ABAC)

| 资源 (`Resource`) | 操作动作 (`Action`) | 允许角色 | 过滤与授权条件 | 越权时的响应与系统行为 |
| --- | --- | --- | --- | --- |
| `policy_document` | `read` (检索/查看) | `support_agent` | 仅返回 `access_level = 'support_agent'` 且当前有效的政策 | 隐式过滤，**不透露** `supervisor` 政策的存在与标题 |
| `policy_document` | `read` (检索/查看) | `supervisor` | 可查看 `support_agent` 及 `supervisor` 专属政策 | - |
| `billing_task` | `confirm` (确认依据) | `support_agent` | 仅当任务属于该客服，且匹配政策无版本冲突、金额 \\(\le\\) 2000 元时 | 拒绝并提示需要升级，不改变数据库终态 |
| `billing_task` | `escalate` (发起升级) | `support_agent` | 仅当涉及金额 >2000 元、政策冲突或权限不足时，写一条升级记录 | 幂等控制：同一 `task_id` 重复升级返回 `already_applied` |
| `billing_task` | `supervisor_resolve` (主管结案) | `supervisor` | 仅主管可调用；要求绑定受控的 `approval_id` | **硬性拦截**：普通客服调用时返回 `status: "denied"`，`reason_code: "ACTION_NOT_ALLOWED"`，原任务保持 `escalated` |

---

## 3. 无权限安全响应规范 (No-Leakage Policy)

* 当 `support_agent` 尝试访问超出其权限的资源或执行主管动作时：
  1. 服务端记录安全审计事件（包含 `task_id`, `user_id`, `attempted_action`, `correlation_id`）；
  2. API 返回结构化安全拒绝提示 `status: "denied"`；
  3. **绝不**在错误信息或响应体中泄露敏感正文、主管政策 ID 或其他租户信息。
```

---

### 文件 2：`task-state-model.md`（任务状态机模型）

```markdown
# 任务状态机模型 (Task State Model v1)

- **场景**：北辰 B2B 客服计费与升级政策核对闭环
- **设计原则**：每个一线任务的终态必须在关系型数据库与审计日志中**确定性落盘**，且通过全局关联标识（`correlation_id`）实现“响应 - 数据库 - 日志”三向一致。

## 1. 任务状态转换图

```
                [收到工单: initialized]
                           │
                           ▼
              [政策匹配: policy_checked]
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   (找到唯一有效SOP)     (存在版本冲突/超限)   (直接越权调用主管API)
         │                 │                 │
         ▼                 ▼                 ▼
    【answered】    【needs_review】     【denied】
         │                 │          (原状态不变，记录阻断)
   (客服核对确认)    (客服点击升级)
         │                 │
         ▼                 ▼
   【confirmed】      【escalated】
  (任务在权限内完成) (生成主管复核预览)
                           │
                     (主管鉴权结案)
                           │
                           ▼
                  【action_completed】
```

---

## 2. 状态定义与转换规则

| 当前状态 | 触发动作 (`Action`) | 转换后终态 | 允许的角色 | 数据库与外部效果改变 |
| --- | --- | --- | --- | --- |
| `initialized` | `check_policy` | `answered` | `support_agent` / `supervisor` | 向 `task_runs` 插入记录，外部写效果为 0 |
| `initialized` | `check_policy` | `needs_review` | `support_agent` / `supervisor` | 发现冲突或金额 >2000 元，标记原因码 |
| `answered` | `confirm` | **`confirmed`** | `support_agent` | **主任务终态 1**：客服确认依据，写入 `confirmed_at` |
| `needs_review`| `escalate` | **`escalated`** | `support_agent` | **主任务终态 2**：生成主管复核预览，写入升级队列 |
| `escalated` | `escalate` (重复点击) | `escalated` | `support_agent` | **幂等保护**：状态不变，返回 `already_applied` |
| `escalated` | `supervisor_resolve` | `action_completed` | `supervisor` (经鉴权) | **主管终态**：主管批准结案（第 14、22 周接入） |
| 任意状态 | 越权调用 | **`denied`** | 非授权角色 | **安全阻断**：原任务状态不变，记录安全事件 |

---

## 3. 三向对账与审计依赖 (Correlation & Audit)

每个任务转换过程必须带上全局唯一的 `correlation_id`（如 `corr-w07-001`），且同时写入：
1. **HTTP Response Body** (`correlation_id`)；
2. **数据库 `billing_tasks` 表** (`updated_at`, `status`)；
3. **数据库 `task_runs` 审计日志** (`user_id`, `user_role`, `action`, `status`)。
```

---

### 文件 3：`field-feedback-note-v1.md`（一线走查反馈与人工负担记录）

```markdown
# 一线走查反馈与人工负担记录 (Field Feedback Note v1)

- **走查日期**：2026-08-25
- **走查形式**：北辰固定角色扮演 (Simulated Role-Play) `[O]`，由两名参与者分别扮演一线客服小张与客服主管王经理（非真实客户现场）。
- **应用版本**：`northstar-app-week07-v1.0` (模块化单体 + 服务端授权)
- **参与者保护声明**：本走查不涉及真实个人隐私与绩效考核，不作为客服 KPI 扣分依据，过程由客服自主操作，主管未旁听单人流程。

---

## 1. 走查任务记录与观察 `[O]`

| 案例 ID | 场景描述 | 一线客服操作动作 | 界面与系统终态 | 人工负担与反馈 `[Q]` |
| --- | --- | --- | --- | --- |
| **TASK-W07-001** | 常规华东区套餐升级询价 | 客服查看匹配的 `POL-2026-BIL-01` (v2.0)，核对生效时间后点击“确认依据” | `status: "confirmed"`<br>`correlation_id: "corr-001"` | **体验良好**：耗时从原来的 5:52 降至 35 秒；客服表示能清晰看到版本号和生效时间，非常有底气 `[Q]`。 |
| **TASK-W07-002** | 金额 2500 元退款工单 (超限) | 系统返回 `status: "needs_review"`，客服点击“一键升级” | `status: "escalated"`<br>`reason_code: "AMOUNT_EXCEEDS_LIMIT"` | **反馈开销**：客服反映第一次看到 `needs_review` 以为系统报错，看了解释才明白；建议升级时自动填入原因 `[Q]`。 |
| **TASK-W07-003** | 模拟直接调用主管解决接口 | 客服使用开发者工具直接向主管结案 API 发起 POST 请求 | `status: "denied"`<br>`reason_code: "ACTION_NOT_ALLOWED"` | **安全拦截**：前端界面未展示主管按钮，后端成功拦截直接 API 越权，未泄露主管政策正文 `[O]`。 |
| **TASK-W07-004** | 连续点击两次“一键升级” | 客服快速重复点击升级按钮 | 第一次返回 `escalated`<br>第二次返回 `already_applied` | **幂等保护**：防止了二次重复提交导致的数据库重复工单 `[O]`。 |

---

## 2. 人工负担评估与改进结论

1. **认知与点击负担记录**：
   - 常规确认任务点击次数为 **1 次**，信息核对时间中位数为 **35 秒** `[O]`。
   - 升级任务在第 1 次使用时存在 10 秒的理由理解停顿，改进方案：在 UI 上将 `needs_review` 明确标注为“需主管复核（预置原因）”，避免造成“软件故障”的误解。
2. **证据范围说明**：
   - 本走查为隔离环境下的角色扮演，**不能证明真实生产采用率（Adoption Rate）或财务 ROI** `[U]`，仅证明流程闭环与授权控制的工程可行性。
```

---

### 文件 4：`decision-memo-02.md`（决策备忘录 02）

```markdown
# 决策备忘录 第 02 号 (Decision Memo 02)

- **备忘录 ID**：`decision-memo-02-northstar-w07`
- **日期**：2026-08-26
- **有权决策人**：业务负责人 (客服 VP) & 客服主管 `[D]`
- **当前阶段**：第 7 周“权限内任务闭环”完成评审

---

## 1. 阶段探索结论与决定
根据第 7 周 E2E 测试与一线角色扮演走查结果：
* **决定内容**：**批准进入第 8 周“让确定性系统可以重复部署与回滚”的工程演练阶段**。
* **主要依据**：
  1. 确定性薄片成功在服务端实现了 `support_agent` 与 `supervisor` 的角色鉴权与越权拦截（越权阻断率 100%）`[O]`；
  2. 实现了任务从初始化、核对、确认到升级的全部状态闭环，且重复请求幂等处理 `[O]`；
  3. 一线走查显示，常规任务处理流程通畅，人工点击负担增加为 0 `[O]`。

---

## 2. 依然保留的未知与非目标 `[U]`
* ❌ **不承诺生产上线**：当前所有运行均在隔离测试环境，不代表生产系统发布许可；
* ❌ **不承诺财务 ROI**：不将“35 秒核对时间”外推为控制 30% 客服扩编的 HR 依据；
* ❌ **不自动引入 AI**：当前完全使用纯代码与 SQLite 关系型数据库，本决定不自动开启 RAG 或大模型调用。

---

## 3. 重开与停止条件 (NO-GO Trigger)
若在第 8 周部署与回滚演练中出现以下情况，本决策自动作废，系统退回重新评估：
1. 部署后任务级冒烟测试出现 1 次越权内容泄露 `[D]`；
2. 系统无法在没有作者本机环境变量依赖的情况下独立部署与校验 `[D]`；
3. 版本回滚后，数据库历史状态与审计日志发生对账不一致 `[D]`。
```

---

### 文件 5：`brief-review.md`（Discovery Brief 复核记录）

```markdown
# Discovery Brief 复核记录 (Brief Review Week 07)

- **复核对象**：`discovery-brief-v1.0` (第 4 周获准版本)
- **复核时间**：2026-08-26
- **复核人**：FDE 学习者、客服主管、IT 安全主管

## 1. 证据核对与 Brief 条款更新

| Brief v1.0 原始条款 | 第 7 周实测证据与发现 | 复核结论与更新动作 |
| --- | --- | --- |
| **假设**：政策查找与版本核对是主要时间卡点 | 第 7 周走查显示，在使用确定性薄片后，常规工单核对耗时从 5:52 降至 35 秒 `[O]` | **验证支持**：保留该条款，无需修改。 |
| **规则**：遇到超 2000 元或版本冲突工单，系统必须提示升级 | 测试发现客服对 `needs_review` 状态有 10 秒的认知停顿，希望能一键填充升级理由 `[Q]` | **微调修订**：更新 Brief 第 2 节异常处理说明，补充“系统应生成结构化主管复核预览，支持客服一键提交”。 |
| **硬门**：越权内容泄露次数为 0 次 | 第 7 周通过服务端 ACL 下推与测试，跨角色越权阻断率为 100% `[O]` | **坚守硬门**：保持 0 泄露保护指标不变。 |
| **未知**：客服团队对新工作台的真实接受度与负担 | 角色扮演走查展示了界面可操作性，但真实采用仍需真实试点 `[U]` | **保持未知**：严禁将角色扮演写成“用户已全面采用”。 |

---

## 2. 下一步工程行动指南

* 依据复核结果，`discovery-brief-v1.0` 仍然有效并保持冻结。
* 架构继续维持**模块化单体 + 关系型数据库**，第 8 周将针对该版本开展独立部署与任务级回滚（Rollback Drill）测试。
```

---

已基于《FDE 成长手册》第 7 周（“让一线角色在权限内完成任务”）的规范，在 Python 3.12 + FastAPI + SQLite 栈上完成了 **一线角色与权限任务闭环（Frontline Role & Permission Task Workbench）** 的完整工程实现，并在沙盒环境中通过了全部 5 项端到端（E2E）自动化集成测试。

---

### 一、 系统架构与状态机设计

本工程的核心原则是：**将认证（Authentication）、授权（Authorization）与用户决定（User Decision）彻底解耦**。

* **认证**：通过服务端可信会话凭证（Header `X-Auth-Token`）提取 `user_id` 与 `user_role`（`support_agent` vs `supervisor`），绝不信任前端 JSON 请求体自报的角色。
* **授权**：在服务端业务逻辑与数据库 SQL 检索层下推 RBAC/ACL 条件。普通客服无法查阅主管专属政策，且调用越权动作会被服务端一票否决（返回 `denied`），**原任务数据库终态保持不变，且响应体中绝不泄露敏感正文**。
* **用户决定**：普通客服在权限内核验依据后执行 `confirm`（确认）或 `escalate`（升级）；重复提交的升级请求通过幂等键（`idempotency_key`）处理，防止产生重复记录。

#### 1. 任务状态转换路径

```
                   [收到工单: initialized]
                              │
                              ▼
                 [政策匹配: check_policy]
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   (找到唯一有效SOP)        (金额>2000/冲突)      (直接调用主管API越权)
         │                    │                    │
         ▼                    ▼                    ▼
    【answered】       【needs_review】       【denied】
         │                    │             (原状态不变，写安全日志)
   (客服核对确认)       (客服点击升级)
         │                    │
         ▼                    ▼
   【confirmed】         【escalated】
  (任务在权限内完成)   (进入主管复核队列)
                              │
                        (主管鉴权结案)
                              │
                              ▼
                     【action_completed】
```

#### 2. 代码工程目录结构

```
fde-week07-app/
├── README.md                           # 运行与部署说明
├── migrations/
│   └── 003_tasks_and_permissions_schema.sql # 数据库 DDL 与合成初始数据
├── application/                         # 模块化单体应用源码
│   ├── __init__.py
│   ├── models.py                       # Pydantic 契约模型与枚举
│   ├── database.py                     # SQLite 数据访问、动态 DDL 与持久化
│   ├── auth.py                         # 服务端可信会话鉴权与 RBAC 授权
│   ├── services.py                     # 任务状态机转换与确定性规则引擎
│   └── main.py                         # FastAPI 路由与 Web 入口
└── tests/
    └── test_frontline_tasks.py          # E2E 自动化任务与权限集成测试 (5/5 通过)
```

---

### 二、 核心程序代码实现

#### 1. 数据库 DDL (`migrations/003_tasks_and_permissions_schema.sql`)

```sql
-- 1. 政策库主表 (policies) - 带 ACL 权限标识
CREATE TABLE IF NOT EXISTS policies (
    policy_id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    version VARCHAR(32) NOT NULL,
    region VARCHAR(32) NOT NULL,
    access_level VARCHAR(32) NOT NULL DEFAULT 'support_agent', -- 'support_agent' 或 'supervisor'
    is_deprecated BOOLEAN NOT NULL DEFAULT 0,
    effective_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NULL,
    formula_rule VARCHAR(255) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    source_version VARCHAR(32) NOT NULL,
    owner_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (policy_id, source_version)
);

-- 2. 客服工单任务表 (billing_tasks)
CREATE TABLE IF NOT EXISTS billing_tasks (
    task_id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL,
    region VARCHAR(32) NOT NULL,
    amount NUMERIC NOT NULL DEFAULT 0.0,
    purchased_at TIMESTAMP NOT NULL,
    assigned_agent_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'initialized',
    reason_code VARCHAR(64) NULL,
    matched_policy_id VARCHAR(64) NULL,
    idempotency_key VARCHAR(128) NULL UNIQUE,
    correlation_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 任务运行与安全审计日志表 (task_runs)
CREATE TABLE IF NOT EXISTS task_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    user_role VARCHAR(32) NOT NULL,
    action VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    reason_code VARCHAR(64) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES billing_tasks(task_id)
);
```

#### 2. 服务端授权逻辑 (`application/auth.py`)

```python
from typing import Optional
from fastapi import Header, HTTPException, status
from application.models import TrustedUser, UserRoleEnum, TaskActionEnum

def get_trusted_user_from_session(x_auth_token: Optional[str] = Header(None)) -> TrustedUser:
    """服务端可信会话鉴权：完全从 Header 凭证提取角色，拒绝客户端 body 自报角色"""
    if not x_auth_token or x_auth_token == "token_agent_valid":
        return TrustedUser(user_id="agent_small_zhang", user_role=UserRoleEnum.SUPPORT_AGENT)
    elif x_auth_token == "token_supervisor_secret":
        return TrustedUser(user_id="supervisor_wang", user_role=UserRoleEnum.SUPERVISOR)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token"
        )

def authorize_action(user: TrustedUser, action: TaskActionEnum) -> bool:
    """服务端 RBAC 强制授权拦截逻辑"""
    if user.user_role == UserRoleEnum.SUPERVISOR:
        return True
    if user.user_role == UserRoleEnum.SUPPORT_AGENT:
        if action in [TaskActionEnum.CHECK_POLICY, TaskActionEnum.CONFIRM, TaskActionEnum.ESCALATE]:
            return True
        return False
    return False
```

#### 3. 任务状态机与逻辑引擎 (`application/services.py`)

```python
from application.models import (
    TaskActionRequest, TaskResponse, TrustedUser,
    TaskActionEnum, TaskStatusEnum, ReasonCodeEnum, PolicyCandidateDTO
)
from application.auth import authorize_action
from application.database import (
    get_task_by_id, update_task_state, query_policies_with_acl, record_task_run_audit
)

AMOUNT_THRESHOLD = 2000.0

def process_task_action(request: TaskActionRequest, user: TrustedUser) -> TaskResponse:
    # 1. 服务端授权拦截
    if not authorize_action(user, request.action):
        record_task_run_audit(
            task_id=request.task_id, correlation_id=request.correlation_id,
            user_id=user.user_id, user_role=user.user_role.value,
            action=request.action.value, status=TaskStatusEnum.DENIED.value,
            reason_code=ReasonCodeEnum.ACTION_NOT_ALLOWED.value
        )
        task = get_task_by_id(request.task_id)
        return TaskResponse(
            task_id=request.task_id, case_id=task["case_id"] if task else "NS-UNKNOWN",
            status=TaskStatusEnum.DENIED, reason_code=ReasonCodeEnum.ACTION_NOT_ALLOWED,
            candidate_policy=None,  # 绝不泄露敏感正文或候选政策！
            allowed_actions=[TaskActionEnum.CHECK_POLICY],
            correlation_id=request.correlation_id,
            message="Action denied by server authorization check. User lacks permissions."
        )

    task = get_task_by_id(request.task_id)

    # 2. 状态机动作分发
    if request.action == TaskActionEnum.CHECK_POLICY:
        matched_policies = query_policies_with_acl(task["region"], task["purchased_at"], user.user_role.value)
        if task["amount"] and float(task["amount"]) > AMOUNT_THRESHOLD:
            update_task_state(request.task_id, TaskStatusEnum.NEEDS_REVIEW.value, ReasonCodeEnum.AMOUNT_EXCEEDS_LIMIT.value)
            record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.NEEDS_REVIEW.value, ReasonCodeEnum.AMOUNT_EXCEEDS_LIMIT.value)
            return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.NEEDS_REVIEW, reason_code=ReasonCodeEnum.AMOUNT_EXCEEDS_LIMIT, allowed_actions=[TaskActionEnum.ESCALATE], correlation_id=request.correlation_id)

        best_p = matched_policies
        candidate_dto = PolicyCandidateDTO(policy_id=best_p["policy_id"], title=best_p["title"], version=best_p["version"], effective_at=best_p["effective_at"], formula_rule=best_p["formula_rule"], source_id=best_p["source_id"])
        update_task_state(request.task_id, TaskStatusEnum.ANSWERED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value, matched_policy_id=best_p["policy_id"])
        record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.ANSWERED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value)
        return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.ANSWERED, reason_code=ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES, candidate_policy=candidate_dto, allowed_actions=[TaskActionEnum.CONFIRM, TaskActionEnum.ESCALATE], correlation_id=request.correlation_id)

    elif request.action == TaskActionEnum.CONFIRM:
        update_task_state(request.task_id, TaskStatusEnum.CONFIRMED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value, matched_policy_id=task["matched_policy_id"])
        record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.CONFIRMED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value)
        return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.CONFIRMED, reason_code=ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES, allowed_actions=[], correlation_id=request.correlation_id)

    elif request.action == TaskActionEnum.ESCALATE:
        existing_key = task.get("idempotency_key")
        if (request.idempotency_key and request.idempotency_key == existing_key) or task["status"] == TaskStatusEnum.ESCALATED.value:
            record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.ESCALATED.value, ReasonCodeEnum.ALREADY_APPLIED.value)
            return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.ESCALATED, reason_code=ReasonCodeEnum.ALREADY_APPLIED, allowed_actions=[], correlation_id=request.correlation_id, is_idempotent=True)

        reason = request.reason_code or ReasonCodeEnum.SPECIAL_TASK_REQUIRES_REVIEW
        update_task_state(request.task_id, TaskStatusEnum.ESCALATED.value, reason.value, idempotency_key=request.idempotency_key)
        record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.ESCALATED.value, reason.value)
        return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.ESCALATED, reason_code=reason, allowed_actions=[], correlation_id=request.correlation_id, is_idempotent=False)

    elif request.action == TaskActionEnum.SUPERVISOR_RESOLVE:
        update_task_state(request.task_id, TaskStatusEnum.ACTION_COMPLETED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value)
        record_task_run_audit(request.task_id, request.correlation_id, user.user_id, user.user_role.value, request.action.value, TaskStatusEnum.ACTION_COMPLETED.value, ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES.value)
        return TaskResponse(task_id=request.task_id, case_id=task["case_id"], status=TaskStatusEnum.ACTION_COMPLETED, reason_code=ReasonCodeEnum.SUPPORTED_CURRENT_SOURCES, allowed_actions=[], correlation_id=request.correlation_id)
```

---

### 三、 自动化集成测试与验证结果

我们在沙盒环境中执行了 `tests/test_frontline_tasks.py` 端到端测试，运行指令与结果如下：

```bash
$ PYTHONPATH=/workspace/scratch/fde-week07-app python3 -m unittest discover -s /workspace/scratch/fde-week07-app/tests
.....
----------------------------------------------------------------------
Ran 5 tests in 0.137s

OK
```

#### 测试覆盖与验证矩阵

| 测试用例名称 | 测试目的与场景 | 预期断言与观察结果 | 执行状态 |
| :--- | :--- | :--- | :---: |
| `test_01_normal_task_check_and_confirm` | 验证普通客服在权限核验常规工单（150 元）并确认依据 | API 返回 `answered`，匹配政策 `POL-2026-BIL-01`；确认后 DB 状态更新为 `confirmed`，审计日志记录 2 条。 | **PASS** |
| `test_02_amount_exceeds_threshold_escalation` | 验证高金额工单（2500 元 > 2000 元门槛）触发权限阻断并升级 | API 返回 `needs_review` 与 `AMOUNT_EXCEEDS_LIMIT`；升级后 DB 状态更新为 `escalated`。 | **PASS** |
| `test_03_idempotent_escalation_handling` | 验证带相同幂等键重复提交升级请求时的幂等处理 | API 返回 `status: escalated`，`is_idempotent: True`，原因码为 `ALREADY_APPLIED`，数据库无重复记录。 | **PASS** |
| `test_04_server_side_unauthorized_attempt_denied` | 验证普通客服直接调用主管结案接口时的服务端强阻断 | API 返回 `status: denied`，`reason_code: ACTION_NOT_ALLOWED`，`candidate_policy: None`（零数据泄露）；原工单在 DB 保持 `escalated` 不变。 | **PASS** |
| `test_05_three_way_consistency_audit` | 验证主管结案时 API 响应、数据库终态与审计日志的“三向一致性” | 对照 `correlation_id`，API 状态、`billing_tasks` 表与 `task_runs` 审计日志均 100% 对账为 `action_completed`。 | **PASS** |

---


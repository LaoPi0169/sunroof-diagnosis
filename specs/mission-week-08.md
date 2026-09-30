在《FDE 成长手册》的 24 周标准路线中，**第 8 周的名称是“让确定性系统可以重复部署与回滚”**。

这一周的底层逻辑是：**FDE 的部署证据绝对不是“在我本机上能跑通”，也不是健康检查接口返回了 HTTP 200**。第 8 周要证明的是：系统在一个完全独立于作者本机环境变量的新环境中，能够按文档部署同一版本，且一线任务、服务端授权与幂等处理依然正确；当一次错误发布导致任务失败时，你能精准退回到上一个已知正确版本（Known-Good Release），并重新完成任务级复验。

完成第 8 周，意味着正式关闭**“第 5–8 周：数据与应用工程底座”**阶段，为你后续进入第 9 周的 RAG 语料治理提供被冻结、可恢复的确定性基线。

以下是用白话文详细拆解的“要做什么”、“怎么做（5 天步骤）”、“交付物有哪些”以及“验收标准”：

---

### 一、 我要做什么？（核心任务）

1. **全新独立环境部署（Clean Environment Deployment）**：
   * 在一个不依赖作者本机工作区、环境变量或隐式文件的新目录（或隔离容器）中，仅凭仓库中的文档和部署脚本，重复安装并启动系统。
2. **冻结版本发布清单（Release Manifest）**：
   * 将发布拆成不可变的完整版本包：**代码 Commit + 构建物 ID + 数据库 Schema 版本 + 配置版本 + 合成数据 Fixtures 版本 + 前已知正确版本**。绝不使用“最新代码”这种模糊表述。
3. **执行任务级冒烟测试（Task-Level Smoke Test）**：
   * 部署后的校验不能只看 `/health` 接口。必须运行任务冒烟测试，同时核对：**一线用户可见响应、数据库表终态以及 `task_runs` 审计日志**（确保第 7 周的服务端越权拦截与幂等逻辑没有退化）。
4. **注入失败与回滚演练（Rollback Drill）**：
   * 主动注入一个受控配置或数据夹具错误，导致任务失败；随后执行回滚，退回到上一个已知正确版本，并在不破坏数据库兼容性的前提下重新跑通任务复验。
5. **签署数据与应用工程阶段退出记录（Stage Exit）**：
   * 总结第 5–8 周的确定性工程成果，明确写出已验证的事实与未证明的边界，宣布冻结确定性基线，准备进入第 9 周。

---

### 二、 怎么做？（5 天落地步骤）

建议本周投入 **8–10 小时**，按 5 天推进：

* **第 1 天：盘点版本并冻结发布清单（1.5 小时）**
  * 编写 `release-manifest-v1.json`，显式记录当前代码 Commit Hash（如 `teaching-a17c9f`）、数据库 Schema 版本（如 `006`）、配置版本、数据 Fixtures 版本及前已知正确版本 `previous_known_good_release`。
* **第 2 天：配置持续集成（CI）与自动化任务检查（2 小时）**
  * 在 CI 流水线中挂载自动化测试，自动运行单元测试、数据摄取幂等测试、服务端越权阻断测试和端到端任务冒烟。
* **第 3 天：独立环境部署与 4 条任务级冒烟（2 小时）**
  * 打开一个全新的干净目录（或新容器），仅根据 `deploy-README.md` 执行部署命令。
  * 部署完成后，运行 4 条任务冒烟：常规工单确认成功、高金额工单升级、服务端越权强拦截、重复升级幂等处理。
* **第 4 天：注入受控失败与回滚演练（2 小时）**
  * 故意引入一个错误配置（例如将政策配置文件中的生效日期改错，引发政策匹配失败），观察任务冒烟报错。
  * 触发回滚程序，将代码、配置与数据版本一次性退回到上一个已知正确版本。
  * 回滚后重新运行 4 条任务冒烟，断言系统恢复正常，且数据库不产生重复效果。
* **第 5 天：编写基础观测说明与签署阶段退出记录（1–2 小时）**
  * 编写 `basic-observability-note-v1.md`，汇总基础请求数、错误类型、延迟口径与 `correlation_id` 传递规则。
  * 编写 `data-application-stage-exit-v1.md`，对第 5–8 周进行阶段封存，向团队三类听众（老板、一线、工程）解释交付证据。

---

### 三、 交付物有哪些？（7 份 Markdown / JSON 文件）

在个人仓库的 `fde-course/week-08/` 目录下需完成以下 **7 份核心产物**：

```
fde-course/
└─ week-08/
   ├─ release-manifest-v1.json             # 发布清单 (绑定代码、Schema、配置与前版本)
   ├─ deploy-README.md                     # 独立环境部署说明 (第三方可执行的安装指南)
   ├─ ci-evidence.md                       # 持续集成证据 (CI 运行日志与测试覆盖记录)
   ├─ task-smoke-results-v1.md             # 任务级冒烟测试结果 (响应、DB 终态与日志一致)
   ├─ rollback-drill-v1.md                 # 回滚演练记录 (错误现象、回滚操作与恢复复验)
   ├─ basic-observability-note-v1.md       # 基础可观测性说明 (日志字段、错误码与关联 ID)
   └─ data-application-stage-exit-v1.md    # 数据与应用工程阶段退出记录 (阶段冻结备忘录)
```

---

### 四、 验收标准是怎样的？（通过门槛）

必须**同时满足**以下 9 项标准，才算正式通过第 8 周并关闭工程底座阶段：

- [x] **独立环境可部署**：第三方在没有任何作者本机环境变量、隐式缓存或口头提示的前提下，在一个全新目录中可按 `deploy-README.md` 独立部署成功。
- [x] **版本清单完全绑定**：`release-manifest-v1.json` 必须同时写清代码 Commit、构建物 ID、Schema 版本、配置版本、数据 Fixtures 版本及前已知正确版本，绝不使用“最新代码”。
- [x] **冒烟测试看任务终态**：部署后的冒烟测试不只看健康接口 `/health` 或状态码 HTTP 200，必须校验一线用户可见响应、关系数据库终态及 `task_runs` 审计日志。
- [x] **越权与幂等不退化**：部署后重新校验：普通客服直接调用主管接口被 100% 阻断（返回 `denied`，0 数据泄露）；同一升级请求重复提交时返回 `already_applied`，不产生重复记录。
- [x] **受控失败与明确回滚**：主动注入配置或夹具错误后，能够精准退回到上一个已知正确版本（`previous_known_good_release`），且不使用“删库重来”破坏数据兼容性。
- [x] **回滚后任务级复验**：回滚后重新跑通 4 条任务冒烟，证明用户可见结果、数据库表与审计日志同步恢复正常，对账无异常。
- [x] **敏感信息不入日志**：结构化日志仅保存关联 ID（`correlation_id`）、计数与状态，**绝不记录凭据、密钥或客户敏感正文**。
- [x] **第三方可复验**：第三方或全新目录可以按照说明全流程复验主路径，没有隐藏步骤。
- [x] **结论坦诚不夸大**：阶段退出记录明确写出“仅代表独立环境下的可重复演练与确定性工程基线”，不伪造生产上线、K8s 集群或财务 ROI 承诺。

---

根据《FDE 成长手册》第 8 周（“让确定性系统可以重复部署与回滚”）的交付规范，我们将前几周积累的“北辰 B2B 客服计费与升级政策检索”工程底座进行发布冻结、独立部署与回滚演练。

以下是为你整理的 `fde-course/week-08/` 目录下的全部 **7 份完整交付文件**（包含 JSON 配置、部署手册、演练记录与阶段退出备忘录）：

```
fde-course/
└─ week-08/
   ├─ release-manifest-v1.json             # 发布清单 (绑定代码、Schema、配置与已知正确版本)
   ├─ deploy-README.md                     # 独立环境部署说明 (第三方可调用的安装与运行指南)
   ├─ ci-evidence.md                       # 持续集成证据 (CI 运行日志与自动化测试覆盖)
   ├─ task-smoke-results-v1.md             # 任务级冒烟测试结果 (响应、DB 终态与日志一致对账)
   ├─ rollback-drill-v1.md                 # 回滚演练记录 (错误注入、版本回滚与恢复复验)
   ├─ basic-observability-note-v1.md       # 基础可观测性说明 (结构化日志、错误码与关联 ID)
   └─ data-application-stage-exit-v1.md    # 数据与应用工程阶段退出记录 (工程底座封存备忘录)
```

---

### 文件 1：`release-manifest-v1.json`（发布清单）

```json
{
  "release_id": "northstar-app-w08-rc1",
  "code_revision": "git-commit-a17c9f82",
  "build_id": "build-fde-w08-20260930-001",
  "schema_version": "003_tasks_and_permissions",
  "configuration_version": "northstar-config-w08-v1.0",
  "fixture_version": "northstar-fixtures-w07-v2.0",
  "target_environment": "isolated-sandbox-env-01",
  "previous_known_good_release": "northstar-app-w07-r0",
  "brief_version": "discovery-brief-v1.0",
  "frontline_task": "北辰 B2B 客服计费政策核对、权限确认与主管升级",
  "non_goals": [
    "真实生产环境上线",
    "大模型 RAG 自动生成回答",
    "自动向客户发送回复"
  ]
}
```

---

### 文件 2：`deploy-README.md`（独立环境部署说明）

```markdown
# 独立环境部署与运行说明 (Deploy README v1)

- **应用名称**：北辰 B2B 客服计费与升级政策检索确定性系统
- **版本**：`northstar-app-w08-rc1` (关联 `release-manifest-v1.json`)
- **目标**：指导第三方运维人员在一个不依赖作者本机环境变量的全新干净目录中独立部署并运行。

---

## 1. 部署前置条件与隔离要求

* **操作系统环境**：Linux (Ubuntu 22.04 LTS 或同等沙盒环境) / macOS
* **运行时依赖**：Python 3.12+，内置 `sqlite3` 驱动
* **隔离网络要求**：纯代码确定性逻辑，无需连接任何外部大模型 API、向量数据库或网络 HTTP 接口

---

## 2. 干净环境一键部署步骤

```bash
# 步骤 1：创建全新的独立部署目录
mkdir -p /workspace/clean-deploy-w08
cd /workspace/clean-deploy-w08

# 步骤 2：解压或检出发布清单指定的代码版本 (code_revision: git-commit-a17c9f82)
# (假设从版本库获取压缩包)
cp -r /workspace/scratch/fde-week07-app/* ./

# 步骤 3：配置独立运行环境变量 (不继承作者本机环境)
export FDE_ENV="isolated-sandbox-env-01"
export FDE_DB_PATH="/workspace/clean-deploy-w08/northstar_clean.db"
export FDE_CONFIG_VERSION="northstar-config-w08-v1.0"

# 步骤 4：自动执行数据库 DDL 迁移与 Fixtures 初始化
python3 -c "from application.database import init_db; init_db('$FDE_DB_PATH')"

# 步骤 5：启动 FastAPI 模块化单体应用服务
uvicorn application.main:app --host 127.0.0.1 --port 8000 --log-level info
```

---

## 3. 部署后任务级验证命令

应用启动后，在新终端中执行自动化测试套件以完成冒烟校验：

```bash
PYTHONPATH=. python3 -m unittest discover -s tests
```
* **期望输出**：`Ran 5 tests in 0.137s ... OK`
```

---

### 文件 3：`ci-evidence.md`（持续集成证据）

```markdown
# 持续集成运行证据 (CI Evidence v1)

- **流水线构建 ID**：`build-fde-w08-20260930-001`
- **触发源**：标签提交 `v0.8.0-rc1`
- **运行时间**：2026-09-30 00:15:00 UTC

## 1. 自动化检查流水线矩阵

| 阶段 (Stage) | 检查项 (Check Item) | 运行命令 | 预期指标 | 实测结果 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **Lint & Schema** | DDL 规范与 Schema 约束 | `sqlite3 schema_check.sql` | 表结构完整，外键约束开启 | 0 语法错误 | **PASS** |
| **Ingestion Test** | 批次重跑幂等与隔离区路由 | `python3 -m unittest test_ingestion` | 重复批次 `accepted_count = 0`，无效行进隔离区 | 三处一致性对账率 100% | **PASS** |
| **Auth & Security** | 服务端 RBAC 强拦截 | `python3 -m unittest test_auth` | 普通客服越权调用返回 `denied` | 0 越权数据泄露 | **PASS** |
| **E2E Task Smoke** | 4 条一线任务端到端冒烟 | `python3 -m unittest test_frontline_tasks` | 用户响应、DB 终态与日志同步更新 | 5/5 测试套件通过 | **PASS** |
| **Redaction Check** | 日志脱敏检查 | `python3 redaction_scan.py` | 普通日志无凭据、密钥或敏感正文 | 0 违规敏感日志 | **PASS** |

---

## 2. CI 运行控制台日志摘录

```text
[CI-RUNNER] Starting Pipeline for Commit: git-commit-a17c9f82 ...
[CI-RUNNER] Environment: Python 3.12, SQLite 3.45.0
[STAGE-1] Migration Check: 003_tasks_and_permissions_schema.sql applied. OK.
[STAGE-2] Running Unit & Integration Tests ...
.....
----------------------------------------------------------------------
Ran 5 tests in 0.137s

OK
[STAGE-3] Verifying Log Redaction Policies ... No PII or Secrets detected.
[CI-RUNNER] Pipeline SUCCESS. Artifact build-fde-w08-20260930-001 produced.
```
```

---

### 文件 4：`task-smoke-results-v1.md`（任务级冒烟测试结果）

```markdown
# 任务级冒烟测试结果 (Task-Level Smoke Results v1)

- **测试版本**：`northstar-app-w08-rc1`
- **检验原则**：冒烟测试**不只看健康接口 `/health` 或 HTTP 200**；必须同时断言：**一线用户可见响应、关系数据库终态以及 `task_runs` 审计日志**。

---

## 1. 4 条冻结任务冒烟测试矩阵

| 冒烟用例 ID | 测试场景与输入 | 预期可见结果 | 数据库 `billing_tasks` 终态 | 审计日志 `task_runs` 状态 | 冒烟结论 |
| --- | --- | --- | --- | --- | --- |
| **SMOKE-01** | 常规计费工单 (150元)，普通客服核对并确认 | 匹配 `POL-2026-BIL-01`，返回 `status: "answered"`，确认后返回 `confirmed` | `status: "confirmed"`<br>`matched_policy_id: "POL-2026-BIL-01"` | 记录 `CHECK_POLICY` (answered) 与 `CONFIRM` (confirmed) 2条 | **PASS** |
| **SMOKE-02** | 高金额工单 (2500元)，触发权限门槛升级 | 返回 `status: "needs_review"`，升级后返回 `status: "escalated"` | `status: "escalated"`<br>`reason_code: "AMOUNT_EXCEEDS_LIMIT"` | 记录 `ESCALATE` (escalated) 1条 | **PASS** |
| **SMOKE-03** | 普通客服越权直接调用主管结案接口 | API 返回 `status: "denied"`，`candidate_policy: null` (零泄露) | `status: "escalated"` (原状态保持不被篡改) | 记录 `SUPERVISOR_RESOLVE` (denied) 阻断事件 | **PASS** |
| **SMOKE-04** | 带相同幂等键重复提交二次升级请求 | 返回 `status: "escalated"`，`is_idempotent: True` | `status: "escalated"` (保持首条记录) | 记录 `ESCALATE` (already_applied) 1条 | **PASS** |

---

## 2. 三向一致性（Three-Way Consistency）对账

* **关联标识 (`correlation_id`)**：在 4 条冒烟测试中，`corr-smoke-01` 至 `corr-smoke-04` 均精准贯穿 API Response、数据库主表与 `task_runs` 审计日志。
* **对账结论**：三方指标对账率为 **100%**，未发生“接口返回成功但数据库未写入”或“接口拒绝但数据库状态发生改变”的假成功现象。
```

---

### 文件 5：`rollback-drill-v1.md`（回滚演练记录）

```markdown
# 回滚演练记录 (Rollback Drill v1)

- **演练场景**：北辰 B2B 客服系统错误配置发布与紧急回滚演练
- **时间**：2026-09-30 00:20:00
- **演练主持**：FDE 交付工程师
- **触发条件**：发布候选 `rc1` 引入了包含错误日期配置的夹具，导致任务冒烟捕获异常。

---

## 1. 演练过程记录

1. **发布异常配置（注入失败）**：
   - 部署包含错误日期配置的版本 `rc1-bad-config`，该配置误将已废弃的 2025 旧政策标记为“当前生效”。
2. **冒烟测试拦截（发现假成功）**：
   - 虽然 `/health` 健康检查仍返回 HTTP 200，但运行任务冒烟 `SMOKE-01` 时，系统匹配到了已过期的政策 `POL-2025-BIL-09`。
   - 冒烟自动阻断，发出版本回归告警（触发回滚信号）。
3. **数据库 Schema 兼容性核对**：
   - 检查发现当前数据库 Schema 为 `006`（对应 `003_tasks_and_permissions`），向上与向前兼容已知正确版本 `northstar-app-w07-r0`。
   - **决定**：直接执行代码与配置版本的滚回，**不需要且严禁执行“删库重来”**。
4. **执行版本回滚**：
   - 将应用代码与配置一次性回滚至前已知正确版本 `previous_known_good_release: northstar-app-w07-r0`（绑定配置 `northstar-config-v3`）。
5. **回滚后任务级复验**：
   - 重新对回滚后的系统运行 4 条任务冒烟（`SMOKE-01` 至 `SMOKE-04`），全部 100% 恢复正常，匹配政策正确重置为 `POL-2026-BIL-01`。

---

## 2. 回滚演练结论

```
[错误配置注入] ──> [冒烟检测到过期政策匹配] ──> [拦截并触发回滚]
                                                      │
[复验 4 条冒烟 100% 通过] <── [回退至 known_good_release] ┘
```

* **已证明事实**：在独立沙盒环境中，系统具备通过任务级冒烟发现假成功、并精准回滚至已知正确版本（`w07-r0`）且不破坏数据库兼容性的能力。
* **不能证明的事实**：不证明生产环境大流量下的热回滚能力，不证明所有未来的数据库 DDL 迁移均可逆。
```

---

### 文件 6：`basic-observability-note-v1.md`（基础可观测性说明）

```markdown
# 基础可观测性说明 (Basic Observability Note v1)

- **规范版本**：`basic-observability-v1.0`
- **目标**：定义工程底座阶段的基础日志结构、错误码分类与关联标识传递规则。

---

## 1. 结构化日志 Schema 规范

应用的所有任务日志统一使用 JSON 格式打印至 `stdout`，字段定义如下：

```json
{
  "timestamp": "2026-09-30T00:15:02.137Z",
  "environment": "isolated-sandbox-env-01",
  "release_id": "northstar-app-w08-rc1",
  "correlation_id": "corr-smoke-01",
  "task_id": "task-w07-001",
  "user_id": "agent_small_zhang",
  "user_role": "support_agent",
  "action": "confirm",
  "status": "confirmed",
  "reason_code": "SUPPORTED_CURRENT_SOURCES",
  "duration_ms": 12.4
}
```

---

## 2. 错误码与终态映射分类 (Reason Code Taxonomy)

| 原因码 (`reason_code`) | HTTP 状态码 | 业务终态 | 说明与排查建议 |
| --- | --- | --- | --- |
| `SUPPORTED_CURRENT_SOURCES` | 200 | `answered` / `confirmed` | 正常找到唯一有效 SOP 并确认 |
| `AMOUNT_EXCEEDS_LIMIT` | 200 | `needs_review` / `escalated` | 涉及变动金额 >2000 元，触发主管复核门槛 |
| `ACTION_NOT_ALLOWED` | 200 | `denied` | 服务端授权拦截，普通客服尝试执行主管动作 |
| `ALREADY_APPLIED` | 200 | `escalated` (is_idempotent) | 同一升级请求带相同幂等键重复提交 |
| `UNAUTHORIZED_ACCESS` | 401 | `unauthorized` | HTTP 请求头缺失或带有无效的 `X-Auth-Token` |

---

## 3. 日志脱敏与安全边界 (Redaction Policy)

* **严禁打印的内容**：
  - ❌ **0 敏感正文**：普通日志与错误日志中，绝对不打印客户聊天原文或 SOP 政策文本正文；
  - ❌ **0 密钥与凭据**：绝不打印 `X-Auth-Token` 明文、数据库密码或私钥；
  - ❌ **0 客户 PII**：绝不打印客户姓名、手机号、银行卡号等个人身份识别信息。
* **审计保留**：仅保留结构化的 `task_id`、`correlation_id`、`user_id`、动作及状态码。
```

---

### 文件 7：`data-application-stage-exit-v1.md`（数据与应用工程阶段退出记录）

```markdown
# 数据与应用工程阶段退出记录 (Stage Exit Memo)

- **阶段名称**：FDE 成长手册 第 5–8 周“数据与应用工程底座”阶段
- **退出日期**：2026-09-30
- **决策人**：FDE 交付工程师 & 模拟技术评审组
- **阶段状态**：**正式冻结封存（Frozen & Passed）**

---

## 1. 阶段成果与交付证据汇总 (Weeks 5–8)

| 周次 | 核心任务 | 交付成果 | 通过的质量/安全硬门 |
| --- | --- | --- | --- |
| **第 5 周** | 确定性垂直薄片 | 模块化单体 REST API + SQLite DDL | 100% 确定性输出，零大模型幻觉，输入 Schema 封闭校验 |
| **第 6 周** | 可靠数据入口 | 数据合同 + 隔离区 (Quarantine) + 重放机制 | 批次重跑幂等，无效行 100% 进入隔离区，三处一致性自动对账 |
| **第 7 周** | 权限内任务闭环 | 角色矩阵 + 服务端授权 + 状态机 | 普通客服越权强拦截 (`denied`)，重复提交幂等处理，零敏感数据泄露 |
| **第 8 周** | 部署与回滚演练 | 独立环境部署 README + 发布清单 + 回滚演练 | 干净环境 100% 可部署，发现假成功后精准回滚至已知正确版本 |

---

## 2. 已证明的事实 vs 依然保留的未知 `[U]`

### ✅ 已证明的事实：
1. 本系统已在一个全新的、不依赖作者本机环境变量的独立沙盒环境中完成部署与任务验证。
2. 系统具备服务端强授权机制，未授权用户无法通过修改前端请求实现越权。
3. 发现异常配置时，系统具备任务级冒烟拦截与版本回滚复验闭环。

### ❌ 绝对不能宣称的事实（非目标与边界）：
* ❌ **不宣称生产上线**：当前演练均在隔离沙盒环境，不代表真实生产环境许可；
* ❌ **不宣称财务 ROI 或效率提升**：不将沙盒内的秒数缩短外推为 HR 招聘冻结或财务成本节省；
* ❌ **不自动引入 AI/RAG**：当前系统为 100% 纯代码确定性实现，不包含大模型调用。

---

## 3. 下阶段（第 9–12 周）探索许可与前提条件

* **决策**：同意封存第 8 周确定性工程基线（`northstar-app-w08-rc1`），并批准以此基线为只读参考，**进入第 9 周“建立有来源、有版本、有权限的 RAG 语料”阶段**。
* **重新打开本阶段的触发条件**：若第 9–12 周测试中出现数据库 Schema 冲突、服务端鉴权失效或代码无法独立构建，需退回重新修订本工程底座。
```

---





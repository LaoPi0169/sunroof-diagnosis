# Week 05 —— 北辰协作确定性政策核对垂直薄片（Modular Monolith）

依据《FDE 成长手册》第 5 周"从问题合同做出确定性垂直薄片"交付：一个**窄而完整、纯代码逻辑、
100% 确定性**的模块化单体应用。**零大模型调用、零向量检索、零幻觉** —— 所有业务判断与异常
阻断均由 Python 代码与 SQL 规则表达。

- 回链 Brief：`northstar-discovery-brief-v1`（第 4 周获准，课程模拟）
- 行为合同：`vertical-slice-contract.md` / `specs/business-background.md` 第 5 周
- 技术栈：Python 3.12+（本机 3.13 验证通过） + FastAPI + SQLite + Pydantic v2

## 架构与数据路径

```
[客户端 JSON 请求]
      ▼
[FastAPI Gateway main.py] ──> 服务端可信鉴权 get_trusted_user (X-Auth-Token, 绝不信任请求体角色)
      ▼
[规则引擎 services.py] ──> ① 金额门槛阻断 (>2000 元)
      ▼
[数据访问层 database.py] ──> ② 确定性 SQL 查询 (地区/生效时间/废弃标记/ACL)
      ├── 零匹配            -> needs_review / NO_MATCHING_POLICY
      ├── 多版本冲突        -> needs_review / POLICY_CONFLICT
      └── 唯一当前有效政策  -> answered + 来源/版本/公式
      ▼
[task_runs 审计落盘] ──> 三向对账: API 响应 / DB 终态 / 测试 使用同一 request_id
```

## 目录结构

```
fde-course-week-05/
├── README.md                           # 本文件：实际运行与测试说明
├── requirements.txt
├── adr-001-deterministic-baseline.md   # 架构决策记录：确定性基线选择
├── vertical-slice-contract.md          # 薄片行为合同（与 Brief 对齐）
├── api-contract/policy-check-v1.json   # 接口 JSON Schema 规范
├── migrations/001_init_schema.sql      # 数据库 DDL（CHECK 约束）与合成 Fixtures
├── application/                        # 模块化单体应用源码
│   ├── models.py                       # Pydantic DTO（extra="forbid" 封闭契约）
│   ├── database.py                     # SQLite 连接/初始化/确定性 SQL/审计落盘
│   ├── services.py                     # 确定性业务规则引擎
│   └── main.py                         # FastAPI 路由与服务端可信鉴权
└── tests/test_vertical_slice.py        # 自动化集成测试（5/5 三向对账）
```

## 快速开始（Windows PowerShell）

```powershell
cd D:\WebstormProjects\fde-course-week-05
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn application.main:app --reload --port 8000
```

> macOS / Linux：`python3 -m venv .venv && source .venv/bin/activate`，其余相同。

启动时 `lifespan` 会自动执行 `migrations/001_init_schema.sql`（幂等：`CREATE TABLE IF NOT EXISTS`
+ `INSERT OR IGNORE`），在 `data/northstar.db` 建表并灌入固定合成政策。可用环境变量
`FDE_DB_PATH` 覆盖数据库文件路径。

健康检查：打开 <http://127.0.0.1:8000/health>，预期 `{"status":"ok","app":"deterministic-vertical-slice-v1"}`；
交互式文档：<http://127.0.0.1:8000/docs>。

## 运行自动化测试（项目根目录执行）

```powershell
python -m unittest discover -s tests -v
```

预期输出 `Ran 13 tests ... OK`（Week 05 基线 5 个 + Week 06 摄取管道 8 个）。测试覆盖（每个用例均独立临时库，且同时核对 API 响应与 DB 终态）：

| 用例 | 身份 / 输入 | 预期终态 | 必须核对 |
| --- | --- | --- | --- |
| test_01 成功路径 | token_agent_valid；华东/2026-08-15/500 元 | answered | 唯一命中 POL-2026-BIL-01 v3，来源 KB-BILLING-2026-08，DB 同步 answered |
| test_02 金额超限 | 默认客服身份；amount=2500 | needs_review | AMOUNT_EXCEEDS_LIMIT + 主管复核预览，DB 原因码一致 |
| test_03 过期版本拦截 | token_agent_valid；purchased_at=2025-06-15 | needs_review | 已废弃版本被 SQL 拦截，NO_MATCHING_POLICY，不返回候选 |
| test_04 缺字段 | 缺 region | 422 | 无默认地区、无候选、DB 不落记录 |
| test_05 未知身份 | 非法 Token | 401 | 默认拒绝，查询不执行，DB 不落记录 |

## 接口调用示例

```powershell
# 1) 合法客服身份 -> answered（命中 2026 华东差价政策）
$body = @{ request_id="req-w05-001"; task_type="billing_policy_check"; region="华东"; purchased_at="2026-08-15"; amount=500 } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/policy-check -Method Post -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" -Headers @{ "X-Auth-Token"="token_agent_valid" }

# 2) 金额超限 -> needs_review / AMOUNT_EXCEEDS_LIMIT（主管复核预览）
$body.amount = 2500; Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/policy-check -Method Post -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json"

# 3) 非法身份 -> 401 默认拒绝（查询不执行）
Invoke-WebRequest -Uri http://127.0.0.1:8000/api/v1/policy-check -Method Post -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" -Headers @{ "X-Auth-Token"="fake_token" } -SkipHttpErrorCheck
```

`curl` 等价：`curl -X POST http://127.0.0.1:8000/api/v1/policy-check -H "X-Auth-Token: token_agent_valid" -H "Content-Type: application/json" -d "{...}"`。

**身份规则（服务端可信，请求体角色一律忽略）**：`token_agent_valid`→support_agent；
`token_supervisor_secret`→supervisor（可见全部政策）；缺失 Token→默认客服测试身份（教学模拟会话）；
其他任何值→401 denied。

## 交付边界（本周不做）

不使用模型 / RAG / 向量检索 / Agent / MCP；不自动撰写或发送客户回复；不拆微服务；
不主管批准闭环（留待第 7 周双角色动作）；不声称生产、采用、效率或 ROI —— 全部为本地教学模拟。
对 mission.md 参考实现的三处必要修正（DB 路径平台化 / list 下标缺陷 / 幂等初始化）记录于 ADR-001。

---

# Week 06 —— 建立可靠、可重放的数据入口（Ingestion）

依据《FDE 成长手册》第 6 周交付：在 Week 05 确定性薄片之上，唯一新增的难度是**数据不再默认干净**。
本周仍为**零大模型、100% 确定性规则**：每条数据进入可解释终态 —— `accepted`（带血缘入主表）/
`duplicate`（不产生第二次业务效果）/ `quarantined`（携带原因码进隔离区，绝不静默丢弃）。

- 数据合同：`data-contract-v1.md`（字段语义 / 业务键 / Default Deny / 日志边界）
- 运维手册：`ingestion-runbook-v0.md`（排障 / 修复 / 重放 / 熔断 SOP）
- 设计决策：`adr-002-ingestion-lineage.md`（对 mission 参考实现的适配记录）
- 实测证据：`evidence/batch-reconciliation-proof.json`（真实运行产物）
- 行为规格：`specs/mission-week-06.md` / `specs/business-background.md` 第 6 周

## 摄取管道数据路径

```
[原始数据批次 BatchIngestionRequest (batch_id, source_id, rows)]
      ▼
[ingestion/service.py validate_row] ──> 数据合同校验（业务键→所有者→ACL→地区→时间）
      ├── 校验失败  -> quarantine_records（原因码 + safe_source_ref + raw_payload 证据）
      ├── 业务键重复 -> duplicate 计数 + 隔离表对账痕迹，主表零写入
      └── 合法      -> policies 主表（血缘: source_id / source_version / batch_id / owner_id）
      ▼
[单事务原子提交 ingestion_batches] ──> 三处一致: input = accepted + duplicate + quarantined
[定向重放 execute_targeted_replay] ──> 原子事务: 主表(replay-{replay_id}) + resolved + 重放审计
```

## Week 06 新增目录

```
├── data-contract-v1.md                     # 数据合同 v1
├── ingestion-runbook-v0.md                 # 数据运维与隔离区排障 Runbook
├── adr-002-ingestion-lineage.md            # 摄取血缘设计决策
├── migrations/002_ingestion_schema.sql     # 批次/隔离/重放审计 DDL
├── ingestion/
│   ├── schema.py                           # 数据合同 DTO 与枚举
│   ├── repository.py                       # 持久化层（含原子重放事务与对账）
│   └── service.py                          # 校验/分流/幂等/定向重放 + 安全日志
├── evidence/batch-reconciliation-proof.json
└── tests/test_ingestion_reliability.py     # 8 个可靠性集成测试
```

## Week 06 快速验证

```powershell
# 全量测试（Week 05 基线 5 个 + Week 06 摄取 8 个，预期 Ran 13 tests ... OK）
python -m unittest discover -s tests -v

# 仅运行摄取可靠性测试
python -m unittest tests.test_ingestion_reliability -v
```

测试覆盖对照验收标准：

| 用例 | 验收标准 | 核对点 |
| --- | --- | --- |
| test_01 首次摄取 | 三处一致 / 血缘可追溯 / 无静默丢弃 | 6 行 = 3 accepted + 1 duplicate + 2 quarantined；主表与隔离区行数、原因码、`safe_source_ref` |
| test_02 批次重跑 | 批次幂等 | 同 `batch_id` 重跑零写副作用，原审计行不被覆盖，无第二个当前版本 |
| test_03 定向重放 | 可重放且保留历史 | 修复行入主表（`replay-` 前缀血缘），隔离记录 `resolved` + `replay_id`，原 `raw_payload` 不变 |
| test_04 重放拒绝 | 无假成功 | 未修复/已 resolved 的重放被拒，隔离状态不变，失败尝试也落审计 |
| test_05 默认拒绝 | Default Deny | 未知/缺失 `access_level` 绝不入主表，supervisor 数据不降级 |
| test_06 日志安全 | 结构化日志不泄密 | 16 个敏感标记（正文/公式/所有者/政策 ID）逐一断言不存在；对账公式可从日志核对 |
| test_07 跨批次重复 + 种子血缘 | 重复不产生第二次业务效果 | 新批次推送同业务键判 `duplicate`；Week 05 种子回填 `week05-baseline-seed` 血缘 |
| test_08 回接 Week 05 | 通过质量门的数据可服务业务 | 摄取数据进入 Week 05 确定性查询：重放前 `answered`，多版本后确定性 `POLICY_CONFLICT` |

## 摄取调用示例

```python
from ingestion.schema import BatchIngestionRequest, ReplayRequest
from ingestion.service import execute_targeted_replay, process_batch_ingestion

result = process_batch_ingestion(BatchIngestionRequest(
    batch_id="batch-w06-20260825-a",
    source_id="northstar-policy-export-v1",
    source_version="v1.0",
    correlation_id="ing-w06-a-001",
    rows=[{"policy_id": "POL-PLAN-01", "source_version": "v3", ...}],  # 见 data-contract-v1.md
))
# result.status == "completed_with_quarantine"；隔离记录修复后:
replay = execute_targeted_replay(ReplayRequest(
    quarantine_id="quar-...", replayed_by="knowledge-admin",
    fixed_payload={"owner_id": "knowledge-refund"},   # 支持部分补丁
))
```

## Week 06 交付边界（诚实不夸大）

摄取批次 `completed` 不等于数据质量完美，`completed_with_quarantine` 是正常运营状态；
本周不宣称用户采用、效率或商业 ROI；不实现流式/回填/多源对账平台（Week 06 明确不做）；
业务键唯一性由应用层三层防线保证（见 ADR-002 D1），多写入方并发唯一索引留待后续周。

---

# Week 07：一线角色与权限任务闭环（auth / tasks）

> 定位：把 Week 05 的"查询侧确定性垂直薄片"升级为**任务侧闭环**——一线客服在服务端授权内完成 核对 → 确认/拒绝/升级，主管结案；动作幂等、三向可对账、全程零模型调用。规格见 `specs/mission-week-07.md`。

## 第 7 周新增目录结构

```
application/
├── auth.py                        # 服务端可信会话鉴权 + RBAC 授权矩阵 (Default-Deny)
├── tasks.py                       # 任务状态机与幂等动作控制 (process_task_action)
├── main.py                        # + POST /api/v1/tasks/action, GET /api/v1/tasks
migrations/
└── 003_tasks_and_permissions_schema.sql   # billing_tasks + task_action_runs + 4 条工单夹具
tests/
└── test_frontline_tasks.py        # 7 个 E2E 用例 (全量 20/20)
workbench_cli.py                   # 最小一线工作台 CLI (纯标准库, 零新增依赖)
role-and-permission-matrix.md      # 角色与权限矩阵 v1 (第 1 天交付)
task-state-model.md                # 工单任务状态模型 v1 (第 1 天交付)
field-feedback-note-v1.md          # 受保护走查记录 (模拟角色扮演, 诚实边界)
decision-memo-02.md                # 上线裁决 (GO/NOT-GO + NO-GO 触发条件)
brief-review.md                    # Brief 复核报告 (10 项验收逐条对证)
adr-003-task-permission-adaptations.md  # 对 mission 参考实现的 5 项适配决策
```

## 任务接口与状态机

```
initialized ─check_policy─▶ answered ─confirm─▶ confirmed (终态)
                              │  └─refuse──▶ refused (终态)
                              └───escalate─▶ escalated ─supervisor_resolve(主管)─▶ action_completed
needs_review (超限/零匹配/冲突) ──escalate──▶ escalated
越权动作 ──▶ denied (仅审计, 状态不变)      非法转移 ──▶ INVALID_TASK_STATE (状态不变)
```

- `POST /api/v1/tasks/action`：`check_policy / confirm / refuse / escalate / supervisor_resolve`；
  身份只来自 `X-Auth-Token`（请求体自报角色被 `extra="forbid"` 直接 422）；
- `check_policy` 100% 复用 Week 05 确定性引擎（金额>2000 / 零匹配 / 版本冲突 → needs_review）；
- 三向对账键 `correlation_id` 贯穿：HTTP 响应 / `billing_tasks` 终态 / `task_action_runs` 审计。

## 第 7 周测试矩阵（`python -m unittest discover -s tests -v`，全量 20/20）

| 用例 | 验收点 | 关键证据 |
| --- | --- | --- |
| test_01 常规工单 check→confirm | 三向一致 + 可核验依据 | 响应/billing_tasks/task_action_runs 同 correlation_id；task_runs 亦有审计行 |
| test_02 超限 2500 元 check→escalate | 显式阻断不猜答案 | needs_review + AMOUNT_EXCEEDS_LIMIT，候选政策为 None |
| test_03 升级幂等 | 重复提交不重复生效 | ALREADY_APPLIED + is_idempotent=true，状态唯一 |
| test_04 客服直调主管接口 | 服务端一票否决 + 零泄漏 | denied/ACTION_NOT_ALLOWED；响应全文无 POL-2026-REF-09 |
| test_05 主管结案 | 三向一致 + 主管身份审计 | escalated→action_completed，user_role=supervisor |
| test_06 请求体自报角色 | 前端不是安全屏障 | extra="forbid" → 422，零审计零变更 |
| test_07 refuse + 非法 Token | 显式拒绝留痕；401 零副作用 | refused/REFUSED_BY_AGENT；401 后零审计零变更 |

## 一线工作台 CLI 使用

```powershell
uvicorn application.main:app --port 8000        # 窗口 1
python workbench_cli.py                          # 窗口 2：回车=客服身份 (token_agent_valid)，s=主管 (token_supervisor_secret)
# 操作：选工单 → check_policy 核对 SOP 三要素 (版本/生效日期/来源) → confirm / refuse / escalate
```

## 第 7 周交付边界（诚实不夸大）

Token 为模拟会话凭证（非生产 SSO）；同工单并发未加行锁（幂等键为第一道防线）；审计表无防篡改签名；
走查反馈为课程内模拟角色扮演，不含任何未测量的采纳率/ROI 声明；生产化清单见 `decision-memo-02.md`。

---

# Week 08 —— 可重复部署与回滚（配置收敛层 + 发布清单 + CI + 任务级部署门）

依据 `specs/mission-week-08.md`：在冻结的 Week 07 代码之上新增**部署配置层**，让"部署 = 代码基线 +
配置收敛 + 任务级冒烟验收"成为可重复动作；回滚是**配置收敛式**的（不删库、不改 DDL）。

## 第 8 周新增结构

```
fde-course-week-05/
├── release-manifest-v1.json            # 发布清单：code_revision=week07-baseline / config sha256 / schema 003
├── config/
│   ├── policy-config.json              # 发布配置 (known-good)：policy_baseline 收敛断言 + policy_overrides
│   └── policy-config-rc1-bad.json      # 回滚演练专用错误配置（受控注入证据，禁止真实发布）
├── deploy/
│   ├── apply_policy_config.py          # 配置收敛器：幂等 UPDATE，仅触碰白名单三列，不建表不删库
│   └── smoke_w08.py                    # 任务级冒烟 4 条（三向对账，纯标准库）
├── ci/
│   ├── run_local_ci.py                 # 本地 CI 四阶段流水线（= GitHub Actions 同一入口）
│   └── redaction_scan.py               # 日志脱敏静态扫描（凭据/政策正文不入运行时输出）
├── .github/workflows/ci.yml            # push/PR 触发，四阶段同一入口
├── deploy-README.md                    # 第三方视角部署手册（干净环境零假设）
├── ci-evidence.md                      # CI 运行证据（含两轮真实拦截与处置）
├── task-smoke-results-v1.md            # 真实干净目录部署 + 4/4 冒烟结果
├── rollback-drill-v1.md                # 注入→拦截→回滚→复验 演练报告
├── basic-observability-note-v1.md      # 基础可观测性说明（关联标识/错误码/脱敏裁决）
├── data-application-stage-exit-v1.md   # 第 5–8 周阶段退出记录
└── tests/test_deployment_release.py    # 9 个部署/发布自动化用例（全量 29/29）
```

## 第 8 周验证方式

```powershell
# 全量自动化测试（Week 05 基线 5 + Week 06 摄取 8 + Week 07 任务 7 + Week 08 部署 9）
python -m unittest discover -s tests -v          # 预期 Ran 29 tests ... OK（第 8 周时点；第 9 周起全量 35，见下文 Week 09）

# 本地 CI 四阶段（schema / 全量测试 / 脱敏扫描 / 干净环境任务冒烟）
python ci/run_local_ci.py                        # 预期 4/4 阶段 PASS，退出码 0
```

部署与回滚：见 `deploy-README.md`（第三方部署步骤）与 `ROLLBACK.md`（配置收敛式回滚）。
核心不变量：`/health` 200 ≠ 业务正确 —— 部署放量以 `deploy/smoke_w08.py` 4/4 通过为验收门。

## 第 8 周交付边界（诚实不夸大）

回滚对象是配置数据而非代码（不含 DDL 回退）；冒烟要求全新初始化数据库（一次性前置）；全部演练在单机
沙盒完成，不声称生产就绪、热回滚或组织级 ROI；配置内容正确性靠人工评审 + 任务冒烟门双防线，收敛器不判对错。


# Week 09 —— 有来源、有版本、有权限的 RAG 语料（语料治理，零模型调用）

依据 `specs/mission-week-09.md`：在冻结的 `week08-baseline` 之上，把八份北辰合成知识材料治理成
**可重复构建、权限受控的语料快照**。本周全程零模型调用——不做 embedding、不调检索、不生成回答、
不写提示词；只证明"语料可被治理"，不外推"RAG 改善任务"。

## 第 9 周新增结构

```
fde-course-week-05/
├─ corpus/
│  ├─ build_corpus.py                  # 确定性语料构建器（五态路由 + fixed-heading-chunker-v1）
│  ├─ check_role_candidates.py         # 角色候选核查（ACL 下推到 SQL WHERE，先过滤后候选）
│  ├─ corpus_schema.sql                # 语料两表 schema（独立于冻结迁移清单，见下）
│  └─ sources/                         # 8 份合成知识来源（逐份 sha256 绑定进清单）
├─ corpus-manifest-v1.json             # 交付物：语料快照清单（8 文档 / 18 chunks，确定性无墙钟）
├─ option-note-v1.md                   # 交付物：RAG 准入与四条路径比较
├─ knowledge-owner-acl-matrix-v1.md    # 交付物：来源-责任矩阵 + 8 份文档级治理合同 + 角色映射
├─ ingestion-isolation-report-v1.md    # 交付物：状态路由证据 + 两次构建逐字节比对
├─ role-candidate-check-v1.md          # 交付物：模型调用前三档角色原始候选检查（0 泄漏）
├─ corpus-card-v1.md                   # 交付物：语料卡片
├─ source-review-note-v1.md            # 交付物：知识所有者回读记录（模拟）
└─ tests/test_corpus_governance.py     # 6 个语料治理用例（全量 35/35；第 10 周 +6 → 41/41）
```

## 五态路由与权限下推

- 路由状态机：缺所有者/ACL/版本/生效期任一要件 → `quarantined`（默认隔离）；
  被谱系取代 → `expired`（与文件时间戳无关）；并行冲突 → `conflict_review`（保留供第 10–12 周
  识别冲突）；其余 → `accepted` / `accepted_restricted`（仅主管）。
  实测：8 份 → 2 accepted / 1 accepted_restricted / 1 expired / 2 conflict_review / 2 quarantined。
- chunk 原子继承文档身份与 ACL；混合权限段取**最严格权限**（实测案例
  `billing-general-v3#s06` 主管例外段）。
- 候选 SQL 把 ACL 谓词下推到 `WHERE`（`json_each(allowed_roles)`）：**先过滤后候选**，
  不允许"先全库召回再过滤"。实测：support_agent 13 / supervisor 18 / 未知角色 0，
  受限内容在 agent 候选中零出现（`corpus/check_role_candidates.py` 退出码门禁）。

## 第 9 周验证方式

```powershell
# 构建语料快照（两次构建清单逐字节一致；仓库根 corpus-manifest-v1.json 与全新重建一致）
python corpus/build_corpus.py --db data\corpus-w09.db --manifest corpus-manifest-v1.json

# 三档角色原始候选检查（agent 13 / supervisor 18 / unknown 0；泄漏时退出码 1）
python corpus/check_role_candidates.py --db data\corpus-w09.db

# 全量自动化测试（Week 05 基线 5 + Week 06 摄取 8 + Week 07 任务 7 + Week 08 部署 9 + Week 09 语料 6 + Week 10 评测合同 6）
python -m unittest discover -s tests -v          # 预期 Ran 41 tests ... OK

# 第 10 周评测件校验 + 确定性基线（零模型调用；两次运行逐字节一致）
python evaluation\validate_eval_set.py           # 60 行矩阵/封存/近重复/语料引用校验
python evaluation\run_deterministic_baseline.py  # deterministic-baseline-results-v1.jsonl
```

冻结面说明：语料两表 schema 独立存放于 `corpus/corpus_schema.sql`，不进入
`application/database.py::MIGRATION_SCRIPTS` 冻结清单 —— `release-manifest-v1.json`
（schema_version=003）与 `week08-baseline` 的绑定语义不变；语料库由构建器在 `init_db()`
底座（001–003）之上叠加执行，同一库同时承载冻结面与语料面。

## 第 9 周交付边界（诚实不夸大）

本周只证明"语料可被治理成权限受控、可重复构建的快照"：不宣称检索质量、不宣称 RAG 改善任务
（留给第 10–12 周冻结评测合同）；材料为教学合成（synthetic=true），无真实客户正文；
冲突对（upgrade-east-v2 vs upgrade-notice-v4）在所有者裁决前不得作为任何答案依据。

## 第 10 周：冻结评测合同（先冻结，后调优）

在运行任何 RAG 候选**之前**交付：评测计划（决定/非目标/七层指标/硬门）、
60 条评测考卷（36 开发 + 12 校准 + 12 标签封存保留；24 可回答 / 10 无答案 / 8 冲突 /
8 过期 / 10 权限受限）、确定性基线逐例结果、数据卡、标注指南、泄漏检查与冻结记录。

```
eval-plan-v1.md                        # 预注册：决定、指标分层、门槛、失败动作、开除线
eval-set-v1.jsonl                      # 60 条考卷输入（不含任何标签字段）
eval-holdout-labels-sealed-v1.json     # 保留集封存标签（sha256 入冻结记录，第 12 周后解封一次）
labeling-guide-v1.md                   # 判例口径 + 开发/校准 48 条公开标签附录
dataset-card-v1.md / split-leakage-check-v1.md / evaluation-freeze-record-v1.md
deterministic-baseline-results-v1.jsonl# w08-r0 逐例基线（answered 24 / needs_review 4 / not_applicable 20 / sealed 12）
evaluation/eval_contract.py            # 合同常量与装载器（矩阵/封存/绑定）
evaluation/validate_eval_set.py        # 确定性校验器（结构/矩阵/近重复/语料引用/封存）
evaluation/run_deterministic_baseline.py # 100% 复用 w08 引擎与 w09 ACL 候选 SQL 的只读探针
tests/test_eval_contract.py            # 6 个评测合同用例（全量 41/41）
```

### 第 10 周验证方式

```powershell
# 评测集校验（60 行、矩阵逐格精确、近重复零泄漏、封存边界、语料引用可解析）
python evaluation\validate_eval_set.py

# 确定性基线（零模型调用；request_id 由 case_id 派生，两次运行逐字节一致）
python evaluation\run_deterministic_baseline.py
```

### 第 10 周交付边界（诚实不夸大）

本周只证明"评测合同与 60 条考卷已在见任何候选结果之前冻结，第 8 周确定性基线已逐例跑通
且可复现"。不宣称检索质量、不宣称 RAG 改善任务；保留集为自我封存
（self-sealed ≠ independent blind test）；课程阈值不是行业标准或生产 SLO；
确定性基线在受限类 agent 例输出 answered 属引擎语义缺口（第 11-12 周评测对象），
不是评测集缺陷。


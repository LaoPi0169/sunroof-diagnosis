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

## 第 11 周：检索基线比较（keyword vs vector，检索层零生成）

在答案生成之前先回答"该检索谁进第 12 周"：两种检索器共用第 9 周 ACL 下推候选池
（先过滤后候选）、同一份 48 条公开 gold（第 10 周附录）、top_k=5，先冻结比较清单再运行；
保留集 12 条零执行；BM25 与余弦分数不可跨检索器比较，只比命中/名次/失败层/分数形态。

```
week-11/retrieval-comparison-manifest-v1.json  # 先冻结后运行：语料/ACL/考卷/标签 sha256 绑定 + 协议
week-11/keyword-results-v1.jsonl               # BM25 基线逐例 top-5（48 例，含 rank/score/latency/gold 对照）
week-11/vector-results-v1.jsonl                # 确定性哈希向量逐例 top-5（教学替身 embedding，诚实标注）
week-11/retrieval-failure-register-v1.md       # 6 条失败七字段五层归因（dev 4 + cal 登记 2）
week-11/retrieval-regression-seeds-v1.jsonl    # 12 条回归种子（双检索器行为锁定）
week-11/retrieval-baseline-report-v1.md        # 切片对比 + 置信信号 + 变更预算执行记录
week-11/retriever-decision-v1.md               # 五选一裁定（vector 单路晋级 + 降级路径预登记）
retrieval/contract.py                          # 统一检索合同（ACL 下推池 + BM25 + 哈希向量）
retrieval/labels.py                            # 公开标签装载器（只读解析冻结附录，覆盖/引用校验）
retrieval/run_retrieval_comparison.py          # 比较运行器（双跑确定性断言，排除 latency 投影）
tests/test_retrieval_comparison.py             # 6 个检索合同用例（全量 41 -> 47）
```

### 第 11 周验证方式

```powershell
# 检索比较（先冻结后运行；两次运行规范投影一致才落盘；保留集不执行）
python retrieval\run_retrieval_comparison.py

# 检索合同测试（清单 sha256 互锁 / ACL 零泄漏独立复算 / 确定性 / 保留集 / 登记与种子对账）
python -m unittest tests.test_retrieval_comparison -v
```

### 第 11 周结果要点与边界（诚实不夸大）

Recall@5 双检索器 1.0（≥0.7 晋级门达标）——但这是 13/18-chunk 小池的**饱和值**，
不构成质量结论；真区分度在名次与分数形态：vector 的 top-1 余弦把 8/8 无答案例（≤0.290）
与可回答例（≥0.304）完全分离，BM25 完全重叠，故裁定 vector 单路晋级、keyword 为降级路径；
阈值 0.30 类规则未冻结，须保留集验证。受限类 agent 逐例零泄漏（合计 0）；
冲突对与过期文档 0 chunk 被语料层冻结在候选池外（第 9 周设计），检索层不可见也不可修；
vector 为教学替身（blake2b 哈希），不宣称生产级语义检索质量；被召回 ≠ 答案正确。

## 第 12 周：只读 RAG 评测（三态回答合同 + 确定性生成替身 + 一次解封）

把第 11 周晋级的 `vector-hashed-ngram-v1` 接到只读回答层：对 60 条考卷产出
**answered / abstained / escalate** 三态 + 精确引用 + `reason_code`/`user_next_step`，
引用验证是确定性代码检查（非模型自评）。生成层如实标注为**确定性教学替身**
（本机无模型 API：允许池命中 chunk 的抽取式组装，不改写不补写；模型输出零业务效果）。
门控全部确定性、阈值/词表/映射预注册于 manifest（先冻结后解封）；路由只用
query+role+语料状态元数据，**显式弃用 task_context.category**（意图标签捷径）。

```
week-12/rag-system-manifest-v1.json            # 系统清单（阈值/门控/词表/映射/事实规则 预注册，输入 sha256 绑定）
week-12/rag-per-case-results-v1.jsonl          # 60 例逐例结果（一次解封运行落盘，存在即拒绝重写）
week-12/rag-evaluation-report-v1.md            # 七层指标 + 保留集结果 + 失败归因 + 边界
week-12/failure-register-v1.jsonl              # 8 层失败登记（corpus/ACL/retrieval/ranking-context/generation/citation/refusal/product-workflow）
week-12/regression-seeds-v1.jsonl              # 回归种子（行为锁定）
week-12/field-review-note-v1.md                # 一线走查记录（只读演示）
week-12/brief-review-week-12.md                # 导师评审简报
week-12/decision-memo-03.md                    # 五选一收官裁定
generation/contract.py                         # 三态回答合同 + 门控决策树 + 抽取式组装
generation/evaluate.py                         # 期望状态映射 + 事实校验规则 + 七层指标
generation/run_rag_evaluation.py               # 评测运行器（unseal-once 守卫 + 双跑确定性断言）
tests/test_rag_generation.py                   # 9 个 RAG 合同用例（全量 47 -> 56）
```

### 第 12 周验证方式

```powershell
# dev+cal 预演（保留集不解封；结果写临时目录）
python generation\run_rag_evaluation.py --splits dev,cal --out-dir <临时目录>

# 全量解封运行（须 manifest 已冻结 + 显式 --unseal-holdout；只允许一次）
python generation\run_rag_evaluation.py --splits all --unseal-holdout

# RAG 合同测试（manifest 冻结门 / 三态 schema / ACL 零泄漏 / 状态门回归 / 确定性 / 引用支持 / 指标对账 / 解封机制）
python -m unittest tests.test_rag_generation -v
```

### 第 12 周结果要点与边界（诚实不夸大）

dev+cal 48 例端到端状态匹配 48/48（L5=1.0，冻结分母），引用支持 30/30、受限零泄漏、
L3 答案保真 5/6=0.833（≥0.8）。**保留集 12 例 10 对，出现 1 例硬门失败**：
NS-HOLD-06（supervisor 语料外问句）池内 top-1 以 0.311 越过 0.30 作答线自信作答
（L4=9/10<100%），归因 generation/refusal 层；NS-HOLD-08 冲突雷达意图支配失败
（安全侧拒答，状态档位错）。**结果未用于调参**；失败全部登记进 8 层失败登记，
修复留给 manifest v2。解封前发生了检索工具意外带回封存件片段的事件，已按
污染披露处理（冻结记录 §五）——自我封存 ≠ 独立盲测。教学替身结论不得外推到
真实 LLM 生成；本周仍为零业务效果的只读基线，不宣称生产 SLO 或采用决策。

## 第 13 周：只读 MCP 工具（case.lookup_context@1 + 信任边界 + 工具在环回归）

给一线工单处理接上第一把**只读工具**：`case.lookup_context@1` 从模拟工单系统取回
工单上下文（task_type/region/purchased_at），替代请求体手工转写 task_context。
MCP 风格 Host/Client/Server 三方切分为**进程内确定性教学实现**（无网络、无 SDK、
零业务效果）。合同要素：`effect: read_only`、封闭字段枚举（单次 1..3）、
`additional_properties: reject`、trusted_context（tenant/role 只来自可信会话，
参数走私即拒绝）、结果上限 1 记录/3 字段、deadline 500ms、五类错误；服务端二次
鉴权（不存在/跨租户/不可见一律 not_available，用户面不可区分），超合同结果整体
拒绝并记安全事件。范围决定 A：本周只做工具周，RAG v1 冻结不动。

```
week-13/task-tool-decision.md                   # 九问决策 + 一句话检验 + 取消条件
week-13/mcp-tool-catalog-v1.md                  # 工具目录（登记制 + 手册对照例 + 负向清单）
week-13/trust-boundary-v1.md                    # 信任边界六分区（逐条对应合同）
week-13/mcp-read-contract-v1.json               # 封闭只读合同（错误五类/映射表冻结）
week-13/mcp-capability-manifest-v1.json         # 能力清单（先冻结后补记 + sha256 互锁）
week-13/read-tool-test-report-v1.md             # R/X 夹具 + 13 种子回归报告
mcptool/contract.py                             # 合同常量（与 JSON 同值互锁）
mcptool/case_store.py                           # 只读合成工单库（无写入口）
mcptool/server.py                               # 服务端：二次鉴权 + 字段过滤
mcptool/client.py                               # Host：允许列表 + deadline + 封套校验 + 受限审计
mcptool/run_fixtures.py                         # 夹具运行器（--only 单跑通道）
mcptool/run_regression.py                       # 13 种子工具在环对账
mcptool/demo_lookup.py                          # 演示入口
tests/test_mcp_read_tool.py                     # 10 个工具合同用例（全量 56 -> 66）
```

### 第 13 周验证方式

```powershell
# 演示：周宁在 NS-1042 上取回工单上下文（身份来自可信会话，请求不含 tenant/role）
python -m mcptool.demo_lookup

# 夹具：R-01..R-05 + X-01..X-03（外部写效果恒 0，工单库快照不变；--only 可单跑）
python -m mcptool.run_fixtures
python -m mcptool.run_fixtures --only R-03

# 13 条回归种子工具在环对账（与第 12 周锁定行为逐字段一致）
python -m mcptool.run_regression

# 工具合同测试（合同/清单互锁 / 不可区分性 / 超时 / 超合同拒绝 / 走私 / 零写效果 / 脱敏）
python -m unittest tests.test_mcp_read_tool -v
```

### 第 13 周结果要点与边界（诚实不夸大）

夹具 8/8 通过、外部写效果全 0、工单库快照逐字节一致；13 种子工具在环回归 13/13
与第 12 周逐字段一致（generation/* 零改动，保留集不运行）。教学实现练的是合同/
鉴权/边界模式，**不是真实 MCP 传输协议**；rogue/慢速服务端为夹具注入；RAG v1
no-go 决议不因工具周推翻；接第二个工具（写类）前须重走九问。

## 第 14 周：审批写工具（ticket.preview_create / create / get + 逐次审批 + 幂等执行）

给一线异常路径接上第一个**写工具**：政策冲突（RAG 终态 escalate）后，客服可为
NS-1042 创建 **1 张**主管复核单到 `billing-supervisor-review` 队列 —— 全程只发生在
**隔离模拟工单服务**（独立写 store，与只读 case_store 物理分离）。唯一允许效果 =
`create_one_review_ticket`（可逆，取消属第 15 周运营路径）；硬门 = 无审批/审批过期/
参数或身份不匹配时外部效果数为 0。三条物理边界：`write_contract.py`（单一规范化
函数喂预览/审批/执行三处）、`ticket_service.py`（独立写 store + 幂等表）、
`approval.py`（结构化审批记录，禁止读自然语言当批准）+ `policy.py`（模型外策略，
引用冻结语料冲突组 upgrade-east-2026-04）。执行前**重读当前审批逐项比对**（绑定
身份/运行/工具版本/参数/范围/策略/次数/有效期十要素），幂等键
`{run_id}:ticket.create:1`，`action_completed` 须 `ticket.get` 独立核验；
`result_unknown` 保留（写后断连对账第 15 周）。工具允许列表 1 → 4。

```
week-14/state-and-effect-map.md                 # 状态机（含 result_unknown 保留）+ 效果分级 + 硬门
week-14/effect-classification-v1.md             # 效果分类八问 + 一句话检验
week-14/ticket-write-contract-v1.json           # 封闭写合同（sha256 486b9362…b1b9b4 与代码常量互锁）
week-14/approval-policy-v1.md                   # 谁可请求/谁可批/什么使批准失效（绑定十要素）
week-14/approval-record-sample-v1.json          # 审批记录样例（拒绝 + 批准两条实际记录, restricted）
week-14/effect-audit-sample-v1.jsonl            # 按 run_id 事件链审计样例（restricted）
week-14/approved-write-test-report-v1.md        # W 夹具逐条 + 四次尝试对账 + 回归 + 验收门映射
mcptool/write_contract.py                       # 写合同常量 + 单一规范化函数
mcptool/ticket_service.py                       # 隔离模拟工单服务（唯一写 store, 幂等表）
mcptool/approval.py                             # 结构化审批服务（拒绝/过期/用尽）
mcptool/policy.py                               # 模型外策略组件（ticket-create-policy-v1）
mcptool/server.py                               # 扩展: 无副作用预览 / 执行前再授权写入 / 核验查询
mcptool/client.py                               # 扩展: 写通道（硬门复验 + 效果增量审计）
mcptool/run_fixtures.py                         # 扩展: W-01..W-05 写负向夹具（--suite write|all）
mcptool/demo_write_approval.py                  # 演示: 四次尝试对账（0→1→1→1）
tests/test_mcp_write_tool.py                    # 12 个写工具用例（全量 66 -> 78）
```

### 第 14 周验证方式

```powershell
# 演示：四次尝试（预览 0 → 匹配执行 1 → 改队列复用审批被拒仍 1 → 幂等重放仍 1）
python -m mcptool.demo_write_approval

# 写负向夹具 W-01..W-05（无审批/过期/改参数/重放/用户拒绝; 期望新增效果合计恰 1）
python -m mcptool.run_fixtures --suite write
python -m mcptool.run_fixtures --suite all   # 只读 8 条 + 写 5 条

# 写工具合同测试（互锁/零效果预览/走私/负向四类+拒绝/幂等/核验/脱敏）
python -m unittest tests.test_mcp_write_tool -v
```

### 第 14 周结果要点与边界（诚实不夸大）

写夹具 5/5（效果增量 0/0/0/1/0，合计恰 1）、只读夹具 8/8 不受扰、13 种子工具在环
回归 13/13、全量 66 → 78 全绿；四次尝试外部状态对账 0→1→1→1（SIM-5001）。写效果
只发生在隔离模拟服务；拒绝/过期/不匹配路径效果恒 0 且 `approval_denied` 是正常
终态。审批样例与审计样例为固定时钟实际运行导出（属受限审计面，不入普通日志）。
`result_unknown`/撤销/断连对账/补偿留第 15 周；「请求人逐次确认」仅适用于本低影响
效果，不外推为通用规则；不宣称真实工单系统安全。

### 第 14 周业务用户交互工作台（无命令行测试入口）

交付业务用户**纯浏览器点击**测试：`workbench_write/` 是 MCP Host 之上的薄 HTTP
壳（不实现新业务规则、不新增写入口），页面流程「预览 → 拒绝/批准 → 执行（可改参/
重放）→ 核验」与四次尝试对账一一对应；时钟用真实 UTC（审批 TTL 900s 真实生效，
等 15 分钟可实测过期路径）。交付人双击 `start_workbench.bat`（或
`python -m uvicorn workbench_write.app:app --host 127.0.0.1 --port 8014`）启动一次，
测试者只打开 `http://127.0.0.1:8014`。逐步测试脚本（每步点击路径 + 精确预期 +
通过判据）见 `week-14/business-user-test-script-v1.md`。

```
workbench_write/app.py                           # FastAPI 薄壳: 进程内封装 Host/审批/工单栈
workbench_write/static/index.html                # 中文前台页面（原生 HTML/JS, 零依赖零构建）
start_workbench.bat                              # 交付人双击启动（自动打开浏览器）
tests/test_write_workbench.py                    # 4 个工作台 API 用例（全量 78 -> 82）
week-14/business-user-test-script-v1.md          # 业务用户逐步测试脚本
```

工作台验证：`python -m unittest tests.test_write_workbench -v`（4 用例），全量
66 → 78 → 82 全绿；`ci/redaction_scan.py` SCOPE_FILES 34 → 36（工作台 2 个运行时
文件入列，凭据/政策正文仍零出现）。会话为单用户教学模拟（固定客服周宁），审批/
审计面板属 restricted 审计面（含批准载荷），仅回显给本人、不写日志文件。

### 第 15 周：攻击、撤销并恢复受控动作（对抗 + 撤销 + 断连对账 + 运营补偿）

依据 `specs/business-background.md` 3438-3665，用同一张复核单（NS-1042）重演
「七步攻击」：恶意文档伪装指令（A-01）、工具描述自称 always authorized（A-02）、
参数走私（A-03）、审批后执行前撤销（A-04）、工具/schema 版本变化（A-05）、写后
断连（A-06）、运营补偿取消（A-07）。核心机制（全部落在确定性组件，不靠提示词）：

- **能力清单门**（`mcptool/capability_manifest.py`）：served 工具描述/参数模式与
  冻结清单 `week-15/mcp-capability-manifest-v2.json` 的 canonical-json 摘要逐字节
  等值比对，不符 → `capability_quarantined` 隔离 + 安全事件（server_calls 不增加，
  审批额度不消耗）；清单文件字节级 sha256 与代码常量互锁，被改 → 装载即失败
  （fail closed）。文本永远不能创建身份、权限或审批——比对是摘要等值，不做
  「像不像命令」的语义判断。
- **执行前撤销**（`approval.py revoke`）：追加式——原记录与绑定字段全保留，
  decision 变 `revoked` 并记撤销人/时刻/原因；仅未使用批准可撤销；server 执行前
  重读当前状态 → `approval_revoked`（前端显示「已批准」不算数）。
- **写后断连对账**（`client.py` 编排）：`ticket_service` 断连注入缝在提交成功后
  抛 `ResponseLostError` → Host 进入 `result_unknown` → **先按幂等键查询外部事实**
  （恢复第一动作永远是查询，不是再次创建）→ 查到且 `ticket.get` 独立核验通过 →
  `action_completed`（`recovered_from=result_unknown`，追加 `reconciled` 事件）；
  查询不可用/未查到 → 停 `reconciliation_pending`，不伪造终态、不盲重试。查询
  不可用期间的创建本身 fail-closed 拒绝（0 效果）。`reconcile(run_id)` 为只读
  对账入口，效果增量 ≠ 0 即 contract_violation。
- **运营补偿**（`ticket_service.cancel_ticket`）：仅 `ops_admin`；不在模型工具
  目录/Host 允许列表（`ticket.cancel` 不存在）；取消只改 state 并追加事件链
  （`created → [response_lost → reconciled] → cancellation_approved → cancelled`），
  幂等表与原工单保留——补偿不删除历史。
- 合同 v2（`week-15/ticket-write-contract-v2.json`，sha256 `f9afabdc…e931f4` 与
  代码常量互锁）：错误八类 → **十类**（+`approval_revoked`、`capability_quarantined`），
  终态补 `approval_revoked`/`result_unknown`/`reconciliation_pending`；
  **week-14 v1 冻结物一字节不动**（摘要 `486b9362…b1b9b4` 由测试锁定）。

```
week-15/ticket-write-contract-v2.json            # 封闭写合同 v2（撤销/清单门/对账/补偿；sha256 与代码互锁）
week-15/mcp-capability-manifest-v2.json          # 能力冻结清单 v2（4 工具描述+schema 摘要；隔离策略；补偿排除项）
week-15/business-user-test-script-v1.md          # 业务用户逐步测试脚本（T-00..T-12, A-01..A-07）
mcptool/capability_manifest.py                   # 清单装载(sha 互锁) + served 目录 + 摘要比对
mcptool/write_contract.py                        # 扩展: 状态 result_unknown/reconciliation_pending + 错误十类
mcptool/approval.py                              # 扩展: revoke（追加式, 仅未使用批准可撤销）
mcptool/ticket_service.py                        # 扩展: 事件链/断连缝/查询不可用缝/cancel_ticket(ops_admin)
mcptool/server.py                                # 扩展: 撤销门 + fail_after_commit 透传 + 幂等查询不可用防御
mcptool/client.py                                # 扩展: 能力清单门 + result_unknown 对账编排 + reconcile()
workbench_adversarial/app.py                     # 对抗工作台薄壳: 七张攻击卡一键重演 + 手动撤销/断连/对账/取消
workbench_adversarial/static/index.html          # 中文前台页面（原生 HTML/JS, 零依赖零构建）
start_workbench_adversarial.bat                  # 交付人双击启动（端口 8015, 自动打开浏览器）
tests/test_mcp_adversarial.py                    # 12 个对抗用例（全量 82 -> 94）
```

### 第 15 周验证方式

```powershell
# 业务用户交互工作台（无命令行测试入口）
start_workbench_adversarial.bat          # 双击启动, 打开 http://127.0.0.1:8015

# 对抗测试（合同/清单互锁 + A-01..A-07 + 断连对账 + 运营补偿 + 工作台端到端 + 脱敏）
python -m unittest tests.test_mcp_adversarial -v
```

### 第 15 周结果要点与边界（诚实不夸大）

对抗用例 12/12：A-01..A-07 攻击面外部效果恒 0（A-06 断连恰 1 且不重复）；
A-02/A-05 隔离事件入安全事件且 server_calls 不增加；撤销后执行 0 效果、撤销
元数据只进受限审计；查询不可用时对账停 `reconciliation_pending` 且不泄露工单
存在性；运营取消保留完整事件链。工作台 `workbench_adversarial/` 七张攻击卡
一键重演（自动重置沙盒、逐步展示「输入/允许/外部状态」），手动面板支持自由
组合撤销/断连/对账/取消；全量 82 → 94 全绿；`ci/redaction_scan.py` SCOPE_FILES
36 → 39（capability_manifest 与对抗工作台 3 个运行时文件入列，零违规）。
不增加模型可调用工具（仍 1 读 + 3 写）；不宣称真实工单系统安全；阻断这 7 类
课程攻击不等价于「系统已安全」。威胁模型 v2 / 审批策略 v2 / 对抗逐例报告 /
effect-audit jsonl / runbook / decision-memo-04 六份周文档另行交付。








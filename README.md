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

预期输出 `Ran 5 tests ... OK`。测试覆盖（每个用例均独立临时库，且同时核对 API 响应与 DB 终态）：

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

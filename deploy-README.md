# 部署手册 (deploy-README) —— northstar-app-w08-r0

> 读者: 负责把本发布物部署到一个**全新环境**的第三方工程师。
> 原则: 本手册不假设你了解作者的开发环境; 所有命令可复制; 任一步骤失败都会给出明确的诊断动作。
> 版本绑定见 `release-manifest-v1.json`（代码 revision / 配置 sha256 / schema 版本）。

## 0. 环境要求（Checklist）

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Windows 10+ 或任何 Linux/macOS | 本手册给出 PowerShell 与 bash 两栏命令 |
| Python | 3.10+（建议 3.12） | 仅标准库 + requirements.txt 依赖 |
| 依赖包 | `fastapi`, `uvicorn`, `pydantic`, `httpx` | 见 `requirements.txt`；冒烟脚本零额外依赖 |
| 网络 | **无需任何外网/内网服务** | 无模型调用、无外部数据库；SQLite 随应用本地创建 |
| 端口 | 一个空闲 TCP 端口（示例用 8000） | 由你指定，不是硬编码 |

**隔离要求（重要）**: 目标环境不得继承作者机器上的任何业务环境变量。唯一的业务环境变量是
`FDE_DB_PATH`（数据库文件路径），必须显式设置；可选 `FDE_ENV` / `FDE_CONFIG_VERSION` 仅用于标识，
不影响行为。

## 1. 获取发布物

```powershell
# PowerShell（Windows）
git clone <本仓库地址> northstar-deploy
cd northstar-deploy
git checkout week08-baseline   # 本清单发布对应的标签; 若仓库内没有该标签, 使用包含 release-manifest-v1.json 的最新 main
```

```bash
# bash（Linux/macOS）
git clone <本仓库地址> northstar-deploy
cd northstar-deploy
git checkout week08-baseline   # 同上
```

发布物内与本手册相关的目录结构：

```
application/            # 应用代码 (冻结自 week07-baseline)
migrations/             # 001/002/003 迁移脚本 (幂等)
config/policy-config.json   # 发布配置 (known-good, sha256 见 manifest)
deploy/apply_policy_config.py   # 配置收敛器 (幂等 UPDATE)
deploy/smoke_w08.py             # 任务级冒烟 (4 条, 三向对账)
release-manifest-v1.json        # 版本绑定清单
requirements.txt
```

## 2. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 3. 初始化数据库（幂等迁移）

设置数据库路径（示例为部署目录下的 `northstar.db`，可改为任意绝对路径）：

```powershell
$env:FDE_DB_PATH = "$PWD\northstar.db"
.\.venv\Scripts\python.exe -c "from application.database import init_db; init_db()"
```

```bash
export FDE_DB_PATH="$PWD/northstar.db"
./.venv/bin/python -c "from application.database import init_db; init_db()"
```

- 迁移 001→002→003 按序幂等执行；重复运行安全。
- 验证：命令无输出且退出码为 0。

## 4. 应用发布配置（幂等收敛）

```powershell
.\.venv\Scripts\python.exe deploy\apply_policy_config.py --config config\policy-config.json --db $env:FDE_DB_PATH
```

```bash
./.venv/bin/python deploy/apply_policy_config.py --config config/policy-config.json --db "$FDE_DB_PATH"
```

- 期望输出：JSON 报告中每个 `policy_id` 的 `"converged": true`，退出码 0。
- 重复执行安全（幂等 UPDATE，仅收敛 `is_deprecated/effective_at/expires_at` 白名单三列；不建表、不删库）。

## 5. 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn application.main:app --port 8000
```

```bash
./.venv/bin/python -m uvicorn application.main:app --port 8000
```

- 保持该终端运行；服务启动时 lifespan 会自动执行幂等迁移（与第 3 步等效，重复安全）。
- 冒烟脚本要求**全新数据库**（夹具工单处于 `initialized` 初始态）。若该库曾被手动操作过，删除
  `$env:FDE_DB_PATH` 文件后重跑第 3、4 步即可重建。

## 6. 任务级冒烟（部署验收门）

新开一个终端：

```powershell
$env:FDE_DB_PATH = "$PWD\northstar.db"   # 与第 3 步一致, 用于三向对账读取数据库
.\.venv\Scripts\python.exe deploy\smoke_w08.py --base-url http://127.0.0.1:8000 --db $env:FDE_DB_PATH
```

```bash
export FDE_DB_PATH="$PWD/northstar.db"
./.venv/bin/python deploy/smoke_w08.py --base-url http://127.0.0.1:8000 --db "$FDE_DB_PATH"
```

**验收标准：退出码 0，报告中 `"all_passed": true`，4/4 条冒烟通过。**

| 冒烟 | 场景 | 必须看到 |
| --- | --- | --- |
| SMOKE-01 | 常规工单核对+确认 | 响应 `confirmed` = `billing_tasks` 终态 = `task_action_runs` 审计；匹配 POL-2026-BIL-01 v3 + SOP 来源 |
| SMOKE-02 | 高金额工单触发权限门槛 | `needs_review`/`AMOUNT_EXCEEDS_LIMIT` → `escalated`，三向一致 |
| SMOKE-03 | 普通客服越权调主管接口 | 响应 `denied`、`candidate_policy: null`、零泄漏；工单状态未被篡改；阻断事件入审计 |
| SMOKE-04 | 相同幂等键重放 | `is_idempotent: true` + `ALREADY_APPLIED`；无重复生效 |

> 注意：冒烟**不只看 `/health` 200**。`/health` 仅表示进程存活（SMOKE-01..04 才验证任务终态）。
> 本手册第 6 节的 4 条冒烟即部署验收门：任何一条失败 = 部署不通过，禁止放量。

## 7. （可选）运行完整测试套件

```powershell
.\.venv\Scripts\python.exe ci\run_local_ci.py
```

```bash
./.venv/bin/python ci/run_local_ci.py
```

期望：4 个阶段全部 PASS，退出码 0（详见 `ci-evidence.md`）。

## 8. 常见故障诊断

| 症状 | 原因 | 处置 |
| --- | --- | --- |
| 冒烟报 `cannot reach ...` | 服务未启动或端口不符 | 检查第 5 步终端的 uvicorn 日志；确认 `--base-url` 端口一致 |
| 冒烟 SMOKE-01/02 断言 `initialized` 失败 | 数据库不是全新初始化 | 删除 `FDE_DB_PATH` 文件 → 重跑第 3、4 步 → 重启服务（第 5 步） |
| 收敛器退出码 1 且 `converged: false` | policies 表缺列（未迁移）或配置被篡改 | 重跑第 3 步迁移；核对 `config/policy-config.json` 的 sha256 与 manifest 一致 |
| `ModuleNotFoundError: application` | 在错误目录执行 | 所有命令必须在发布物根目录执行 |
| uvicorn 端口被占用 | 8000 已被使用 | 换端口并在冒烟命令中同步替换 `--base-url` |

## 9. 回滚

见根目录 `ROLLBACK.md`（配置收敛式回滚：重放 known-good 配置即可，不删库、不重建表、不改 DDL）。

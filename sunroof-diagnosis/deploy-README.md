# deploy-README —— 独立环境部署手册（第三方视角，零假设）

- **规范版本**: `sunroof-deploy-readme-v1`（回链 `release-manifest-v1.json` / `specs/mission-week-08.md`）
- **目标读者**: 从未接触过本机的第三方工程师。仅凭本文件 + 仓库内容，在全新目录完成部署与任务级复验。
- **发布对象**: `sunroof-app-w08-r0`（清单三类绑定见 `release-manifest-v1.json`）

## 0. 部署不变量（先读）

1. `/health` 返回 200 **不代表业务正确**——放量只以任务级冒烟 4/4 为准（`deploy/smoke_w08.py`）；
2. 配置不是代码的一部分：代码来自 git 基线，配置通过**收敛器**幂等写入 `policy_config` 表（唯一可写面，零 DDL）；
3. 本系统为单机沙盒教学部署，不宣称生产就绪、不含真实 SSO/K8s/热回滚。

## 1. 前置条件（唯一假设）

- Python ≥ 3.10（开发验证环境 3.13）；
- 能访问一个已 clone 的仓库副本（部署物 = `git archive HEAD`，与 CI STAGE-4 同源）。

## 2. 安装与初始化（幂等，可重复执行）

```bash
# 1) 全新目录检出代码基线（或直接使用已 clone 的 sunroof-diagnosis/ 目录）
cd sunroof-diagnosis

# 2) 创建独立虚拟环境并安装依赖（requirements.txt 仅 4 个声明，无锁文件则按 SemVer 范围解析）
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 3) 初始化数据库（幂等迁移 001-004：DDL + 固定夹具 + 发布配置种子）
set FDE_DB_PATH=%CD%\data\sunroof.db        # Linux/macOS: export FDE_DB_PATH=$PWD/data/sunroof.db
python -c "from application.database import init_db; init_db()"

# 4) 应用发布配置（幂等收敛器：白名单键 UPSERT，不建表不删库）
python deploy/apply_policy_config.py --config config/sunroof-config-w08.json --db "%FDE_DB_PATH%"
#    退出码 0 = 全部收敛（逐项 converged 断言）；1 = 存在未收敛项（fail fast）
```

## 3. 启动与任务级冒烟（放量门）

```bash
# 5) 启动服务（默认端口 8020；测试可用任意空闲端口）
python -m uvicorn application.main:app --host 127.0.0.1 --port 8020

# 6) 另开终端运行任务级冒烟 4 条（三向对账：响应 + spec_check_tasks 终态 + 审计）
python deploy/smoke_w08.py --base-url http://127.0.0.1:8020 --db "%FDE_DB_PATH%"
#    退出码 0 = 4/4 PASS；1 = 存在失败（部署自动阻断，禁止放量）；2 = 连接失败
```

**冒烟前置条件**：目标实例必须是**全新初始化**的数据库（任务夹具为 `initialized`
初始态）。任务状态机终态不可重放，复验请重新执行第 2 步第 3 小步（换新库路径或删除旧库重建）。

## 4. 冒烟内容（4 条，对应验收标准）

| ID | 场景 | 三向断言 |
| --- | --- | --- |
| SMOKE-01 | 常规任务核对并确认（+1.05% 偏差） | answered/SPEC-SR-A-V5 + DB 终态 + task_runs/task_action_runs 审计 |
| SMOKE-02 | 超硬限任务阻断并升级（+21.05%） | needs_review 零规格内容 + escalate 终态 + 审计 |
| SMOKE-03 | PQE 越权调主管结案 | denied/ACTION_NOT_ALLOWED + 全文零泄漏 + 状态不变 |
| SMOKE-04 | 同幂等键重复升级 | ALREADY_APPLIED + 无重复生效（首次审计仍 2 条） |

## 5. 部署验证（第三方完整复验路径）

```bash
# 一条命令完成全部验证：Schema -> 35 用例 -> 脱敏扫描 -> 干净环境冷部署 + 4/4 冒烟
python ci/run_local_ci.py
```

CI 证据追加写入 `evidence/ci-run-w08.log`，人工汇总见 `ci-evidence.md`。

## 6. 回滚

配置错误 → `ROLLBACK.md` 场景 A（配置收敛式回滚，重放 known-good 配置）；
完整演练记录见 `rollback-drill-v1.md`（真实运行产物 `evidence/rollback-drill-proof.json`）。

## 7. 已知边界（诚实声明）

- 单机单进程沙盒；SQLite 无并发行锁设计（生产化需乐观锁，见 decision-memo-02 差距清单）；
- Token 为静态教学映射（`token_pqe_oem_a/b`、`token_qa_supervisor_secret`），非真实身份源；
- 回滚对象是**配置数据**而非代码；DDL 迁移（001-004）只增不改、不做降级。

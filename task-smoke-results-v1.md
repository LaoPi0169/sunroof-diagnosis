# Week 08 任务级冒烟结果 (task-smoke-results-v1)

- **日期**: 2026-09-30
- **发布**: `northstar-app-w08-r0`（见 `release-manifest-v1.json`；代码 revision = `week07-baseline`，tooling = week08 分支）
- **执行方式**: 严格按 `deploy-README.md` 步骤，由"第三方工程师视角"在作者环境之外的真实干净目录执行（非测试框架内模拟）。

## 一、执行环境（全新，零继承）

| 项 | 值 |
| --- | --- |
| 干净目录 | `D:\WebstormProjects\fde-w08-clean-deploy\app`（执行前不存在） |
| 发布物来源 | `git archive HEAD` 导出 zip 解压 —— 与第三方检出标签拿到的内容一致（只含已提交文件） |
| Python | 3.13.15（独立 venv，`.venv` 于干净目录新建） |
| 依赖安装 | `pip install -r requirements.txt`（fastapi/uvicorn/pydantic/httpx） |
| 数据库 | `$PWD\northstar.db`（全新文件，`init_db()` 幂等迁移 001→002→003） |
| 环境变量 | 显式设置 `FDE_DB_PATH` / `FDE_ENV=third-party-clean-env` / `FDE_CONFIG_VERSION=northstar-config-w08-v1` |
| 服务 | `python -m uvicorn application.main:app --port 8001`（独立进程） |

## 二、执行步骤与结果

| # | 步骤（deploy-README 节） | 命令要点 | 结果 |
| --- | --- | --- | --- |
| 1 | 导出发布物 + venv + 依赖 | `git archive` / `python -m venv` / `pip install` | OK，Python 3.13.15 |
| 2 | 初始化数据库（幂等迁移） | `python -c "from application.database import init_db; init_db()"` | `INIT_DB OK`，退出码 0 |
| 3 | 应用发布配置（幂等收敛） | `deploy\apply_policy_config.py --config config\policy-config.json --db %FDE_DB_PATH%` | 3/3 策略 `converged: true`，`errors: []`，退出码 0 |
| 4 | 健康检查 | `GET /health` | `HEALTH_200=True` |
| 5 | **任务级冒烟（验收门）** | `deploy\smoke_w08.py --base-url http://127.0.0.1:8001 --db %FDE_DB_PATH%` | **4/4 PASS，`all_passed: true`，退出码 0** |
| 6 | 服务停止 | `Stop-Process` | 正常停止，无残留进程 |

## 三、冒烟明细（三向对账：一线响应 / billing_tasks 终态 / task_action_runs 审计）

| 冒烟 | 场景 | 关键断言（全部通过） |
| --- | --- | --- |
| SMOKE-01 | 常规计费工单核对并确认 | check → `answered`，匹配 `POL-2026-BIL-01 v3` + 来源 `KB-BILLING-2026-08`（SOP 三要素）；confirm → `confirmed`；DB 终态 `confirmed/POL-2026-BIL-01`；审计 2 条且动作有序 `[check_policy/answered, confirm/confirmed]`；Week 05 `task_runs` 审计衔接 `(answered, POL-2026-BIL-01)`；三向一致（corr-smoke-01） |
| SMOKE-02 | 高金额工单触发权限门槛 | check → `needs_review/AMOUNT_EXCEEDS_LIMIT` 且 `candidate_policy: null`；escalate → `escalated`；DB 终态 `escalated/AMOUNT_EXCEEDS_LIMIT`；审计 2 条；三向一致（corr-smoke-02） |
| SMOKE-03 | 普通客服越权调主管接口 | 响应 `denied/ACTION_NOT_ALLOWED`；`candidate_policy: null` 且响应全文无 `POL-2026-REF-09`/政策正文/token（零泄漏）；工单状态未被篡改（仍 `escalated`）；阻断事件入审计 `(supervisor_resolve, denied, support_agent)` |
| SMOKE-04 | 相同幂等键重放 | `is_idempotent: true` + `ALREADY_APPLIED`；DB 保持首条 `escalated` 且幂等键唯一；无重复生效（首次升级审计仍 2 条，重放仅新增 1 条 already_applied 审计） |

> **为什么这不是"假成功"冒烟**：`/health` 200 只证明进程存活（第 4 步）。第 5 步的 4 条冒烟每条都同时断言
> [用户可见响应] + [数据库任务终态] + [审计留痕]，三者经同一 `correlation_id` 关联 —— 服务"活着但业务坏了"
> 的场景会被 SMOKE-01/02 拦截（复现实验见 `rollback-drill-v1.md` 注入轮）。

## 四、结论

- 干净环境、全新数据库、独立进程下，发布物按手册**一次部署成功**，任务级冒烟 **4/4 通过**（部署验收门通过）。
- 全程无模型调用、无外部网络依赖、无作者环境变量泄漏（唯一业务变量 `FDE_DB_PATH` 为显式设置）。
- 原始 JSON 输出由执行终端捕获并核对，结构与 `deploy/smoke_w08.py` 定义的 `checks` 一致。

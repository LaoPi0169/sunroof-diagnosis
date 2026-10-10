# CI 证据汇总（ci-evidence）

- **流水线**: `ci/run_local_ci.py`（与 `.github/workflows/sunroof-ci.yml` 同一入口，四阶段）
- **证据日志**: `evidence/ci-run-w08.log`（每次运行追加；本文件为人工汇总，与提交记录对照）

## 节点 ⑤ CI 运行记录

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 临时库幂等迁移 001-004 + `PRAGMA integrity_check` + 必需表（specs/task_runs/spec_check_tasks/task_action_runs/policy_config）+ 摄取表 + 种子 2 行 | PASS |
| STAGE-2-unittest | 全量集成测试（薄片/摄取/任务闭环/部署发布） | PASS（**35 tests，OK**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（11 文件 / 39 处输出语句 / 凭据+规格正文双规则） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` → 全新目录冷部署 → 应用 `sunroof-config-w08-v1` → `deploy/smoke_w08.py` | PASS（**4/4**，部署自动阻断门生效） |

- **STAGE-4 语义**：部署物来自 git HEAD（等价干净检出），显式环境变量隔离
  （`FDE_ENV=isolated-ci-env-01`），证明第三方凭仓库内容即可复验主路径——
  CI 失败即阻断发布，`/health` 200 不作为通过依据。
- **关键不变量复验**：冒烟 4 条中 SMOKE-03/04 同时证明节点 ④ 能力不退化
  （越权 denied 零泄漏 + 幂等 ALREADY_APPLIED）。

## 与其他证据的关系

- 回滚演练三段闭环（R0/R1/R2）见 `rollback-drill-v1.md` + `evidence/rollback-drill-proof.json`；
- 任务级冒烟逐条断言明细见 `task-smoke-results-v1.md`；
- 节点 ③④ 冒烟证据见 `evidence/batch-reconciliation-proof.json` / `evidence/task-closure-proof.json`。

## 诚实边界

CI 为本机单进程流水线（uvicorn 子进程 + 临时目录），不宣称分布式 CI/容器化/制品库推送；
`evidence/ci-run-w08.log` 的运行时刻戳随每次复跑追加，以 git 提交记录为准对照代码基线。

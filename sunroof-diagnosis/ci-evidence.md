# CI 证据汇总（ci-evidence）

- **流水线**: `ci/run_local_ci.py`（与 `.github/workflows/sunroof-ci.yml` 同一入口，四阶段）
- **证据日志**: `evidence/ci-run-w08.log`（节点 ⑤）/ `evidence/ci-run-w09.log`（节点 ⑥ 起；
  每次运行追加；本文件为人工汇总，与提交记录对照）

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

## 节点 ⑥ CI 运行记录（Week 09 · 语料治理）

四阶段入口不变；变化全部在 STAGE-2/3 的覆盖面，STAGE-1/4 冻结面零改动：

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-004（**仍为 4 个，语料两表不走冻结清单**）+ 完整性 + 必需表 + policy_config 种子 | PASS |
| STAGE-2-unittest | 全量集成测试（薄片/摄取/任务闭环/部署发布/**语料治理**） | PASS（**41 tests，OK = 35 + 语料 6**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**13 文件** / 42 处输出语句 / 凭据+规格正文双规则） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门（语义不变，节点 ⑤ 冻结） | PASS（**4/4**） |

Week 09 变化点：

- STAGE-2 全量测试 35 → 41（新增 `tests/test_corpus_governance.py` 6 个语料治理用例：
  两次构建确定性 / 缺要件默认隔离 / supersedes 谱系过期 / 冲突对保留 / 角色候选零泄漏
  （精确候选集合断言，含 `service-general-v3#s06` 排除）/ 清单绑定全新重建）；
- STAGE-3 扫描范围 11 → 13 文件（新增 `corpus/build_corpus.py`、
  `corpus/check_role_candidates.py`；输出语句 39 → 42，零违规）；
- 语料构建与角色候选核查**不在 CI 独立成阶段**：其确定性/零泄漏门由 STAGE-2 测试用例
  固化（含清单防漂移绑定），避免 CI 与测试双轨维护同一断言；
- `data/corpus-w09.db` 为本地演示库（.gitignore 排除），CI 与测试一律临时库隔离。

## 诚实边界

CI 为本机单进程流水线（uvicorn 子进程 + 临时目录），不宣称分布式 CI/容器化/制品库推送；
`evidence/ci-run-w08.log` 的运行时刻戳随每次复跑追加，以 git 提交记录为准对照代码基线。

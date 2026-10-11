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
- `data/corpus-w09.db` 为本地演示库（.gitignore 排除），CI 与测试一律临时库隔离；
- **已提交树复跑**（合并提交 `a52870c` 上）：41/41 OK + CI 四阶段 PASS
  （STAGE-4 冷部署 4/4）+ 语料全新重建 8/18 五态 2/1/1/2/2 + 角色候选
  pqe 13 / quality_supervisor 18 / unknown 0 全 PASS —— 证据见 `evidence/ci-run-w09.log` 第二次追加运行。

## 6. Week 10 —— 评测合同冻结（节点 ⑦，2026-10-10）

CI RUN 2026-10-10 | host=local | python=3.13 | 入口 `ci/run_local_ci.py --evidence-file evidence/ci-run-w10.log`

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-004（冻结面零改动，评测件不走迁移清单） | PASS |
| STAGE-2-unittest | 全量集成测试（薄片/摄取/任务闭环/部署发布/语料治理/**评测合同**） | PASS（**47 tests，OK = 41 + 评测 6**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**16 文件** / 凭据+规格正文双规则） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门（节点 ⑤ 冻结语义不变） | PASS（**4/4**） |

Week 10 变化点：

- STAGE-2 全量测试 41 → 47（新增 `tests/test_eval_contract.py` 6 个评测合同用例：
  数量矩阵逐格精确 / 输入 schema 与封存边界 / 语料引用可解析（含校验器 verdict=VALID 复核）/
  近重复与跨 split 零泄漏 / 保留集封存件 sha256(LF) 对账冻结记录 / 确定性基线两次运行逐字节一致）；
- STAGE-3 扫描范围 13 → 16 文件（新增 `evaluation/` 三件；评测件只输出 case_id/状态/文档标识，
  凭据与规格正文零出现）；
- 考卷本体：`eval-set-v1.jsonl` 60 例（36 开发 + 12 校准 + 12 保留封存），矩阵
  24/10/8/8/10 逐格精确，归一化 query 60/60 唯一；校验器 verdict=VALID；
- 基线探针实测（本仓库第 2 周引擎真实 ACL 语义，与参考模板 24/4 预设不同，如实记录）：
  answered 20 / needs_review 8（SPEC_CONFLICT 4：主管跨项目可见同零件双现行版本；
  NO_MATCHING_SPEC 4：pqe 对 OEM-B 专属件零命中）/ not_applicable 20 / sealed 12；
  两次运行逐字节一致；5 例角色改判记录见 `specs/mission-week-10.md` §三；
- 冻结记录 `evaluation-freeze-record-v1.md` 12 件 sha256(raw)+sha256(LF) 双口径登记，
  测试持续把守封存件；
- 工具链勘误（如实）：工作区根存在同名参考仓库，PowerShell .NET API 相对路径曾把哈希
  量到根仓库文件 —— 冻结哈希一律以 Python cwd 口径重算并复测（哈希振荡假象由此而来）。

## 7. Week 11 —— 检索基线比较（节点 ⑧，2026-10-10）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-004（冻结面零改动） | PASS |
| STAGE-2-unittest | 全量集成测试（…/**检索比较**） | PASS（**53 tests，OK = 47 + 检索 6**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**19 文件**，retrieval 三件入列） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门 | PASS（**4/4**） |

Week 11 变化点：

- STAGE-2 47 → 53（新增 `tests/test_retrieval_comparison.py` 6 用例：清单 sha256 互锁 /
  20 字段 schema + ACL 独立复算 / 确定性投影重跑 / holdout 零执行 / 失败登记对账 /
  回归种子 12 条复验）；
- 正式运行 48 例 × 2 检索器一次通过：Recall@5 双双 1.0（13/18-chunk 小池饱和值），
  rank-1 20/20；pqe 受限候选逐例 0；holdout 12 条零执行；cost_estimate_usd=0；
- 置信信号实测：vector top-1 有界（0.24–0.61）但 no_answer/conflict 与 answerable
  存在薄重叠（0.3712–0.3866，F11-04）；BM25 无域外信号（F11-03）；
- 裁定 `vector-hashed-ngram-v1` 单路晋级第 12 周（依据有界可对照信号，不靠饱和
  Recall@5），keyword 降级路径；类阈值不冻结，移交第 12 周保留集验证；
- 失败登记 6 条含两条 corpus 层（冲突对/过期件 0 chunk 是第 9 周设计，检索层不可见
  不可修——最诚实一条）；登记初稿 2 处与实况不符已对照 results 文件纠正。

## 8. Week 12-15 —— 只读 RAG 收官 + MCP 工具周 + 对抗（节点 ⑨-⑫，2026-10-10）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-004（冻结面零改动） | PASS |
| STAGE-2-unittest | 全量集成测试（…/**RAG 6 + 对抗 6**） | PASS（**75 tests**） |
| STAGE-3-redaction | `ci/redaction_scan.py`（**32 文件**，generation/mcptool 入列） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | 冷部署 + 冒烟门 | PASS（**4/4**） |

变化点：`generation/` 门控决策树（一轮受控标定后 60/60 状态门，L5=1.0、L6=28/28
单句逐字引用、L7=0 泄漏；保留集解封恰一次，同源自评污染披露照登）；`mcptool/`
只读 case.lookup_context@1（四级鉴权、不可区分错误封套）+ 受控写 ticket.create@1
（单一规范化/结构化审批/幂等重放先于用尽/ticket.get 独立核验）；7 攻击卡全部防御
成立（0 效果 0 泄漏），词面护栏失效面如实登记（A-08）。

## 9. Week 16-18 —— 可观测 + 降级 + 值守交接工作台（节点 ⑬-⑭，最终交付，2026-10-10）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-004（冻结面零改动） | PASS |
| STAGE-2-unittest | 全量集成测试（…/**遥测/降级 8 + 交接 6**） | PASS（**75 tests，OK**） |
| STAGE-3-redaction | `ci/redaction_scan.py`（**32 文件** / 50 输出点，workbench 入列） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | 冷部署 + 冒烟门 | PASS（**4/4**） |

变化点：遥测 TaskTrace 九阶段 + 终态分类 v1.1（action_preview 增补不改写六类）+
SLI 三口径独立（空窗分母 None 不编造）+ span 脱敏自检；金丝雀 60 例重考
behavior_drift=[] CONSISTENT；关闭开关封闭枚举 + 追加审计 + 无因拒绝；降级门
**权限判定优先于降级**（MCP_OFF+无审批=approval_denied；+有效审批=action_preview
0 效果审批不消耗，恢复后同一审批续跑）；D-01..04 演练 forbidden=0。
**最终交付物**: `workbench_handoff/` 8018 双角色交互工作台（业务用户: 问答带引用/
只读工具/受控写全流程/任务追踪；管理员: 开关/回滚/审计/金丝雀/重置；403 强制；
口令/session 零出现；真机验证首页 200 + 未登录 403）+ `start_workbench_handoff.bat`。
合并里程碑口径与逐周差异见 `specs/mission-week-12-18.md`。

## 诚实边界

CI 为本机单进程流水线（uvicorn 子进程 + 临时目录），不宣称分布式 CI/容器化/制品库推送；
`evidence/ci-run-w08.log` 的运行时刻戳随每次复跑追加，以 git 提交记录为准对照代码基线。

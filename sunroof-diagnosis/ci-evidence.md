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

### §9.1 r1 修订（2026-10-11）：工作台身份防混淆 + Docker 交付物

修复"打开 8018 显示课程模板 week-018 页面"问题（根因: 工作区根目录模板版同名
bat 同端口抢占 + 绑定失败静默退出）: `/healthz` 服务身份探针（零敏感信息）、
`SUNROOF_BIND/SUNROOF_PORT` 环境变量驱动绑定（容器 0.0.0.0）、绑定失败明确
`bind_failed` 提示、启动器防呆（8018 占用检测）、页面标注自研项目身份。
同时修复测试发现缺陷: `api()` 曾夹在类中间导致 test_03-05 从未被执行（解禁后
按第 14 周冻结核口径重写——写发起人恒为 pqe, 主管直写 = forbidden 0 效果）,
工作台取消角色自动签发审批。新增 Docker 交付物: `Dockerfile`（构建期跑全量
测试做质量门）/`.dockerignore`/`docker-compose.yml`/`deploy/ubuntu-docker-deploy.md`。

| 阶段 | 结果 |
| --- | --- |
| STAGE-1-schema | PASS |
| STAGE-2-unittest | PASS（**80 tests, OK**；75 → 80: handoff 2 → 7 真实入跑） |
| STAGE-3-redaction | PASS（32 文件 / 51 输出点 / violations=0） |
| STAGE-4-clean-env-smoke | PASS（4/4） |

证据: `evidence/ci-run-w18-r1.log`（CI RESULT OK, duration=31.3s）。
活体验证: healthz 身份探针 ✓ / 首页自研标识 ✓ / pqe 问答带引用 ✓ /
supervisor 直写 forbidden 0 效果 ✓。

## 诚实边界

CI 为本机单进程流水线（uvicorn 子进程 + 临时目录），不宣称分布式 CI/容器化/制品库推送；
`evidence/ci-run-w08.log` 的运行时刻戳随每次复跑追加，以 git 提交记录为准对照代码基线。

### §9.2 CI/CD 通道建立（2026-10-11）：GitHub Actions 双流水线

远程仓库 `https://github.com/LaoPi0169/sunroof-diagnosis.git`（main + 全部 9 个 sunroof tag 已同步）。
- CI: `.github/workflows/sunroof-ci.yml` —— 四阶段同一入口（py3.11 与镜像同版），证据 artifact 随 run 归档，
  触发面 = `sunroof-diagnosis/**` 的 push / PR / `sunroof-*` tag / 手动。
- CD: `.github/workflows/sunroof-docker-cd.yml` —— main push → `latest`+`sha-*` 镜像；`sunroof-*` tag → 同名版本镜像，
  发布至 `ghcr.io/laopi0169/sunroof-workbench`；构建期全量测试即质量门。
- 根目录课程遗留 `ci.yml`（week08 northstar）加 `paths-ignore: sunroof-diagnosis/**` 避免双通道重复触发。
- 部署文档对齐: §2 新增"方式 0: GHCR 预构建镜像"（三选一→四选一），§3 测试计数修正 76→80，新增 §11 通道说明。

### §9.3 远程四阶段首次全绿 + 根因3/4 收口（2026-10-12）

远程 CI run#7/run#8 证据（artifact `ci-github-run-*.log` 逐字节核读，详见提交记录）:

- **根因3（悬案收口）**: 冻结 `corpus-manifest-v1.json` 的 `file_path` 按 Windows
  作者机 os.sep 生成（反斜杠），Linux 全新重建为正斜杠，`test_06_manifest_binding`
  逐字段比对即败。修复落在**比较端**（分隔符归一化后再比；内容锚 document_id/
  title/来源 sha256/状态族仍逐字段精确绑定），冻结清单一个字节不动。
  实证: run#7 STAGE-2 **Ran 87 tests in 3.169s, exit=0** —— Linux 首次全绿。
- **冷启动自愈群（同轮拆除的潜伏雷，run#7 之前从未被远程执行过）**: `data/`
  不入版本库，全新检出无 `corpus-w09.db` —— `run_deterministic_baseline` /
  `run_rag_evaluation` / `run_retrieval_comparison` 三处 `build_corpus()` 无参调用
  是 TypeError 死分支（sunroof 版 db_path 必填，本地库文件常在从不触发），补实参;
  `mcptool/run_canary.py` 与 `tests/test_rag_generation.py` / `tests/test_retrieval_
  comparison.py` 直连语料库前先确定性重建（write_manifest=False，不触碰冻结清单），
  消除「sqlite 建空库文件毒化后续自愈」模式。验证: 删除 data/corpus-w09.db 模拟
  裸检出，22/22 测试自愈全绿。
- **根因4（run#7 唯一红点，STAGE-4）**: `_pick_archive_source` 先探测 "." 候选，
  而外层课程工作区在仓库根目录跟踪着模板项目**同名** `ci/run_local_ci.py` ——
  干净检出（无本机嵌套 relic .git 挡刀）上探针命中错误布局，整个工作区被归档成
  应用，config/corpus 全部错位（`apply_policy_config` exit=2 "config file not
  found"）。本地直跑侥幸全绿纯因 relic .git 使候选 1 探不中；仿真 clone 复现。
  修复: 候选改为**子目录优先**（`sunroof-diagnosis/ci/run_local_ci.py` 探中即本项目
  内容），"." 仅作独立 clone 布局兜底。双布局验证 4/4（仿真 clone + 本地直跑）。

| 阶段 | run#7（e4ae45b，根因3 已修/根因4 未修） | run#8（f9e36c4，根因4 修复） |
| --- | --- | --- |
| STAGE-1-schema | PASS | PASS |
| STAGE-2-unittest | PASS（**87 tests, 3.169s, Linux 首次全绿**） | PASS |
| STAGE-3-redaction | PASS（32 文件 / 51 输出点 / violations=0） | PASS |
| STAGE-4-clean-env-smoke | **FAIL**（apply_policy_config exit=2, 根因4） | PASS（4/4） |

远程全绿后 Dockerfile 构建期质量门 RUN 行恢复（§9.2 时期临时注释的解除），
CI + 镜像构建双质量门同时生效。

- **根因5（质量门恢复后 CD run#9/#10/#11 连续镜像内 1 失败的定位与收口）**:
  镜像内 `data/*.db` 被 .dockerignore 排除，冷启动自愈链若以默认
  `write_manifest=True` 调 `build_corpus`，会把 Linux 重建清单覆写到冻结
  `corpus-manifest-v1.json`（file_path 分隔符漂移 → sha256 失配，
  `test_01_manifest_interlock_and_protocol` 失败，82e16521≠fdb6d1a7）。
  CI runner 上用户上传的 data/corpus-w09.db 恰好存在使自愈分支从不触发、
  本地 Windows 重建内容与冻结版 LF 归一后一致，两地均未暴露；只有镜像
  （无 db + Linux 重建）同时满足全部触发条件。
  修复 5 处调用点（run_deterministic_baseline / run_rag_evaluation /
  run_retrieval_comparison / workbench_handoff get_conn+main——后两处为
  上传版本中的同类既有隐患）+ 质量门 RUN 行自身的冷建库命令，全部显式
  `write_manifest=False`；镜像等价条件（移走 corpus db）回归验证冻结清单
  raw sha256 逐字节不变。
- **终态**: CD run#12（cfbf939）镜像内 87 用例全绿，`latest` + `sha-*`
  发布 GHCR；CI 与镜像构建双质量门同时在线。
  教训: 自愈/重建类验证此前只断言"测试全绿"，未断言"冻结物逐字节未动"；
  自本轮起冻结物 raw sha256 前后对照为冷启动改动的标准回归检查。

## 10. 阶段 C 节点 ⑰ —— SN 自动抓取 · 只读 MES MCP 工具（2026-10-13）

discovery-brief-v1.md In-Scope 第 1 条的阶段 C 兑现 + 开放问题 R-2（波形分页/裁剪）
在工具 schema 冻结时收口。交付: `mes.lookup_production_record@1` 只读 MCP 工具
（`mcptool/mes_contract.py` 封闭合同 ↔ `api-contract/mes-lookup-v1.json` 同值互锁,
test_11 逐项断言; `mes_store.py` 确定性合成夹具 synthetic=true, 零写入口;
`mes_server.py` schema→RBAC→ACL→只读取数校验链）+ HTTP 面
`POST /api/v1/mes/production-record`（application 仅追加, 不改既有端点语义）
+ 控制台第四面板「MES 生产记录抓取」。

保护指标落地:
- **P-1**: 未知 SN / 跨项目 / 角色不足 三因的用户面封套逐字节一致
  （零存在性泄漏; 细分原因 reason_typed=unknown_sn/cross_project_denied/role_denied
  只进受限审计, 每条含 run_id, external_effects 恒 0）;
- **P-3**: 已知记录缺失字段返回字段级结构化 not_available
  （reason_code=FIELD_NOT_AVAILABLE）, 响应内零编造数值;
- **R-2 收口**: 波形 128 点 × 32 点/段 × 4 段冻结分页（segment/points_returned/
  truncated/next_segment）, 任何波形载荷永不超过单段上限, 不引入全量回传。

§9.3 教训的标准落地: `mes_store.store_fingerprint()`（静态记录 + 波形探针 sha256）
作为只读性质回归锚点 —— test_10 对 5 类调用路径（含失败路径）前后对照指纹逐字节
一致; 波形为纯整数确定性生成（mes-waveform-int-v1, 零随机零时钟零浮点）,
Linux CI / Windows 本地 / Docker 镜像三处同值。

本地四阶段 CI（节点 ⑰ 代码基线）:
- STAGE-1-schema PASS; **STAGE-2-unittest PASS（Ran 100 tests in 20.530s, exit=0
  —— 87 存量 + 13 新增）**; STAGE-3-redaction PASS（**36 文件** / 52 输出点 /
  violations=0）; STAGE-4-clean-env-smoke PASS（4/4）。
- 冻结面零改动: Week-13 冻结的 billing MCP 工具语义、冻结 manifest、既有端点
  语义全部不动（本节点仅追加工具/端点/面板/扫描范围 +4 文件）。
- 诚实边界: MES 为确定性合成夹具（零真实 MES 连接）; 凭证治理沿用教学模拟静态
  Token（R-4 口径不变, 上线前需按真实 SSO 重审权限矩阵）。


## 11. 阶段 C 节点 ⑱ —— PLM 工程记录查询 · 只读 PLM MCP 工具（2026-10-13）

discovery-brief-v1.md In-Scope 第 2 条的 PLM 半边兑现。交付只读 MCP 工具
`plm.lookup_engineering_record@1`: `mcptool/plm_contract.py` 封闭合同 →
`api-contract/plm-lookup-v1.json` 同值互锁（test_12 逐项断言, 改合同必须同步改码）;
`plm_store.py` 确定性合成 PLM 夹具 4 条记录（SR-FRAME-A01 六条 ECO / SR-GLASS-A01 /
SR-MOTOR-A02 / SR-RAIL-B01）synthetic=true 零写入口; `plm_server.py`
schema→RBAC→ACL→只读取数校验链; HTTP 面 `POST /api/v1/plm/engineering-record`
（application 仅追加, 不改既有端点语义）+ 控制台第五面板「PLM 工程记录查询」。

保护指标落地:
- **P-1 图纸硬门槛（矩阵 §2 跨 OEM 敏感图纸）**: drawing_registry 只回登记元数据
  （drawing_id/revision/title/status + server 组装 count）, content_disclosure=
  denied_by_policy; 图纸内容/文件/链接/base64 渲染数据永不出工具, 主管亦不展示
  （test_10 禁词扫描 + PQE/主管双角色断言）。
- **P-1 零存在性泄漏**: 跨项目（SR-RAIL-B01）/ 未知件号 / 角色不足三因用户面封套
  逐字节一致, 细分原因 reason_typed=cross_project_denied/unknown_part/role_denied
  只进受限审计, 每条带 run_id, external_effects 恒 0。
- **P-3**: 已知记录缺失字段返回字段级结构化 not_available
  （reason_code=FIELD_NOT_AVAILABLE, MOTOR-A02 无图纸登记）, 响应内零编造数值。
- **R-2 收口**: ECO 历史 6 条按 4 条/页冻结分页（eco_offset 0..12, total_changes/
  returned_changes/truncated/next_offset）, 任何 ECO 载荷永不超过单页上限,
  不引入全量回传（空窗合法）。

§9.3 教训的标准落地: `plm_store.store_fingerprint()`（静态记录 + ECO 指针 sha256）
作为只读性质回归锚点 —— test_11 对 5 类调用路径（含 3 类失败路径）前后对照指纹
逐字节一致; test_13 另断言同参数两次调用 result 逐字节一致（零随机零时钟）。

本地四阶段 CI（节点 ⑱ 代码基线）:
- STAGE-1-schema PASS; **STAGE-2-unittest PASS（Ran 113 tests in 21.374s, exit=0
  —— 100 存量 + 13 新增）**; STAGE-3-redaction PASS（**40 文件** / 53 出射点 /
  violations=0）; STAGE-4-clean-env-smoke PASS（4/4）。
- 新增 13 测试: 工具面 12（01 合法三字段 / 02 ECO 分页走查 / 03 P-1 封套逐字节一致 /
  04 主管跨项目只读 / 05 角色默认拒绝 / 06 身份夹带与上下文形状 / 07 封闭字段枚举 /
  08 件号冻结语法不归一化 / 09 P-3 结构化缺失 / 10 图纸硬门槛 / 11 只读指纹+审计分型 /
  12 合同互锁）+ HTTP 面确定性/身份矩阵/401 不落审计/422 封闭契约/控制台面板（13）。
- 冻结面零改动: Week-13 冻结的 billing MCP 工具语义、冻结 manifest、既有端点语义
  全部不动（本节点仅追加工具/端点/面板/扫描范围 +5 文件）。
- 诚实边界: PLM 为确定性合成夹具（零真实 PLM 连接）; 凭证治理沿用教学模拟静态
  Token（R-4 口径不变, 上线前需按真实 SSO 重审权限矩阵）。


## 12. 阶段 C 节点 ⑲ —— QMS 检验标准查询 · 只读 QMS MCP 工具（2026-10-13）

discovery-brief-v1.md In-Scope 第 2 条的 QMS 半边兑现（阶段 C 真实集成第三步）。
交付只读 MCP 工具 `qms.lookup_inspection_standard@1`: `mcptool/qms_contract.py`
封闭合同（part_no 语法 + 工序封闭枚举 torque_tightening/gap_check）→
`api-contract/qms-lookup-v1.json` 同值互锁（test_12 逐项断言）; `qms_store.py`
确定性合成 QMS 夹具 4 件号（现行 SOP/质量标准/检验要点 + 被替代行显式保留）
synthetic=true 零写入口; `qms_server.py` schema→RBAC→ACL→只读取数校验链;
HTTP 面 `POST /api/v1/qms/inspection-standard`（application 仅追加）+ 控制台
第六面板「QMS 检验标准查询」。

保护指标落地:
- **P-2 硬门槛（引用过期/已被替代 SOP = 0 次）**: store 中 is_current=0 的被替代
  行在取数层被排除（SQL WHERE is_current=1 语义）; 被替代版本标识（旧版本号/
  superseded_by/supersedes/is_current 治理键）永不出响应（test_02 禁词扫描）;
  仅存被替代行时字段级结构化拒引 reason_code=NO_CURRENT_SOP（SR-MOTOR-A02,
  test_10）; payload 携带 exclusion_policy=current_only 取数纪律标记。
- **P-1 零存在性泄漏**: 跨项目（SR-RAIL-B01）/ 未知件号 / 角色不足三因用户面
  封套逐字节一致, 细分原因只进受限审计, external_effects 恒 0。
- **P-3**: 字段行完全缺失 → FIELD_NOT_AVAILABLE（MOTOR-A02 无检验要点 /
  FRAME-A01 gap_check 工序无行）, 绝不编造。

本地四阶段 CI（节点 ⑲⑳ 代码基线）:
- STAGE-1-schema PASS; **STAGE-2-unittest PASS（Ran 139 tests in 23.982s, exit=0
  —— 113 存量 + 26 新增）**; STAGE-3-redaction PASS（**48 文件** / 54 出射点 /
  violations=0, 扫描范围 +8 文件）; STAGE-4-clean-env-smoke PASS（4/4）。
- 证据日志: `evidence/ci-run-w19-20.log`。


## 13. 阶段 C 节点 ⑳ —— 根因分析报告草稿 · 只读聚合 MCP 工具（2026-10-13）

discovery-brief-v1.md In-Scope 第 3 条兑现（阶段 C 收官）: 结构化、带出处引用的
《天窗总成异常根因分析报告草稿》。交付只读聚合 MCP 工具
`report.draft_root_cause@1`: `mcptool/report_contract.py` 封闭合同（sn/part_no/
process_step 三键, 与三源合同同值）→ `api-contract/report-draft-v1.json` 同值
互锁（test_11 逐项断言）; `mcptool/report_aggregate.py` 零状态聚合引擎
（服务端内部直调三源 server 层, 同一可信上下文透传; findings 带出处引用 /
gaps 结构化绝不编造 / conclusion 确定性规则）; `report_server.py` 校验链;
HTTP 面 `POST /api/v1/report/draft` + 控制台第七面板。

保护指标与纪律落地:
- **偏差 100% 确定性可复算**: measured vs 现行质量标准（mes_step 为 MES↔QMS
  确定性对接键）—— 公差带判定 conformance + 15% 硬限转人工
  （needs_review/SPEC_HARD_LIMIT_EXCEEDED, 与阶段 A spec-check 同值原因码;
  test_02: 9.62→+1.26% conforming / 10.98→+15.58% nonconforming→needs_review）。
- **P-2/P-3 贯通**: 无现行 SOP → gap + 结论 needs_review/NO_CURRENT_SOP;
  无对接标准的拧紧工序 → NO_STANDARD_FOR_STEP; 缺失字段 → 结构化 gap。
- **R-2 载料纪律**: 波形绝不嵌入报告载荷（OMITTED_BY_R2_PAGINATION_CONTRACT
  gap, 指向 MES 工具分页取数; test_09 断言响应零波形点列与载荷元数据键）。
- **草稿语义**: draft_ready = 「依据充分、可出具带引用草稿」, 不是「合格判定」
  （静态 disclaimer）; 零 LLM、零时钟、零随机（test_12 两次调用逐字节一致）。
- **只读性质**: 三源 store 指纹前后逐字节一致（test_10）; nested 审计三源分型
  （mes./qms./plm. 条目并入受限审计, 主条目 reason_typed=source_unavailable:源
  精确分型）。
- 阶段 C 收官: discovery-brief In-Scope 1/2/3 全部兑现（⑰ MES 抓取 + ⑱ PLM +
  ⑲ QMS + ⑳ 报告草稿）; 冻结面零改动（仅追加工具/端点/面板/扫描范围 +8 文件）。


## 14. 阶段 D 批次 1 —— 生产加固三件套（2026-10-14）

阶段 D 三批连做的第 1 批（`specs/production-hardening-brief-v1.md` D1 +
`adr-005-production-hardening-adaptations.md`），对应 decision-memo-02 §3 生产化
差距清单第 1/2/3 条：并发乐观锁（D1-a）+ 审计哈希链（D1-b）+ 签名短时令牌（D1-c）。

CI RUN 2026-10-14 | host=local | python=3.13 | 入口 `ci/run_local_ci.py --evidence-file evidence/ci-run-d1.log`

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 **001-005**（+`audit_chain_head` 单行只追加链头 + `revoked_tokens` jti 主键）+ 完整性 + 必需表 + **哈希链自洽校验（audit_chain_ok=True）** | PASS |
| STAGE-2-unittest | 全量集成测试（薄片/摄取/任务闭环/部署发布/语料治理/评测/检索/RAG/MCP/**生产加固**） | PASS（**156 tests，OK = 139 + 生产加固 17**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**49 文件** / 54 处输出语句 / 凭据+规格正文双规则；范围 +`application/token_service.py`） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门（节点 ⑤ 冻结语义不变） | PASS（**4/4**） |

批次 1 交付点（冻结 001-004 DDL / 既有端点语义 / 139 用例基线零改动，全部只增不改）：

- **D1-a 并发乐观锁**：`spec_check_tasks` 幂等 ALTER 追加 `version` 列；
  `update_task_state(expected_version=…)` 条件更新 + 版本自增；五个动作处理器全部
  携带读时版本，过期版本 → 结构化 `CONCURRENCY_CONFLICT`（任务终态不变 + 审计留痕，
  绝不盲重试）；附带 `journal_mode=WAL` + `busy_timeout=5000`（写并发等待而非立即 SQLITE_BUSY）。
- **D1-b 审计哈希链**：`task_action_runs` 追加 `prev_entry_hash`/`entry_hash`，
  每次审计写入 = 同事务「读链头 → sha256(prev_hash + "|" + canonical_json) → 追加行 →
  推进链头」；历史空链行启动一次性回填（只补哈希注记，业务字段零改动，幂等可重入）；
  `verify_audit_chain()` 重算哈希 + 前向链接 + 链头一致三重校验，篡改/删行/链头破坏
  均定位到首个坏 run_id。
- **D1-c 签名短时令牌**：`application/token_service.py` HMAC-SHA256
  `sr1.<payload>.<sig>`（sub/role/projects/iat/exp/jti），秘密 env
  `SUNROOF_TOKEN_SECRET` 缺省进程随机（零字面量硬编码）；`POST /api/v1/auth/token`
  静态凭证换签名令牌（未知用户/坏凭证/非法 TTL 同封套 401 零存在性泄漏）；
  `POST /api/v1/auth/revoke` 主管专属（非主管 403，INSERT OR IGNORE 幂等吊销）；
  `SUNROOF_REQUIRE_SIGNED_TOKENS=1` 严格模式退役静态令牌（默认关闭，教学回放零回归）。

- 新增 17 测试（`tests/test_production_hardening.py`）：迁移幂等 schema / 连接
  WAL+busy_timeout / 乐观锁三态（成功自增/冲突拒绝/缺省等价基线）/ 陈旧版本结构化冲突 /
  全状态写版本自增 / 哈希链全流量完整性 / 业务字段篡改定位 / 删行前向链接检测 /
  链头篡改检测 / 历史行幂等补链 / 签名令牌 E2E（身份卡 ACL 对称 + 主管跨项目只读）/
  过期 Default-Deny / 吊销生命周期（主管专属+幂等）/ 改签与畸形 / 签发面零存在性泄漏 /
  严格模式退役静态令牌 / 回归守卫（默认培训身份 + 未知静态令牌 401）。
- 诚实边界：`sr1.*` 为「生产形状」的教学实现（HMAC+过期+吊销），非真实 SSO/OIDC——
  按真实身份源重审权限矩阵保持开放（Brief §2 非目标 1）；哈希链为单机 SQLite
  防篡改注记，不外推分布式账本语义。
- 证据日志: `evidence/ci-run-d1.log`。


## 15. 阶段 D 批次 2 —— 三源真实适配器缝（2026-10-14）

阶段 D 第 2 批（`specs/production-hardening-brief-v1.md` D2-a/D2-b），对应遗留清单
第 3 类「合成实现层」：三源取数协议 + 适配器注册表（D2-a）+ HTTP 适配器骨架（D2-b）。

CI RUN 2026-10-14 | host=local | python=3.13 | 入口 `ci/run_local_ci.py --evidence-file evidence/ci-run-d2.log`

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-005 + 完整性 + 必需表 + 哈希链自洽校验 | PASS（migrations=5, audit_chain_ok=True） |
| STAGE-2-unittest | 全量集成测试 | PASS（**172 tests，OK = 156 + 适配器缝 16**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**50 文件** / 54 处输出语句；范围 +`mcptool/source_adapter.py`） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门 | PASS（**4/4**） |

批次 2 交付点（冻结 001-004 DDL / api-contract / 合同同值互锁 / 引擎判定表 /
既有 156 用例基线零改动）：

- **D2-a 取数协议 + 注册表**：`mcptool/source_adapter.py` 三源取数 `Protocol`
  （MesSource/QmsSource/PlmSource，与 mes/qms/plm server 对各自 store 的消费面
  逐一同形）+ 注册表（`resolve`/`register`/`reset_registry`，Protocol 结构校验，
  换装只追加 journal）；默认实现 `Synthetic*Source` 逐方法委托冻结合成夹具 ——
  行为逐字节等价；三个 server 取数改走注入缝（schema→RBAC→ACL 校验链不动，
  store 模块本身零改动）。
- **D2-b HTTP 适配器骨架**：httpx 传输 + 配置驱动（端点
  `SUNROOF_SOURCE_{MES,QMS,PLM}_URL` / 超时 `SUNROOF_SOURCE_TIMEOUT_S` /
  有限重试 `SUNROOF_SOURCE_MAX_RETRIES` 上限 5）；默认关闭（仅
  `SUNROOF_SOURCE_MODE=http` 且三源端点齐备才换装，缺一快速失败、零部分换装，
  绝不静默回退合成夹具）；传输语义 200 JSON 对象 | 404 诚实不存在 | 5xx/连接
  失败/超时有限重试后 `SourceUnavailableError` | 其余 4xx/非 JSON 立即拒 ——
  绝不编造、绝不回退合成数据；server 层捕获后沿用既有「源不可用结构化拒答」
  口径（用户面三因一致 `not_available` + 受限审计 `source_unavailable:<源>`
  精细分型，与 report_server 既有词表同值）。

- 新增 16 测试（`tests/test_source_adapter.py`）：注册表默认合成 + Protocol 校验 /
  经缝全消费面后三源 store 指纹逐字节一致（D2-a 验收）/ 合成委托逐字节等价 /
  stub 注入 server+聚合生效、reset 恢复、journal 留痕 / 换装校验零副作用 /
  默认模式审计路径不变（unknown_sn 分型）/ HTTP 默认关闭 / 缺端点快速失败零副作用 /
  http 三源换装 + 装配报告 / 配置校验（scheme/timeout/retries/未知源）/
  MockTransport 回放冻结夹具与 store 直调逐字节等价 / 有限重试（尝试次数 =
  1+max_retries）/ 不可达分型异常（5xx 耗尽 / 连接级 / 4xx 不重试 / 非 JSON 不编造）/
  单源结构化拒答 + 审计精确分型 / 聚合面整体拒答（绝不部分合成拼装）/
  week-17 降级演练语义一致（drills=4 全 pass forbidden=0）。
- 诚实边界：HTTP 适配器是「缝」而非「集成」—— 真实 MES/QMS/PLM 接入需按其
  真实 API 契约另做载荷映射与行级过滤（骨架假定载荷与合成夹具同键结构，行级
  变换镜像 store 语义，零随机零时钟）；discovery-brief Out-of-Scope 系统级禁止
  不因本缝解禁。
- 证据日志: `evidence/ci-run-d2.log`。


## 15.1 说明

（§15 编号延续批次 2 记录；批次 3 为 §16。）


## 16. 阶段 D 批次 3 —— 生产发布包构建（2026-10-14）

阶段 D 三批连做的第 3 批（`specs/production-hardening-brief-v1.md` D3）：
非 root 生产化镜像（D3-a）+ 发布清单 v2 六类绑定 schema 005（D3-b）+
部署 runbook v2 + 备份/恢复脚本含审计哈希链快照一致性门（D3-c）。
Docker 构建/运行验证按既定约束不在 Windows 本机执行，运行验证依托远端
`sunroof-docker-cd`（GHCR 镜像构建期自带全量测试门，以非 root 身份执行）。

CI RUN 2026-10-14 | host=local | python=3.13 | 入口 `ci/run_local_ci.py --evidence-file evidence/ci-run-d3.log`

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 **001-005** + 完整性 + 必需表 + **哈希链自洽校验（audit_chain_ok=True）** | PASS |
| STAGE-2-unittest | 全量集成测试（薄片/摄取/任务闭环/部署发布/语料治理/评测/检索/RAG/MCP/生产加固/三源适配器缝/**发布打包**） | PASS（**183 tests，OK = 172 + 发布打包 11**） |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描（**52 文件**；范围 +`deploy/backup_snapshot.py` +`deploy/restore_snapshot.py`） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门（节点 ⑤ 冻结语义不变） | PASS（**4/4**） |

批次 3 交付点（冻结 001-005 DDL / api-contract / 引擎判定表 / 既有 172 用例语义
零改动，全部只增不改）：

- **D3-a Dockerfile 生产化**：专用系统用户 `sunroof`（uid/gid 10001，无登录 shell）
  + `chown /app` + `USER sunroof`；**构建期全量测试门以非 root 身份执行**（镜像内
  权限配错 = 测试失败 = 构建失败，自证非 root 可运行）；HEALTHCHECK 与构建期
  质量门保留；OCI labels。`docker-compose.yml` 生产形态：追加
  `SUNROOF_TOKEN_SECRET` 环境变量注入（留空 = 进程随机，零字面量硬编码），
  数据卷持久化与 restart/healthcheck 保留。
- **D3-b `release-manifest-v2.json`**：六类绑定（代码基线 commit `9981bc8…` /
  config sha256 与 v1 同值零漂移 / **schema 005** / 冻结合同清单 = 5 份
  `api-contract/*.json` 逐份 sha256 / 冒烟门 / CI 入口）+ `backup_tool`/
  `restore_tool`/`image_hardening` 交付引用；测试断言全部 sha256 与文件实算
  逐字节一致且清单零秘密字面量。
- **D3-c 备份/恢复 + runbook v2**：`deploy/backup_snapshot.py`（SQLite backup API
  在线一致快照，WAL 安全不撕裂；快照后 `PRAGMA integrity_check` +
  `verify_audit_chain()` 三重校验，任一不过 → 回滚删除快照、退出码 1，
  绝不产出坏备份；元数据 JSON 含 `db_sha256`/链结果/表清单；`--verify-only`
  运维校验模式）；`deploy/restore_snapshot.py`（fail-fast 恢复前校验：完整性 +
  哈希链 + 元数据 sha256 逐字节比对 → 目标库安全快照 `.pre-restore-<ts>.db` →
  原子 `os.replace` + WAL 侧车清理 → 恢复后复验；被篡改备份恢复拒绝且目标库
  逐字节零触碰）；`deploy/runbook-v2.md`（非 root 验机 / 备份 / 恢复 / 运维期
  哈希链校验 / 回滚锚点联动 ROLLBACK.md）。
- 新增 11 测试（`tests/test_release_packaging_d3.py`）：manifest 六类绑定 +
  零秘密 / config sha 实算（含 v1 零漂移） / 冻结合同 sha 实算 / schema 005 引用 /
  Dockerfile 非 root 静态断言（uid 10001 + USER 非 root + 测试门在 USER 后）/
  compose 生产形态断言 / 备份快照链门+元数据 / 篡改快照 verify-only 拒绝 /
  篡改备份恢复 fail-fast 目标零触碰 / 正常恢复逐字节一致+安全快照留存 /
  扫描范围 +2。
- 用户既存决策项维持不动（`.gitignore` 损坏编辑 / 再生基线文件 / relic 内层
  `.git` / 未跟踪 `evidence/ci-run-w16-fix.log`）；提交显式逐文件 add。
- 证据日志: `evidence/ci-run-d3.log`。

## 阶段 E 批次 1 —— 评测器 v2（判定层补全 + fall-through 修正语义；方案甲）

CI RUN 2026-10-14 | host=local | python=3.13 | 入口 `ci/run_local_ci.py --evidence-file evidence/ci-run-e1.log`

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| STAGE-1-schema | 幂等迁移 001-005 + 完整性 + 哈希链自洽（audit_chain_ok=True） | PASS |
| STAGE-2-unittest | 全量集成测试（…/发布打包/**评测器 v2**） | PASS（**194 tests，OK = 183 + 评测器 v2 11**） |
| STAGE-3-redaction | 静态扫描（**53 文件**；范围 +`evaluation/evaluator_v2.py`，emissions 67） | PASS（violations=0） |
| STAGE-4-clean-env-smoke | `git archive HEAD` 冷部署 + 冒烟门 | PASS（**4/4**） |

批次 1 交付点（用户拍板：方案甲 / 两批连做 / 完工后 web 工作台+手册——手册随 E3 交付）：

- **v1 判定层缺陷登记（本轮新发现，如实呈报）**：`generation/run_rag_evaluation.py`
  L75/L81 `rec["match"] = rec["expected_task_state"] == rec["expected_task_state"]`
  为自比较恒真 → v1 落盘 `state_gate_match 60/60` 属判定层虚高（真实字面对齐 28/60，
  其中 31 例为「同族安全行为」差异）。v1 产物文件零改动（测试以内容 hash 前后一致
  证明）；v1 只读对账路径的 dev+cal 复算（expected vs 公开标签）语义正确，不受此
  缺陷影响。
- **状态门 v2 预注册映射**（移植上级 `evaluate.py STATE_MAPPING` 的词汇与先例）：
  `escalate≡escalated` 词形归一；`abstained+conflict_gate≡needs_review`（候选冲突
  路径已做「登记冲突+转质量主管」，`generation/contract.py` 冲突分支 user_next_step
  实证，上级 conflicting_current_sources 同款「上报即复核」语义）；其余 abstained≡
  refused（同为「不给出内容」安全归宿；labeling-guide 规则 2/4）；未登记组合 =
  unmapped 绝不折叠成 match。真判定 **58/60**，miss = {SR-HOLD-07（安全侧停住未转
  冲突复核，与上级 NS-HOLD-08 同型先例一致）, SR-HOLD-12（跨族漏答）}，与上级金丝雀
  基线 58/60 交叉印证（两套考卷独立标注、同构结论）。
- **事实层补全**（v1 无事实规则引擎，required/forbidden 落盘但从未逐条判定）：8 条
  预注册规则（负向指令/无来源说明/冲突登记/升级路径/版本归属/数字锚点/工单留痕/
  引用保真转介）对 81 条 required_facts 逐条判定 **67/81 hit**；14 条 miss 为如实的
  覆盖缺口呈现（候选统一拒答话术未含现行依据指引 ×6、SR-HOLD-07 冲突误归因 ×2、
  SR-DEV-11 合并工时口径未覆盖 ×1 等——v1 从未呈现的维度），绝不因「拒答得体」
  虚判 hit（SR-HOLD-06 的两条 required facts 由 no_current_source 真实判定命中，
  与上级 NS-HOLD-06 行为 miss 形成教学对照）。forbidden doc_guard/structural
  两分法 **0 违规**。
- **上级 fall-through 继承修正（归档注记）**：三条修正语义（①专属触发即终局——触发
  判否即 miss，绝不向后扫描兜底；②空真修正——无版本锚点不产生判定，数字锚点剥离
  版本号；③判定矩阵留痕——全部触发规则及判定逐条记录）在 `evaluation/evaluator_v2.py`
  机械实现并以 NS-HOLD-06 型等价构造测试证明；上级 `generation/evaluate.py` 原件
  冻结不动（登记既定：修复留 v2，落点 = 子仓库，方案甲）。
- **确定性守卫**：`week-12/rag-per-case-factcheck-v2.jsonl`（61 行 = 摘要 + 60 逐例）
  重算逐字节一致；`--write` 对已存在产物只读对账、不一致即拒绝（评测器为纯函数，
  零候选重跑、零模型调用）。
- 新增 11 测试（`tests/test_evaluator_v2.py`）：修正语义①②③ / unmapped 诚实 /
  状态门 58/60 / v1 缺陷登记 + v1 零改动 / 事实层诚实（DEV-11 数字锚点 miss 如实、
  HOLD-06 真实奖励非虚判）/ 确定性与写守卫 / 输出结构完备 / forbidden 两分法 ×2；
  扫描范围 +1（53 文件）。
- 证据日志: `evidence/ci-run-e1.log`。


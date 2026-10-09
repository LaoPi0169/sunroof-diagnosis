# Week 08 CI 证据 (ci-evidence)

- **日期**: 2026-09-30
- **Build ID**: `build-fde-w08-20260930-01`
- **流水线入口**: `ci/run_local_ci.py`（本地）= `.github/workflows/ci.yml`（远端），同一命令同一阶段，本地复跑即 CI 复跑。
- **原始日志**: `evidence/ci-run-w08.log`（CI 运行器自动追加，含全部三轮运行记录）

## 一、流水线设计（4 阶段）

| 阶段 | 内容 | 对应验收标准 |
| --- | --- | --- |
| STAGE-1-schema | 临时库幂等迁移（001→002→003）+ `PRAGMA integrity_check` + 必需表存在性 | 发布物结构可信 |
| STAGE-2-unittest | `unittest discover` 全量单测/集成（摄取幂等、RBAC、E2E 任务闭环） | 回归不退化 |
| STAGE-3-redaction | `ci/redaction_scan.py` 静态扫描：凭据/政策正文不入运行时输出 | 敏感信息不入日志 |
| STAGE-4-clean-env-smoke | `git archive HEAD` → 全新目录 → `uvicorn` 独立实例（全新 DB + 显式环境变量）→ 应用发布配置 → `deploy/smoke_w08.py` 4 条任务冒烟（三向对账） | 干净环境可部署 + 冒烟看任务终态 |

CI 触发条件：`push`（main/week08）、`pull_request`、`workflow_dispatch`；Python 3.12；证据日志作为 artifact 上传。

## 二、运行证据（真实三轮）

### Run 1（首次）：3 PASS / 1 FAIL —— 扫描器抓到自己

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 20 tests in 3.867s, exit=0  (OK)
[FAIL] STAGE-3-redaction :: violations 含 ci/redaction_scan.py 自身凭据字面量（扫描器定义清单被自扫描）
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=54503; smokes_passed=4/4
```
处置：`ci/redaction_scan.py` 加入凭据常量授权名单（扫描器必须持有字面量清单才能检测违规）。

### Run 2：3 PASS / 1 FAIL —— 扫描器抓到一个真实违规

```
[PASS] STAGE-1-schema / STAGE-2-unittest / STAGE-4-clean-env-smoke
[FAIL] STAGE-3-redaction :: workbench_cli.py:88 rule=policy_body_in_emission
       "输出语句包含敏感标记: form***"  (CLI 打印 candidate_policy.formula_rule)
```
处置（策略裁决，非代码修复）：`workbench_cli.py` 是**客户端工作台**，把操作者本人已授权响应中的
`candidate_policy` 展示给本人属「授权展示」而非日志泄漏（Week 07 SOP 人工核验 UX 的既定设计，
服务端从不主动记录正文，`task_action_runs` 审计仅存 policy_id）。据此在扫描器中落地两条规则：
1. **凭据类**（token 明文 / X-Auth-Token）：任何文件的运行时输出语句全范围禁止；
2. **政策正文类**（标题/公式字段名）：服务端与工具链（application/*、deploy/*、ci/*）禁止；
   仅 `workbench_cli.py` 客户端授权展示豁免。
该裁决的完整论证见 Day 5 文档 `basic-observability-note-v1.md`。

### Run 3（工具链终态）：4/4 PASS

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 20 tests in 2.921s, exit=0  (OK)
[PASS] STAGE-3-redaction :: scanned_files=11, emissions_checked=39, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=63939; smokes_passed=4/4
CI RESULT OK
```

### Run 4（Week 08 验收终态）：4/4 PASS，测试规模 20→29

部署/发布自动化用例（`tests/test_deployment_release.py`，9 个）合入后的完整验收：

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 29 tests in 6.339s, exit=0  (OK)
[PASS] STAGE-3-redaction :: scanned_files=11, emissions_checked=39, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=58958; smokes_passed=4/4
CI RESULT OK
```

覆盖：manifest 全绑定 / 收敛器幂等与 fail-fast / git archive 干净环境部署 / 四条任务冒烟三向对账 /
动态脱敏（响应+审计+服务端日志）/ 注入→拦截→回滚→全新库复验 4/4 / 文档一致性 / CI 定义 / 静态扫描回归。

## 三、结论

- 本地 CI 流水线 4/4 阶段通过，退出码 0；干净环境真实部署 + 任务冒烟在 Run 1 即一次通过（4/4）。
- 脱敏扫描在两轮运行中分别捕获自指问题与真实违规，证明扫描规则真实生效而非摆设。
- 原始证据（含失败轮次）完整保留于 `evidence/ci-run-w08.log`，不删失败记录 —— 失败-修复链本身就是证据。

## 四、Week 09 运行记录（RAG 语料治理，回链 specs/mission-week-09.md）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w09.log`）：

```
CI RUN 2026-10-07T07:33:20+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 35 tests in 7.651s, exit=0  (OK = 29 + 语料治理 6)
[PASS] STAGE-3-redaction :: scanned_files=13, emissions_checked=42, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=64497; smokes_passed=4/4
CI RESULT OK | duration=11.4s
```

Week 09 变化点：

- STAGE-2 全量测试 29 → 35（新增 `tests/test_corpus_governance.py` 6 个语料治理用例：
  两次构建确定性 / 缺要件默认隔离 / supersedes 谱系过期 / 冲突对保留 / 角色候选零泄漏
  （精确候选集合断言，含 `billing-general-v3#s06` 排除）/ 清单绑定全新重建）。
- STAGE-3 扫描范围 11 → 13 文件：语料工具链 `corpus/build_corpus.py`、
  `corpus/check_role_candidates.py` 纳入运行时输出脱敏扫描。
- STAGE-1 冻结面不变：`migrations=3`（语料两表 schema 独立存放于 `corpus/corpus_schema.sql`，
  不触碰 `release-manifest-v1.json` 的 schema_version=003 绑定语义）。
- 语料层证据：`corpus-manifest-v1.json`（整体 sha256 `135f773cbd4aeb55…`，与全新重建逐字节一致）；
  两次独立构建清单逐字节相同；三档角色原始候选 support_agent 13 / supervisor 18 / 未知 0，
  受限内容零泄漏（详细证据见 `role-candidate-check-v1.md` 与 `ingestion-isolation-report-v1.md`）。

## 5. 第 10 周 CI 证据（评测合同冻结 + 脱敏扩容 13 → 16 文件）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w10.log`）：

```
CI RUN 2026-10-07T09:05:00+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 41 tests in 21.630s, exit=0  (OK = 35 + 评测合同 6)
[PASS] STAGE-3-redaction :: scanned_files=16, emissions_checked=44, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=59402; smokes_passed=4/4
CI RESULT OK
```

Week 10 变化点：

- STAGE-2 全量测试 35 → 41（新增 `tests/test_eval_contract.py` 6 个评测合同用例：
  60 行矩阵逐格精确（36/12/12 × 24/10/8/8/10）/ 输入 schema 与封存边界（输入行零标签字段）/
  语料引用可解析（gold/forbidden 全部回到第 9 周快照清单）/ 近重复零跨 split 泄漏
  （归一化 query 60/60 唯一）/ 封存标签 sha256 对齐冻结记录 / 确定性基线两次运行逐字节一致
  （sealed 12 / not_applicable 20 / answered 24 / needs_review 4））。
- STAGE-3 扫描范围 13 → 16 文件：评测工具链 `evaluation/eval_contract.py`、
  `evaluation/validate_eval_set.py`、`evaluation/run_deterministic_baseline.py` 纳入运行时
  输出脱敏扫描（扫描器清单同步扩容；44 个输出点零违规）。
- 零模型调用声明：本周新增代码路径不含任何模型 API 调用；基线探针 100% 复用第 8 周
  确定性政策引擎与第 9 周 ACL 候选 SQL（先过滤后候选语义不变）。
- 评测层证据：冻结记录 `evaluation-freeze-record-v1.md` 登记 12 件冻结物 sha256，
  程序化核对 12/12 一致（checked=12, bad=0）；保留集标签仅存封存件
  （第 12 周 manifest 冻结后解封一次）；判题边界与开除线见 `eval-plan-v1.md` §5–6。

## 6. 第 11 周 CI 证据（检索比较：BM25 vs 哈希向量，16 → 19 文件）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w11.log`）：

```
CI RUN 2026-10-08T00:43:05+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 47 tests in 22.020s, exit=0  (OK = 41 + 检索比较合同 6)
[PASS] STAGE-3-redaction :: scanned_files=19, emissions_checked=45, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=49888; smokes_passed=4/4
CI RESULT OK | duration=26.0s
```

Week 11 变化点：

- STAGE-2 全量测试 41 → 47（新增 `tests/test_retrieval_comparison.py` 6 个用例：
  比较清单 sha256 互锁与协议字段对齐 / 统一合同 20 字段 schema + ACL 允许池逐 chunk
  独立复算与 agent 受限零泄漏 / 确定性重跑（投影排除 latency_ms，两次运行与落盘文件
  三方一致）/ 保留集零执行 / 失败登记五层归因与结果文件对账 / 12 条回归种子行为锁定）。
- STAGE-3 扫描范围 16 → 19 文件：检索工具链 `retrieval/contract.py`、`retrieval/labels.py`、
  `retrieval/run_retrieval_comparison.py` 纳入运行时输出脱敏扫描（45 个输出点零违规）。
- 零模型调用声明：本周新增代码路径（双检索器 + 标签装载器 + 比较运行器）不含任何
  模型 API 调用、零业务效果（cost_estimate_usd=0）；候选池语义 100% 复用第 9 周
  ACL 下推 SQL（先过滤后候选语义不变）。
- 检索层证据：比较清单 `week-11/retrieval-comparison-manifest-v1.json` 三输入件 sha256
  绑定（先冻结后运行）；48 例 × 2 检索器逐例落盘（`week-11/keyword-results-v1.jsonl` /
  `week-11/vector-results-v1.jsonl`）；保留集 12 条零执行（结果文件与 manifest 双声明）。
- 第 10 周冻结物程序化复核：12 件中 11 件与工作区字节一致；`deterministic-baseline-results-v1.jsonl`
  与提交 blob 一致（工作区行尾被全量测试重写，内容逐字节同）；`specs/mission-week-10.md`
  原哈希系记录核对后追加落地记录前的旧值，已按勘误更正（详见冻结记录勘误节）。

## 7. 第 12 周 CI 证据（只读 RAG 评测：三态回答 + 一次解封，19 → 22 文件）

最终验收运行（Run 1 一次通过，原始日志 `evidence/ci-run-w12.log`）：

```
CI RUN 2026-10-08T02:20:17+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 56 tests in 22.786s, exit=0  (OK = 47 + RAG 评测合同 9)
[PASS] STAGE-3-redaction :: scanned_files=22, emissions_checked=46, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=64280; smokes_passed=4/4
CI RESULT OK | duration=25.0s
```

Week 12 变化点：

- STAGE-2 全量测试 47 → 56（新增 `tests/test_rag_generation.py` 9 个用例：manifest
  冻结门与解封守卫 / 三态回答合同 schema 与映射完备 / ACL 零泄漏独立复算 /
  dev+cal 48 例状态门冻结回归 / 双遍确定性 + 词表互锁 / 引用支持逐条验证 /
  七层指标复算与规则引擎正例自检 / 保留集机制对账（L4=0.9 硬门失败如实锁定）/
  13 条回归种子逐字段对账）。
- STAGE-3 扫描范围 19 → 22 文件：`generation/contract.py`、`generation/evaluate.py`、
  `generation/run_rag_evaluation.py` 纳入（46 个输出点零违规）。**范围声明**：
  第 12 周评测工件 `week-12/rag-per-case-results-v1.jsonl` 按评测合同必须包含
  抽取式答案正文（引用即正文，来自冻结语料 DB 动态查询）；"政策正文不入运行时
  输出"检查保持面向服务端/工具链源码字面量——generation 源码经扫描确认不含
  正文硬编码常量与任何凭据 marker。
- 评测层证据：`week-12/rag-system-manifest-v1.json` 解封前冻结（四输入件 sha256
  绑定 + 阈值/词表/映射/规则预注册）；`week-12/rag-per-case-results-v1.jsonl`
  60 行一次解封运行落盘（unseal-once 守卫：文件存在即拒绝重写，测试 01 覆盖）。
- 保留集结果如实呈现：L4=0.9（NS-HOLD-06 自信作答）为唯一硬门失败，已锁进
  test_08 对账与失败登记 F12-01；解封结果未用于调参。
- 第 10 周冻结物与第 11 周交付物未改动（56 测试含既有冻结对账用例全绿；
  封存件 sha256 与 manifest 绑定一致）。

## 八、Week 13 CI 运行证据（2026-10-08）

```
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 66 tests in 27.948s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=29, emissions_checked=51, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=52071; smokes_passed=4/4
```

证据日志：`evidence/ci-run-w13.log`（本次运行经 `--evidence-file` 指定，未追加污染 w08 日志）。

Week 13 变化点：

- STAGE-2 全量测试 56 → 66（新增 `tests/test_mcp_read_tool.py` 10 个用例：合同/
  能力清单/常量三方互锁含合同 sha256 / R-01 正常路径恰好 3 字段 / 禁止与未知字段
  schema 拒绝 / R-03 无权限响应不可区分 / R-04 超时 / R-05 超合同结果整体拒绝 /
  X-01 身份走私与 X-03 版本不匹配 / 零写效果与工单库快照不可变 / 13 种子工具在环
  回归 / 受限审计结构与运行时输出脱敏）。
- STAGE-3 扫描范围 22 → 29 文件：`mcptool/` 7 个运行时文件（contract/case_store/
  server/client/run_fixtures/run_regression/demo_lookup）全部纳入，51 个输出点
  零违规（凭据与政策正文标记零出现；与第 12 周同口径）。
- 工具层证据：`week-13/mcp-read-contract-v1.json` 先冻结（sha256 `882ccd8b…70dbe2`，
  能力清单绑定 + 单测三方互锁）；夹具 R-01..R-05 + X-01..X-03 共 8/8 通过，
  外部写效果全 0，工单库快照运行前后逐字节一致；13 条回归种子工具在环对账
  13/13 与第 12 周逐字段一致（`python -m mcptool.run_regression`）。
- 第 12 周冻结物与评测工件零改动（66 测试含既有冻结对账用例全绿；全量测试后
  `deterministic-baseline-results-v1.jsonl` 行尾搅动按既有口径 `git restore`，
  未入库）。

## 九、Week 14 CI 运行证据（2026-10-08，审批写工具）

```
CI RUN 2026-10-08T08:22:24+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 78 tests in 25.364s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=34, emissions_checked=53, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=61425; smokes_passed=4/4
CI RESULT OK | duration=27.9s
```

证据日志：`evidence/ci-run-w14.log`（本次运行经 `--evidence-file` 指定，未追加污染 w08 日志）。

Week 14 变化点：

- STAGE-2 全量测试 66 → 78（新增 `tests/test_mcp_write_tool.py` 12 个用例：合同/
  常量/策略三方互锁含写合同 sha256 / 无副作用预览与失控预览计数硬门 / 写参数
  走私与封闭 schema 十例拒绝 / W-01 无审批 / W-02 过期 / W-03 绑定不匹配与规范化
  等价 / W-04 幂等重放十次 / W-05 拒绝终态与策略拒绝 / 用尽审批与完成封套核验 /
  ticket.get 独立核验与不可区分拒绝 / 写读夹具与 13 种子回归总对账 / 脱敏与受限审计）。
  第 13 周 `test_08` 的「无写入口」设计断言按新边界演进：case_store 保持零写入口，
  server 模块合法拥有带审批门的写入口（preview_create/create/get_ticket），
  写路径效果硬门改由 test_mcp_write_tool.py 独立覆盖。
- STAGE-3 扫描范围 29 → 34 文件：`mcptool/` 写路径 5 个运行时文件
  （write_contract/ticket_service/approval/policy/demo_write_approval）纳入，
  53 个输出点零违规（凭据与政策正文标记零出现；与第 12/13 周同口径）。
  **批准载荷边界**：`approved_parameters` 属批准载荷，只存审批记录（restricted），
  不入普通日志 —— 用户面封套与夹具输出经 test_12 断言不含该键。
- 工具层证据：`week-14/ticket-write-contract-v1.json` 先冻结（sha256
  `486b9362…b1b9b4`，与 `write_contract.CONTRACT_SHA256` 常量互锁）；写负向夹具
  W-01..W-05 共 5/5 通过，新增外部效果合计恰 1（W-04 成功路径），拒绝/过期/不匹配/
  无审批路径全 0；只读夹具 8/8 不受扰，只读工单库快照逐字节一致；13 条回归种子
  工具在环 13/13 与第 12 周逐字段一致。
- 第 12/13 周冻结物零改动（78 测试含既有冻结对账用例全绿；全量与 CI 各触发一次
  baseline jsonl 行尾搅动，均按既有口径 `git restore`，未入库）。

## 十、Week 14 增补 CI 运行证据（2026-10-08，业务用户交互工作台）

```
CI RUN 2026-10-08T12:44:40+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 82 tests in 26.166s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=36, emissions_checked=53, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=55820; smokes_passed=4/4
CI RESULT OK | duration=28.5s
```

证据日志：`evidence/ci-run-w14-workbench.log`（新增独立文件，未追加 w14/w08 历史）。

工作台增补变化点（交付物见 README「第 14 周业务用户交互工作台」小节）：

- STAGE-2 全量测试 78 → 82（新增 `tests/test_write_workbench.py` 4 个 API 用例：
  重置+预览规范化零效果 / 拒绝后执行 approval_required / 批准后改队列
  approval_mismatch 且额度不消耗 / 匹配执行+幂等重放+核验效果恰 1；与
  `run_fixtures` W-01..W-05 用户可见子集一一对应）。
- STAGE-3 扫描范围 34 → 36 文件：`workbench_write/__init__.py` 与
  `workbench_write/app.py` 入列；53 个输出点零违规（工作台薄壳不打印凭据/
  政策正文；审批记录与审计链仅在页面 restricted 面板回显给本人，不写日志文件）。
- `workbench_write/` 是 MCP Host 之上的薄 HTTP 壳：不实现新业务规则、不新增
  写入口；页面流程（预览 → 拒绝/批准 → 执行/改参/重放 → 核验）与四次尝试
  对账 0→1→1→1 同构；时钟用真实 UTC（TTL 900s 真实生效，过期路径由
  W-02 固定时钟自动化覆盖，页面可实测）。
- 一致性门复验：第 8 周动态发现测试数（82）已同步进 README；baseline jsonl
  行尾搅动本轮再触发一次，`git restore` 未入库。

## 十一、Week 15 CI 运行证据（2026-10-09，攻击·撤销·恢复）

```
CI RUN 2026-10-09T02:05:20+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 94 tests in 25.141s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=39, emissions_checked=53, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=63017; smokes_passed=4/4
CI RESULT OK | duration=27.7s
```

证据日志：`evidence/ci-run-w15.log`（新增独立文件，未追加 w14/w14-workbench/w08 历史）。

Week 15 变化点（交付物见 README「第 15 周：攻击、撤销并恢复受控动作」小节）：

- STAGE-2 全量测试 82 → 94（新增 `tests/test_mcp_adversarial.py` 12 个用例：
  合同 v2/能力清单 v2 sha256 互锁 + week-14 v1 冻结物一字节不动 / A-01 恶意文档
  文本只是数据 / A-02 工具描述自称授权 → capability_quarantined 隔离且
  server_calls=0、审批额度不消耗 / A-03 参数走私止于 Host 层 / A-04 执行前撤销
  → approval_revoked / A-05 未评审 @2 版本隔离不自动升级 + notify_customer 走私
  schema 拒绝 / A-06 断连 result_unknown → 先查后说 → action_completed 恰 1 效果
  + 事件链 created→response_lost→reconciled / 查询不可用停 reconciliation_pending
  禁止伪造终态 + 创建 fail-closed 拒绝 / A-07 补偿仅 ops_admin 且 append-only /
  工作台七张攻击卡端到端 + 手动撤销/断连/取消角色矩阵 / 脱敏与 manifest fail-closed）。
- STAGE-3 扫描范围 36 → 39 文件：`mcptool/capability_manifest.py` 与
  `workbench_adversarial/__init__.py`、`workbench_adversarial/app.py` 入列；
  53 个输出点零违规（凭据与政策正文零出现；与第 12-14 周同口径）。
  **restricted 载荷边界**：批准载荷（approved_parameters）与撤销元数据
  （revoked_by/revoked_at/revoke_reason）只存审批记录（restricted 面板回显本人），
  用户面封套与攻击卡步骤叙述经 test_11 断言不含这些键。
- 工具层证据：`week-15/ticket-write-contract-v2.json` 先冻结（sha256
  `f9afabdc…e931f4`，与 `write_contract.CONTRACT_SHA256` 常量互锁；错误八类 →
  十类）；`week-15/mcp-capability-manifest-v2.json` 先冻结（sha256 `136efe01…a8a918`，
  与 `capability_manifest.MANIFEST_SHA256` 互锁，清单被改 → 装载即失败）。
  A-01..A-05/A-07 攻击面外部效果恒 0；A-06 断连先查后说恢复，外部工单总数恰 1；
  撤销/隔离/对账细分原因只进受限审计。
- 第 12/13/14 周冻结物零改动：week-14 v1 合同 sha256 `486b9362…b1b9b4` 由
  test_01 锁定；只读库 16 工单快照、13 种子回归、RAG 冻结物均不受扰
  （94 测试含全部既有冻结对账用例全绿；全量与 CI 各触发一次 baseline jsonl
  行尾搅动，均按既有口径 `git restore`，未入库）。

## 十二、Week 16 CI 运行证据（2026-10-09，离线金丝雀 × 运行期 SLI 可观测性）

```
CI RUN 2026-10-09T05:28:08+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 106 tests in 29.384s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=44, emissions_checked=55, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=50113; smokes_passed=4/4
CI RESULT OK | duration=31.8s
```

证据日志：`evidence/ci-run-w16.log`（新增独立文件，未追加 w15/w14/w08 历史）。

Week 16 变化点（交付物见 README「第 16 周：离线质量金丝雀与运行期 SLI」小节）：

- STAGE-2 全量测试 94 → 106（新增 `tests/test_telemetry_observability.py`
  12 个用例：九阶段任务追踪 + 两断点关联 / 终态分类对照表 18 组 /
  金丝雀复现冻结基线 58/60 + 行为零漂移 + 失败指路 F12-01/F12-02/F12-04 /
  金丝雀只读消费冻结物（week-12 五文件 sha 前后一致 + 答案键 LF sha 对齐
  第 12 周记录） / 回放终态序列与效果（8/10、forbidden=0、unconfirmed=1、
  效果合计 3） / 两视图永不合并 + NS-HOLD-06 双视图分歧活证据 /
  遥测脱敏 90 span 零违规 + 扫描器反向有效性 / 工作台 API 端到端
  （replay/canary/trace/redaction/slo/reset + 404） / SLO 草案 v0 诚实性
  （[U] 与无编造百分比） / 工具允许列表不变 / 样例工件门 /
  归约器语义（denied 携带效果 → forbidden_effect_count 抓得住））。
- STAGE-3 扫描范围 39 → 44 文件：`mcptool/telemetry.py`、
  `mcptool/run_canary.py`、`mcptool/run_telemetry_replay.py` 与
  `workbench_observability/__init__.py`、`workbench_observability/app.py`
  入列；55 个输出点零违规。遥测自检清单不持有凭据字面量（凭据检测职责
  仍在 STAGE-3 全范围字面量扫描 + "token" 键名兜底）。
- 工具层证据：金丝雀 60 例重考 58/60 与冻结答案键一致（行为漂移 0、语料
  卫生 0、已知失败组合不变）；week-12 冻结物零写入（test_04 sha 前后复核）；
  回放 10 任务效果合计恰 3（两次已确认创建 + 一次待对账）、拒绝/撤销/
  超时路径效果恒 0；90 段 span 全链可还原且脱敏零违规。
- 一致性门复验：第 8 周动态发现测试数（106）已同步进 README；baseline jsonl
  行尾搅动本轮再触发，`git restore` 未入库。





## 十三、Week 17 CI 运行证据（2026-10-09，依赖故障安全降级 × 关闭开关 × 恢复复验）

```
CI RUN 2026-10-09T08:33:48+00:00 (UTC) | host=local | python=3.13.15
[PASS] STAGE-1-schema :: integrity=ok; migrations=3; required_tables_missing=[]; ingestion_tables=2
[PASS] STAGE-2-unittest :: Ran 122 tests in 29.223s, exit=0
[PASS] STAGE-3-redaction :: scanned_files=48, emissions_checked=56, violations=0
[PASS] STAGE-4-clean-env-smoke :: clean_dir=app; port=60721; smokes_passed=4/4
CI RESULT OK | duration=31.8s
```

证据日志：`evidence/ci-run-w17.log`（新增独立文件，未追加 w16/w15/w08 历史）。

Week 17 变化点（交付物见 README「第 17 周：让依赖故障进入安全降级路径」小节）：

- STAGE-2 全量测试 106 → 122（新增 `tests/test_degradation_week17.py`
  16 个用例：开关注册表审计与未知开关拒绝 / 审计快照不可变 /
  MCP_OFF 降级封套合同（action_preview + reason_code=mcp_unavailable +
  0 效果 + 审批不消耗）/ 权限判定优先（无审批/撤销 -> 确定性错误不被降级
  吞掉）/ 注入降级恰 1 次探测（不重试、服务端不被打扰）/ 深层缝同终态 +
  幂等键防重复效果 / 降级 store 快照逐字节不变 / trace 终态 action_preview_held
  -> 允许终态 + 降级带效果被 forbidden_effect_count 抓住 / 恢复复验注入路
  （同一 run_id+同一审批续跑恰 1 效果 + 幂等重放）/ 恢复复验开关路
  （按批准复位 + 审计留痕）/ 恢复后受限任务仍拒绝 / D-01 模型超时固定演练 /
  D-02 索引过期固定演练 / 演练全套（8 段 pass + 效果合计 3 + 脱敏 0 违规）/
  允许列表与 week-15 冻结合同 sha256 不变 / 工作台 API 端到端）。
- STAGE-3 扫描范围 44 → 48 文件：`mcptool/kill_switch.py`、
  `mcptool/run_degradation_drill.py` 与 `workbench_degradation/__init__.py`、
  `workbench_degradation/app.py` 入列；56 个输出点零违规。
- 工具层证据：8 段固定演练全 pass（D-01 模型超时 -> system_failed /
  D-02 索引过期 -> abstained / D-03+D-04 MCP 不可用（注入/开关两路同一终态）
  -> action_preview（0 效果、审批不消耗、探测恰 1 次）/ D-05a+b 恢复复验
  （同一 run_id 续跑恰 1 效果 + 幂等重放无重复 + 复位审计留痕）/ D-05c
  受限任务仍拒绝 / D-06 第 15 周 A-06 对账回归）；allowed 7/8、
  forbidden_effect_count=0、效果合计恰 3；证据
  `week-17/degradation-drill-evidence-v1.jsonl`（86 行，逐行可扫描 0 违规）。
- 遥测词表 v1.1 增补（登记性扩展）：第八类可见终态 `action_preview`（允许），
  既有六类语义零改动；降级终态携带效果并入 forbidden_effect_count 口径。
- 一致性门复验：第 8 周动态发现测试数（122）已同步进 README；
  deterministic-baseline-results-v1.jsonl 行尾搅动本轮再触发，
  按既有口径 `git restore`，未入库。

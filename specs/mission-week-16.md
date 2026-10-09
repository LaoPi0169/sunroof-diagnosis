# Mission Week 16 —— 离线质量金丝雀与运行期 SLI（「两种体温计」可观测性）

- 日期: 2026-10-09 · 分支: week16-observability → main（--no-ff）
- 上游: main `c8186b3`（week15-adversarial 合并后）+ 上轮未入库的
  `week-15/attack-cases-plain-guide-v1.md` 随本分支补交
- 手册章节: specs/business-background.md 3667–3888「第 16 周：离线质量金丝雀 /
  运行期 SLI / SLO 草案」

## 一、来源与必要性（手册 §先看完成品）

系统要长期运行，需要回答两个不同的问题：「回答的内容对不对」（质量）与
「过程是否按合同走」（健康度）。手册给出两支体温计：**离线质量金丝雀**——
用冻结考卷定期重考，量内容正确性；**运行期 SLI/SLO**——对每个 task_run_id
记录终态，量过程健康度。最小心智模型：两个分母（考卷 60 例 vs 窗口内任务数）
**永不共享**；运行期只看得见终态，看不见内容错误——必须靠考卷抓「假成功」。

## 二、交付物（手册 §本周产物 七件 + 实现）

| 交付物 | 内容 |
|---|---|
| week-16/task-quality-canary-contract-v1.md | 考卷/答案键绑定（sha256 fail closed）+ 逐题判定 + 通过判据（行为一致而非全绿）+ 节奏 + 红线 |
| week-16/runtime-sli-spec-v1.md | task_run_id 观测单元 + 状态对照表（六类可见终态/非终态）+ 四个 SLI 口径 |
| week-16/slo-proposal-v0.md | 5 条 SLO 候选；除恒等硬门 forbidden_effect_count=0 外目标/窗口全部 [U]；评审清单 |
| week-16/trace-map-v1.md | 九阶段记录面（缺席留痕）+ 两断点画实（RAG→策略版本链；MCP→审批+幂等键） |
| week-16/telemetry-contract-v1.md | span 结构 + 允许/禁止字段白名单 + restricted 边界 + 验证方法 |
| week-16/observability-baseline-report-v1.md | 双视图基线（58/60 vs 8/10）+ NS-HOLD-06 双视图分歧活证据 |
| week-16/telemetry-redaction-test-v1.md | 90 span 零违规 + 扫描器反向有效性 + STAGE-3 同源扩容 |
| week-16/business-user-test-script-v1.md | 业务用户逐步测试脚本（O-00..O-06） |
| mcptool/telemetry.py | TaskTrace（九阶段 span）/ classify_terminal（终态分类表）/ reduce_runtime_metrics（SLI 归约）/ scan_spans_for_violations（脱敏自检）——纯观测零效果 |
| mcptool/run_canary.py | 金丝雀运行器：冻结答案键重考 60 例 + 失败指路 F12 登记 |
| mcptool/run_telemetry_replay.py | 合成回放 O-01..O-10（固定时钟）：六终态 + 两非终态 + 双视图分歧演示 |
| workbench_observability/ | 业务用户可观测性工作台（端口 8016）：两视图并排物理分开 / 任务追踪 / 脱敏检查 / SLO 展示 / 红线 |
| week-16/canary-baseline-v1.jsonl / telemetry-span-sample-v1.jsonl | 逐例对照基线（60 行）+ 遥测样例流（90 行，已过扫描） |
| tests/test_telemetry_observability.py | 12 用例（全量 94 → 106） |
| ci/redaction_scan.py | SCOPE_FILES 39 → 44 |
| README 第 16 周段 / ci-evidence §十二 / evidence/ci-run-w16.log | 运行与证据 |

## 三、设计决定

1. **通过判据 = 行为一致，不是全绿**：金丝雀把 58/60 的 2 例已知失败与
   失败登记（F12-01/F12-02/F12-04）自动关联——它的职责是发现**新**漂移
   （behavior_drift_cases），不是掩盖旧失败；
2. **答案键 sha256 双口径**：LF 归一值对齐第 12 周冻结记录（5a614cda…dfed），
   raw 值检测行尾搅动（仓库既有惯例），两者不一致只说明行尾、不说明内容变化；
3. **非终态不是低分**：result_unknown/reconciliation_pending 不进 allowed_terminal_rate
   分子也不算失败——「诚实停住」单独按 unconfirmed_effects 计数，防止统计口径
   伪造终态；
4. **forbidden_effect_count 与 allowed_terminal_rate 相互独立**：approval_denied
   是允许终态（安全停住）但其携带任何效果增量都是合同违规——两个指标照抓，
   防止「过程健康」掩盖「合同被违反」；
5. **O-08 注入缝子类**：A-06 第二段需要「创建前查重通过、提交后对账查询失效」
   的精确窗口——静态 query_outage 会让创建 fail-closed（0 效果到不了对账段），
   `_OutageAfterCommitTickets` 在 ResponseLostError 抛出的同一瞬间翻转查询开关；
6. **holdout 案例的 mcp_call 段如实 not_applicable**：NS-HOLD-06 不在只读工单库，
   内容管线与冻结评测同构（同一 run_case），不假装工具在环；
7. **遥测不持有凭据字面量**：凭据检测职责归 STAGE-3 全范围字面量扫描；
   遥测自检清单只含客户字段/政策正文标记 + "token" 键名兜底。

## 四、验收门映射（手册 3667–3888 → 证据）

| # | 验收门 | 证据 |
|---|---|---|
| 1 | 离线金丝雀合同（考卷/答案键/分母/判据） | canary-contract §二~四；run_canary 输出 sha256 校验 |
| 2 | 金丝雀基线可复现（含已知失败指路） | baseline-report §二；test_03（58/60 + F12 关联） |
| 3 | 任一 task_run_id 可还原路径（两断点画实） | trace-map §二/三；test_01（九阶段 + 版本链 + 审批/幂等键） |
| 4 | 运行期 SLI：终态对照表 + 分母口径 | runtime-sli-spec §二/三；test_02/test_05/test_06 |
| 5 | 非终态不折进通过率；未确认效果单独口径 | test_05（O-08 不入分子、unconfirmed=1） |
| 6 | 拒/撤/失败路径效果恒 0 被 SLI 抓住 | test_12（denied+effects → forbidden=1）；test_10 |
| 7 | SLO 草案：目标/窗口 [U]，非达标声明 | slo-proposal §一/二；test_09（无编造百分比） |
| 8 | 遥测脱敏（正文/载荷/凭据零出现）+ 扫描器有效 | telemetry-redaction-test §三；test_07/test_11；STAGE-3 44 文件 0 违规 |
| 9 | 业务用户可测试的网页交付物 | workbench_observability + business-user-test-script（O-00..O-06）；test_08 API 端到端 |
| 10 | 两视图永不合并 | 两合同红线 + 指标输出硬编码警示；test_06 |

## 五、运行结果（2026-10-09 真机）

- `python -m mcptool.run_canary --out week-16/canary-baseline-v1.jsonl`：
  60 例重考 58/60，behavior_drift=[]，hygiene=[]，2 例已知失败指路 F12-01/F12-04
  与 F12-02，退出码 0，week-12 冻结物字节级零改动；
- `python -m mcptool.run_telemetry_replay --out week-16/telemetry-span-sample-v1.jsonl`：
  10 任务 allowed 8/10、forbidden 0、unconfirmed 1、效果合计 3、90 段 span；
- `python -m unittest discover -s tests`：**Ran 106 tests, OK**（94 → 106）；
- `python ci/run_local_ci.py --evidence-file evidence/ci-run-w16.log`：四阶段 PASS
  （STAGE-2 Ran 106；STAGE-3 scanned_files=44, emissions_checked=55,
  violations=0；STAGE-4 smoke 4/4）。

## 六、诚实边界

1. 运行期视图是**固定剧本回放**，不是真实流量监控；SLO 目标/窗口全部 [U]，
   本周交付口径与草案，不是达标结论；
2. 金丝雀度量内容、运行期度量过程，两数永不合并；「两支都有」不等于
   「系统已被证明正确」——NS-HOLD-06 硬门失败原样保留并指路登记；
3. 回放 duration_ms 为观测值，本版不设时延门；真实系统的采样/保留策略 [U]；
4. 遥测是教学进程内实现（无 OTLP/后端）；练的是记录面纪律与 SLI 口径，
   不是观测平台工程；
5. 抽样复核独立进行（SLO-5 [U]），不得以金丝雀代填。

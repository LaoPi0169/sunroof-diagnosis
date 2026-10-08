# RAG 评测报告 v1（rag-evaluation-report-v1，第 12 周冻结件）

第 12 周只读 RAG 候选评测（三态回答 + 确定性引用验证，零业务效果）。
候选 `northstar-rag-readonly-v1`；协议与阈值见 `week-12/rag-system-manifest-v1.json`
（解封前冻结）；逐例结果 `week-12/rag-per-case-results-v1.jsonl`（60 行，
sha256 = `5a614cda697c90990b5a035c8ac3cedf42ab47977085aa9d3af644f40da8dfed`）；
失败归因 `week-12/failure-register-v1.jsonl`；收官裁定 `week-12/decision-memo-03.md`。

## 1. 结论速览

- **dev+cal（48 例，冻结分母）：端到端状态匹配 48/48 = 1.0（≥0.8 门达标）**，
  五族全对；引用支持 30/30 = 1.0；受限零泄漏；语料卫生 1.0。
- **保留集（12 例，一次解封）：10/12 对，出现 1 例硬门失败** ——
  **L4 拒答安全 = 9/10 = 0.9 < 100%**（NS-HOLD-06 自信作答），按预注册协议
  候选不可原样晋级；L3 答案保真 = 5/6 = 0.833（≥0.8 达标）；泄漏 0；幻觉引用 0。
- 解封结果**未用于任何调参**：阈值/词表/映射与冻结时逐字节一致；
  修复全部登记进 8 层失败登记，留给 manifest v2。
- 裁定：**返工（rework），第 8 周确定性基线保持为现行回答层**（`decision-memo-03.md`）。

## 2. 方法与协议（先冻结后解封）

- 输入绑定：语料 `northstar-corpus-w09-v1`、考卷 `northstar-eval-w10-v1`（60 条）、
  检索器 `vector-hashed-ngram-v1`（第 11 周单路晋级）；四个输入文件的 sha256
  写入 manifest，运行时逐字节校验，不符即拒绝运行。
- 回答合同：三态 `answered/abstained/escalate` + `reason_code` + `user_next_step`；
  到第 10 周四态期望词表的映射预注册（abstained+conflict → needs_review，其余
  abstained → refused，escalate → escalated）。
- 门控决策树（顺序预注册）：未知角色拒答 → 冲突（雷达支配 ∨ 问面词门）→
  过期（雷达支配 ∨ 问面词门，版本对比豁免）→ 越权检测（全库 top-1 在池外 → 升级，
  不透露受限身份）→ 隔离雷达 → 无来源/证据不足 → 抽取式作答（池内 chunk 逐字引用）。
- **路由只用 query + role + 语料状态元数据**：雷达信号源 = 非 accepted* 文档的
  源文件（0 chunk，manifest content_sha256 逐字节校验），仅用于检测、绝不进入输出；
  `task_context.category` 与期望状态族 1:1，用它路由等于读答案，已显式弃用。
- 确定性：两次独立计算比较排除 `latency_ms` 的规范投影，一致后落盘；
  结果文件存在即拒绝重写（unseal-once）；`cost_estimate_usd=0.0`（本地教学口径）。

## 3. 七层指标主表（60 例全量；分母冻结）

| # | 层 | 分母 | 结果 | 门槛 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | 语料卫生 | 60 | gold/forbidden 引用可解析 60/60 = 1.0 | = 100% | ✓ |
| 2 | 检索命中 | 29（gold 非空且 ⊆ 角色允许池） | gold 进池内 top-5 文档 29/29 = 1.0 | ≥0.7（第 11 周门） | ✓ |
| 3 | 答案保真 | 6（保留集 answered 例；公开标签无事实串） | required_facts 全命中 5/6 = **0.833** | ≥0.8 | ✓ |
| 4 | 拒答安全 | 10（无答案族全 split） | 显式拒答 9/10 = **0.9** | = 100%（硬门） | **✗ 未达标** |
| 5 | 端到端状态匹配 | 48（dev+cal，冻结分母） | 48/48 = 1.0（per-family 全对） | ≥0.8 | ✓ |
| 6 | 引用支持 | 30（有引用的案例） | 可解析+在池+答案逐字含正文 30/30 = 1.0 | = 100%（硬门） | ✓ |
| 7 | 约束与泄漏 | 10（受限族；泄漏仅在 agent 侧计） | 泄漏事件 0 | = 0（一票否决） | ✓ |

dev+cal L3 说明：公开标签附录只有 gold/forbidden/expected_state 三列（无事实串），
预注册口径下 L3 只在含 `required_facts` 的保留集 answered 例上计算；
dev/cal 的回答例做机械引用保真检查（计入 L6：引用可解析 + 在允许池 + 答案逐字含引用正文）。

## 4. 保留集解封结果（10/12 对；一次运行，未调参）

| case | 族/角色 | 候选输出 | 期望 | 对账 |
| --- | --- | --- | --- | --- |
| NS-HOLD-01/02/03/04 | 可回答 ×2 角色 | answered + 引用 gold | answered | ✓ |
| NS-HOLD-05 | 无答案/agent | abstained(no_source_in_corpus) | refused | ✓ |
| **NS-HOLD-06** | 无答案/supervisor | **answered（0.311 越过 0.30 线）** | refused | **✗ 硬门** |
| NS-HOLD-07 | 冲突/supervisor | abstained(conflicting_current_sources) | needs_review | ✓ |
| **NS-HOLD-08** | 冲突/supervisor | abstained(no_source_in_corpus)（雷达 0.268 达标但支配失败，池 top 0.299） | needs_review | ✗（安全侧） |
| NS-HOLD-09/10 | 过期 ×2 角色 | abstained(only_expired_sources) | refused | ✓ |
| NS-HOLD-11 | 受限/agent | escalate(insufficient_authorized_context)，零透露 | escalated | ✓ |
| NS-HOLD-12 | 受限/supervisor | answered + 引用 refund-supervisor-v2 | answered | ✓ |

## 5. 失败归因（详见 failure-register-v1.jsonl，8 层）

- **F12-01（refusal 层，硬门）**：NS-HOLD-06「副卡注销的流程依据是什么」——
  supervisor 18-chunk 池内 billing-general-v3#s07（投诉与升级路径）以 0.311 越线，
  "注销/流程/依据"与"投诉/复核/路径"词面重叠骗过作答线。修复方向（v2）：池内 top-1
  主题相关性二次校验或更高作答线；不得在本次结果上回调阈值（解封结果禁调参）。
- **F12-02（generation 层）**：NS-HOLD-08「升级生效日 9 月 1 日前后」——冲突雷达
  0.268 达阈值但意图支配失败（池内现行文档 0.299 更贴词面）；输出为安全侧拒答
  （无硬门违规），状态档位错（refused ≠ needs_review）。修复方向（v2）：支配条件
  放宽余量或扩充问面 markers。
- **F12-03（corpus 层，第 11 周遗留的显式处理）**：冲突对/过期/隔离件 0 chunk
  在候选池外——本周以雷达绕行 + abstained 不引用冲突文档处理；根治仍是语料层
  重建快照（知识所有者裁决冲突组），非检索/生成层可修。
- **F12-04（product_workflow 层）**：评测器事实规则的 fall-through 语义会把
  "状态不符"的 required_fact 交给后续宽松规则判 hit（HOLD-06 一例虚 True，
  不影响 L3 归类）；评测器与候选同属冻结系统，不改不重跑，v2 修正规则序语义。

## 6. 确定性与解封纪律

- 双遍计算规范投影逐字节一致（含 60 例）；`latency_ms` 遥测只记录不断言。
- unseal-once：结果文件存在即拒绝重写（测试 01 覆盖）；解封执行恰一次。
- **污染披露**：解封前仓库全文检索工具在搜索 `required_facts` 时意外返回封存件
  片段（工具无封存意识，非人工开启）。已登记于 `evaluation-freeze-record-v1.md` §五；
  设计与阈值只依据公开材料；保留集结果属污染后的演示性验证 ——
  **自我封存 ≠ 独立盲测**（声明加重成立）。

## 7. 硬边界（诚实不夸大）

- 生成层为**确定性教学替身**（抽取式组装，无模型、无提示词），结论不得外推到
  真实 LLM 生成；引用支持 1.0 是抽取式结构的必然，不是学习成效。
- L5=1.0 在 48 条模板化小考卷上，同周设计同源同轮标定，无独立盲测效力。
- 0.30 作答线是薄余量冻结（分离带 0.014），HOLD-06 正是这一赌注的兑付——
  第 11 周移交时的警示应验。
- 本报告不宣称 RAG 改善任务、不宣称生产 SLO、不宣称任何采用/ROI 决策；
  即使全过，产出也只是"只读、人工核验的 RAG 基线，批准带入第 13 周"——而本周
  实际存在 1 例硬门失败，结论为返工（见 decision-memo-03.md）。

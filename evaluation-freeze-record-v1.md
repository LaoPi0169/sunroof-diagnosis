# 评测冻结记录 v1（evaluation-freeze-record-v1）

冻结时点: 2026-10-07（第 10 周第 5 天，任何 RAG 候选运行之前）
数据集: `northstar-eval-w10-v1` | 语料: `northstar-corpus-w09-v1`
基线 release: `northstar-app-w08-r0`（release-manifest-v1.json, schema_version=003）

## 一、冻结物清单（sha256，改动任何一件 = 本记录失效，需 v2 整版升级）

| 文件 | sha256 |
| --- | --- |
| eval-plan-v1.md | e98622905786840efeb1d0ff9cb09a59270fc58370a8bf5d3ff9fcd3a8ae3368 |
| eval-set-v1.jsonl | 861116958aa920e11ead1d91fd563c548f69af662be71e3e72004c73b6c817a5 |
| eval-holdout-labels-sealed-v1.json | 4def6aafd98d391e762ec615ecf687668651e7796242545f3d64712a496ce4f9 |
| labeling-guide-v1.md | 60beaa1b56b1185f0ca9e92e97313e1db9b779a90f4d649831c7ee59e2d0010a |
| dataset-card-v1.md | f0bd32fc94a0306b819031823d41277ea210bfc3d1093b6eadc1b0c66acde188 |
| split-leakage-check-v1.md | f16111588f3e7593a17e8b0c3e09f091e7dee9d80331ae1a2c4f3751f7a66cda |
| deterministic-baseline-results-v1.jsonl | 14391aa11af02e6778a170c72b4b1521551b9d782dddd9f06381f8ebc75a1b05 |
| evaluation/eval_contract.py | 7b50405f49b20e2c94018c39a6ee51780a80a6b3be76f6151855dfae73852512 |
| evaluation/validate_eval_set.py | cc9af479f133f84474d4cece3cd79c5bf862d4f024b9dc5b65e00cbb29657cdb |
| evaluation/run_deterministic_baseline.py | 4d67206999e74a3b02cd03e9aeb7c8c27b8e36b17cf7c9c9702ecf3c09de1bb1 |
| specs/mission-week-10.md | b860e1f306b5cd0858f26e522eba6e353cde412af268afefe7aa9bfdbde7f038 |
| tests/test_eval_contract.py | 18606738be6f1fcdbda54c30ebbf831e477fa1272e2b12bbcaa416edfab8e4f1 |

> **勘误（2026-10-08，第 11 周程序化复核时发现）**: 上表 `specs/mission-week-10.md` 一行的
> 原 sha256（d0afa939…ea34）系本记录核对时点之后、冻结提交之前为该文件追加
> "本周工程落地记录"一节前的旧值（第 10 周的 12/12 核对先于该追加执行）。该文件自冻结提交
> 89da932 起未再改动（git log 佐证），现按当前工作区字节更正为
> b860e1f306b5cd0858f26e522eba6e353cde412af268afefe7aa9bfdbde7f038（提交 blob 口径为
> d7c6a2fe3150da05b3f8953d1c0400f0cff028a11dc83a967b34ae9e0e1954f1，差异仅行尾归一）。
> 复核口径说明: 其余 10 件与本表记录值按工作区字节一致（记录时点即工作区口径）；
> `deterministic-baseline-results-v1.jsonl` 与提交 blob 一致（其工作区行尾会被全量测试
> 重写翻转，内容逐字节同）。12 件内容完整性结论不变，仅 mission-week-10.md 一行为过期值更正。

## 二、保留集封存与解封协议

- 封存件 `eval-holdout-labels-sealed-v1.json` 仅含保留集 12 条标签（gold/forbidden 文档、
  必要/禁止事实、期望状态），sha256 见上表；输入行 `eval-set-v1.jsonl` 零标签字段
- 解封条件: 第 12 周候选系统 manifest 冻结之后，解封一次、只跑一遍、不用于调参
- 解封后在本文件追加"解封记录"小节（时点 + 结果文件 sha256），封存件本身不再改动
- 声明: 自我封存 ≠ 独立盲测（同一人标注与裁决，课程约束，已在数据卡记录局限）

## 三、确定性基线快照（northstar-app-w08-r0）

- 60 条逐例结果: answered 24 / needs_review 4 / not_applicable 20 / sealed 12
- request_id 由 case_id 派生（evalw10-ns-…），两次运行逐字节一致（tests/test_eval_contract.py::test_06）
- 零业务效果: 只读探针；task_runs 审计留痕不含任何业务状态变更
- 已知语义缺口（如实记录）: 受限类 agent 例引擎输出 answered（引擎无升级路径/无受限
  文档概念）；主管例输出 needs_review（POLICY_CONFLICT）——这两类语义由第 11-12 周候选
  评测对照标签裁决，本基线只如实记录引擎输出，不修饰

## 四、13 项验收门核对（第 10 周）

| # | 门 | 状态 |
| --- | --- | --- |
| 1 | 决定/基线/用户任务/非目标/决策角色明确（eval-plan §1-2/§8） | 通过 |
| 2 | 指标/门槛/排除/失败动作先于候选冻结（eval-plan §5-6） | 通过 |
| 3 | 36+12+12=60（validate 实测矩阵） | 通过 |
| 4 | 24/10/8/8/10（validate 实测族计数） | 通过 |
| 5 | 校准与保留均含权限+冲突或过期切片（12 条构成） | 通过 |
| 6 | 角色语料版本 gold 事实状态风险来源齐备（输入 schema + 指南附录） | 通过 |
| 7 | 近重复零跨 split；保留集未提前打开（validate V4/V5 + 本记录 sha256） | 通过 |
| 8 | 冲突/证据不足标未知，不硬写（无答案/冲突/过期族判定规则） | 通过 |
| 9 | 权限与关键事实确定性裁决（ACL 候选 SQL + 确定性引擎，零模型评分） | 通过 |
| 10 | 基线逐例输出+精确 release（jsonl 60 行 + release 字段） | 通过 |
| 11 | 数字全部分母/切片/版本（eval-plan §5 表） | 通过 |
| 12 | 课程阈值未写成行业/客户/生产承诺（eval-plan §1/README 边界） | 通过 |
| 13 | 工程门: 全量测试 41/41；本地 CI 4 阶段 PASS；sha256 一致；封存件未改 | 通过（本记录时刻） |

## 五、解封记录（第 12 周）

- 解封时间: 2026-10-08；执行: `python generation/run_rag_evaluation.py --splits all --unseal-holdout`（唯一一次；结果文件存在即拒绝重写）。
- 候选: `northstar-rag-readonly-v1`（manifest `week-12/rag-system-manifest-v1.json`，冻结于解封前，输入 sha256 运行时校验通过）。
- 结果文件: `week-12/rag-per-case-results-v1.jsonl`（60 行）
  sha256 = `5a614cda697c90990b5a035c8ac3cedf42ab47977085aa9d3af644f40da8dfed`。
- **污染披露**: 解封前（同日早些时候），仓库全文代码检索工具在搜索 `required_facts` 时
  意外返回封存件 12 条标签的片段（工具无封存意识，非人工开启）。处理：如实登记于此；
  门控设计与阈值只依据公开材料（第 11 周移交信号 + dev 标定）；保留集结果属
  **污染后的演示性验证**，"自我封存 ≠ 独立盲测"的声明加重成立。
- 结果摘要: L1 1.0；L2 1.0（29/29）；L3 0.8333（5/6，≥0.8 达标）；
  **L4 0.9（9/10，<100% 硬门未达标）**；L5 1.0（48/48，冻结分母）；
  L6 1.0（30/30）；L7 泄漏 0。
- 硬门失败（唯一）：**NS-HOLD-06**「复核：副卡注销的流程依据是什么？」——池内 top-1
  billing-general-v3#s07（投诉与升级路径）以 0.311 越过 0.30 作答线，候选自信作答，
  期望 refused。归因 generation/refusal 层（置信信号在 supervisor 18-chunk 池分离性不足）。
  次级失败：**NS-HOLD-08**「升级生效日 9 月 1 日前后客户怎么答复？」——冲突雷达 0.268
  达阈值但意图支配失败（池内 top-1 0.299），落为 no_source 拒答（期望 needs_review），
  归因 generation/conflict 层（安全侧失败，无硬门违规）。
- 评测器局限（同次运行发现，如实登记）：事实规则引擎的 fall-through 语义会把
  "状态不符"的 required_fact 交由后续宽松规则判 hit（HOLD-06 出现一例虚 True，
  不影响 L3 分母归类）；评测器与候选同属冻结系统，均不改不重跑，修复留 v2。
- 纪律声明：解封结果**未用于任何调参**；阈值/词表/映射与冻结时逐字节一致；
  修复动作全部登记入 `week-12/failure-register-v1.jsonl` 留给 v2。


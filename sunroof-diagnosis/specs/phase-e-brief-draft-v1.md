# 阶段 E Brief 草案 v1（评测器 v2 + SLO 定标准备）

状态：**FROZEN —— 已拍板（2026-10-14）**：E1 = 方案甲（子仓库事实层增强、CI 全覆盖零跨树）；E2 = 聚合器 + runbook；批次节奏 = 两批连做（E1→E2），并追加交付「可交互 web 工作台（生产现场值守场景模拟）+ 详细业务场景使用手册」；本文档即冻结的范围/批次/验收口径，实现按批兑现并在 ci-evidence 留痕。
回链：`production-hardening-brief-v1.md` §2.5（本 Brief 的预留出处：「SLO 定标与评测器 v2
（HOLD-06）不在本 Brief，属另一方向」）· 上级 `evaluation-freeze-record-v1.md`（评测器局限
登记：「fall-through……修复留 v2」）· `week-12/decision-memo-03.md`（变更预算已用尽，RAG v1
冻结不动）· 上级 `week-16/slo-proposal-v0.md`（[U] 清单与定案输入）· `../fde-roadmap-v1.md`
红线 12（非作者复验不可代办）/ 红线 14（升级 = 新版本文件 v2 并存，冻结物零改动）。
纪律：沿用阶段 D 全链——冻结合同 → 测试 → 实现 → 全量绿 → 本地 CI 四阶段 → 证据落盘 →
文档 → 显式逐文件 add → 提交推送 → 远端双 workflow 确认。

## 0. 勘察结论（本 Brief 的事实基础，2026-10-14 实测）

1. **「阶段 E」此前零定义**；唯一有据方向即上 Brief §2.5 预留，拆解为两个独立可执行项。
2. **两套考卷、两代评测器并行**（勘察实测）：
   - 上级仓库 `NS-*` 60 例（北极星）：第 12 周解封定格 **58/60**——NS-HOLD-06（语料外问句）
     自信作答（top-1 0.311 越过 0.30 作答线，**行为层**失败，已锁进金丝雀冻结基线与失败登记
     F12-01/F12-04）；其**评测器**（上级 `generation/evaluate.py`）另有**判定层**缺陷：`check_required_fact`
     「触发但未通过则继续向后扫描」——NS-HOLD-06 的 required_fact 被 `_r_out_of_scope` 如实判否后，
     被 `_r_version_source`（fact 含「来源」触发词）以**空真**（fact 无版本号 → `all([])=True`）
     兜底虚 hit——即登记在案的 fall-through 虚 True。
   - 本子仓库 `SR-*` 60 例：**60/60**（SR-HOLD-06 被 `out_of_scope_phrase` 门正确拒答，conf 0.3198）。
     子仓库评测器（`generation/run_rag_evaluation.py`）**没有事实规则引擎**——required_facts/
     forbidden_facts 逐行落盘但**从未逐条判定**，L3 用 `citation_supported` 代理（结构使然）。
     这是子仓库 v1 评测器的**如实局限**（与上级 fall-through 是两个不同缺陷）。
3. **CI 覆盖约束**：`sunroof-ci.yml` paths 限定 `sunroof-diagnosis/**`——落上级 `generation/`
   的任何代码与本仓库 CI 门（STAGE-2 测试 / STAGE-3 脱敏扫描）**零交集**，需另扩 workflow 才能获得同权门。

## 1. 范围（两批，E1→E2；顺序可由拍板调整）

### E1 批次 1 · 评测器 v2（判定层补全 + fall-through 修正语义）

**落点（推荐·方案甲）**：全部落本子仓库，进既有 CI 门。上级 `evaluate.py` 病灶**冻结不动**，
其 fall-through 登记以「语义已由子仓库 v2 继承修正」注记归档（注记落本仓库文档，不改上级任何文件）。

| # | 交付 | 验收口径 |
| --- | --- | --- |
| E1-a | `evaluation/evaluator_v2.py`：事实规则引擎 v2，判定语义**预注册三条修正**：① 专属触发即终局——required_fact 一旦被专属规则触发（谓词非 None），该判定即终局，触发而判否 = miss，**绝不向后扫描**；② 空真修正——要求核验版本号/锚点的规则在 fact 无锚点时返回不触发（None）而非空真命中；③ 判定矩阵留痕——每条 fact 记录全部触发规则及各自判定（v1 上级只留最后触发），支撑非作者复验 | v2 对本子仓库 60 例**已落盘候选输出**（`week-12/rag-per-case-results-v1.jsonl`，零候选重跑——评测器是纯函数）重判：state 门与 v1 逐行一致（60/60 不变，v2 只增事实层不改行为判定）；规则触发词只从公开标签（labeling-guide）与结果行 required_facts 文本推导并在测试中预注册 |
| E1-b | 对账产物 `week-12/rag-per-case-factcheck-v2.jsonl`（60 行 × 事实级判定 + forbidden 校验 + 层聚合）+ 上级 fall-through 归档注记（ci-evidence 增节） | 60/60 state 门不变；SR-HOLD-06 事实层如实呈现 miss（abstained 无答案，required_facts 全 miss 是诚实结果，绝不因「拒绝得体」虚判 hit）；对账输出含分母与逐例 rule_id 留痕 |
| E1-c | `tests/test_evaluator_v2.py`（估 6–8 用例）：三条修正语义各一条（含上级 HOLD-06 型空真样本的等价构造——用合成 record 复现「触发判否后被兜底虚 hit」并断言 v2 拒绝该路径）、对账一致性、预注册触发词冻结断言 | 全绿并入 STAGE-2；`ci/redaction_scan.py` 范围 +1 |

**落点备选（方案乙，供拍板）**：上级 `generation/evaluate_v2.py` 并存 + 对上级 60 例重判 +
扩 `sunroof-ci.yml` paths —— 真正修复登记所指评测器，但需改 CI 结构且测试游离于 STAGE-2 门外，工作量更大。

### E2 批次 2 · SLO 定标准备包（[U] 定案流程 + 基线聚合器，零编造数字）

| # | 交付 | 验收口径 |
| --- | --- | --- |
| E2-a | `deploy/slo_baseline_aggregator.py`：从 SQLite 聚合 SLO-1（allowed_terminal_rate）/SLO-2（forbidden_effect_count，恒等硬门）/SLO-3（未确认效果确认时延）三口径，输出基线记录 JSON | **[U] 守卫**：窗口分母低于预注册最小样本阈值 → `verdict=insufficient_data`，[U] 保持待定；聚合器**不含任何目标值**（目标 [U] 未定，无可比对）；输出永久携带分母口径声明（与 `mcptool/telemetry.py` DENOMINATOR_NOTE 同源语义）与「非达标声明」标注；SLO-1/SLO-2 数字**永不合并**（两视图红线） |
| E2-b | `deploy/slo-adoption-runbook-v1.md`：上线后 30 天定标流程（采集 → 聚合 → 四项评审输入核对（真实流量量级/值班耐受/SLA/合规）→ 定案记录模板 → slo-proposal v0→v1 升级路径）+ `slo-adoption-record` 模板 | runbook 每步可执行（命令级）；模板含「无真实流量时怎样保持 [U]」段；红线重申（禁止合成回放/考卷结果代填 [U]） |
| E2-c | `tests/test_slo_baselining.py`（估 4–5 用例）：合成夹具库上三口径与 telemetry SLI 语义对账一致、insufficient_data 守卫（小样本绝不产出目标值）、SLO-2 恒等硬门恒可算、输出无目标值字段 | 全绿并入 STAGE-2；`ci/redaction_scan.py` 范围 +1 |

## 2. 非目标（重申，边界不放松）

1. **不填任何 [U]**：本环境无真实流量，E2 交付的是定案流程与采集器，不是达标声明；
2. **不回调候选**：RAG v1（门控/阈值/词表）零调优（decision-memo-03 变更预算已用尽）；
   HOLD-06 型**行为**修复属候选 v2，不在本 Brief；
3. **不做非作者复验**（红线 12）：week-18 A 路收口追加区保持留白，E1 的判定矩阵留痕即为其备料；
4. **上级冻结物零改动**：上级 `generation/*`（含 evaluate.py 病灶）、week-12/16 冻结物、
   `workbench_observability/*` 全部不碰（方案乙若被拍板，也只新增文件不改原件）；
5. 零模型调用、不拆微服务、DDL 零新增（E2 聚合只读既有表）。

## 3. 冻结面（E 轮零改动清单）

本子仓库：`week-12/rag-per-case-results-v1.jsonl` · `deterministic-baseline-results-v1.jsonl` ·
`week-16/canary-baseline-v1.jsonl` · `generation/contract.py` · `evaluation/` v1 三件 ·
api-contract · migrations 001-005 · 既有 183 用例语义 · mcptool/* · workbench_handoff/*。
上级：generation/* · week-11/12/16/18 冻结物。用户既存决策项四项维持不动，提交显式逐文件 add。

## 4. 预期数字与证据链（拍板后按批兑现）

E1 后：STAGE-2 **190±1**（183+7）；E2 后：STAGE-2 **195±1**；STAGE-3 **54 文件** 0 违规；
本地四阶段全 PASS（`evidence/ci-run-e1.log` / `ci-run-e2.log`）；远端双 workflow 预期 run
**#20 / #21**（#19 递推）。诚实边界：v2 事实层是「判定器补全」，不改变 60/60 与 58/60 任何
历史定格；E2 输出在真实流量注入前一律 `insufficient_data`。

## 5. 待拍板清单

1. **E1 落点**：方案甲（子仓库事实层增强，推荐——CI 全覆盖零跨树）vs 方案乙（上级 evaluate_v2 并存 + CI 扩围）；
2. **E2 深度**：聚合器 + runbook（推荐）vs 纯 runbook 文档；
3. **批次节奏**：两批连做 E1→E2（对齐阶段 D 先例）vs 只做其中一项 vs 单批合并。

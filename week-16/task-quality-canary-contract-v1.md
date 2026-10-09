# 任务质量金丝雀合同 v1（task-quality-canary-contract-v1，第 16 周）

- 日期: 2026-10-09 · 回链: `specs/business-background.md` 3667–3888 ·
  实现: `mcptool/run_canary.py` · 基线: `week-16/canary-baseline-v1.jsonl`
- 与 `week-16/runtime-sli-spec-v1.md` 的关系: 两种体温计之一 —— 本合同管
  「离线考卷金丝雀」（量内容正确性）；运行期 SLI 管过程健康度。
  **两个分母永不共享，两个数永不合并。**

## 一、它是什么（白话）

系统上线后，最怕的不是「报错」，而是「悄悄变笨」——没人改配置，但某次发版后
拒答开始变成自信作答。金丝雀的做法：把第 12 周冻结的 60 道考题和标准答案一起
锁进保险柜，每次发版前（以及定期）拿出来重新考一遍系统，逐题对答案。

## 二、考卷与答案键（输入绑定）

| 项 | 值 | 防篡改 |
| --- | --- | --- |
| 考卷 | `eval-set-v1.jsonl`（60 例，dev/cal/holdout） | manifest `input_sha256` 逐文件校验 |
| 答案键 | `week-12/rag-per-case-results-v1.jsonl`（60 行冻结逐例结果） | 运行输出 sha256；LF 归一值 = `5a614cda…dfed`（对齐第 12 周报告记录），raw 值另存作行尾搅动检测 |
| 阈值/词表/映射 | `week-12/rag-system-manifest-v1.json`（冻结） | 装载即校验，不符拒绝运行（fail closed） |
| 语料 | `northstar-corpus-w09-v1`（data/corpus-w09.db，缺省自动重建） | 同上 |

约束：答案键**只读消费**——金丝雀不写 week-12 任何文件（tests/test_04 字节级
复核）；评测管线与冻结评测调**同一个** `run_case`（generation/* 零改动）。

## 三、逐题对什么（判定项）

1. `state_match`：重跑 `mapped_state` 是否等于冻结 `expected_state`；
2. `behavior_drift`：重跑 `mapped_state` 是否偏离冻结 `mapped_state`
   （金丝雀真正的漂移报警器）；
3. 语料卫生：引用可解析、无禁止文档引用、泄漏事件 0。

## 四、通过判据（不是「全绿」）

- **通过** = `behavior_drift_cases` 为空 且 `hygiene_violation_cases` 为空
  且 失败组合与失败登记一致；退出码 0。
- **已知失败基线（第 12 周解封结果，如实保留）**：58/60 匹配；
  NS-HOLD-06（期望 refused，实际 answered，**硬门**）→ 登记簿 F12-01/F12-04；
  NS-HOLD-08（期望 needs_review，实际 refused，安全侧）→ F12-02。
  金丝雀的职责是**发现失败并指路登记**，不是把 58/60 修成 60/60。
- **报警** = 行为漂移 ≥1 例或语料卫生违规 → 退出码 1，发版阻断，人工归因。

## 五、运行节奏

- **强制**：每次影响 RAG 管线/语料/阈值的发版前（CI 内嵌，见 STAGE-2 用例 03）；
- **定期**：节奏 [U]（建议日更，待评审确定——无真实流量前不定死）；
- 运行成本：60 例约 2–5 秒（本地确定性管线），零 API 成本（cost_estimate_usd=0.0）。

## 六、红线

1. 金丝雀分数（58/60）**不是**线上成功率，不得对外宣称「系统 96.7% 正确」
   之类的换算（分子分母都不同）；
2. 不得因为金丝雀通过就跳过人工抽样复核（抽样率 [U]，见 SLO-5）；
3. 不得用金丝雀结果回调阈值/词表（第 12 周 unseal-once 纪律延续）；
4. 考卷/答案键任何变动 = 整体升 v2 并重走冻结流程，本 v1 一字节不动。

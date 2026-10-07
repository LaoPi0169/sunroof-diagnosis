# 数据集卡 v1（northstar-eval-w10-v1）

## 概要
- 名称: `northstar-eval-w10-v1`；60 条 JSONL（`eval-set-v1.jsonl`，UTF-8，每行一例）
- 用途: 第 10 周冻结的评测考卷；第 11-12 周候选比较的唯一判题依据
- 语料绑定: `northstar-corpus-w09-v1`（18 chunks；引用必须回到该快照清单）
- 输入行: case_id / dataset_version / split / corpus_version / user_role /
  task_context / query / risk_tags / provenance —— **不含任何标签字段**
- 标签: 开发/校准在 `labeling-guide-v1.md` 附录；保留集封存于
  `eval-holdout-labels-sealed-v1.json`（sha256 见冻结记录，第 12 周后解封一次）

## 数量矩阵（已由 evaluation/validate_eval_set.py 实测通过）
| 期望状态族 | 开发 36 | 校准 12 | 保留 12 | 合计 |
| --- | --- | --- | --- | --- |
| 可回答 | 16 | 4 | 4 | 24 |
| 无答案 | 6 | 2 | 2 | 10 |
| 当前来源冲突 | 4 | 2 | 2 | 8 |
| 只有过期来源 | 4 | 2 | 2 | 8 |
| 权限受限 | 6 | 2 | 2 | 10 |

## 构成与来源
- 六个任务类别（输入侧字段 `task_context.category`）: billing_general / price_difference_east /
  outside_corpus / upgrade_conflict / expired_reference / restricted_refund
- 每类别绑定语料参考文档（类别语义，非案例标签）: billing-general-v3 /
  price-difference-east-v5 / （无）/ upgrade-east-v2 + upgrade-notice-v4 /
  billing-general-v1（已废弃演示日期护栏）/ refund-supervisor-v2（主管专属）
- 案例为**教学合成**，由任务模板实例化并逐条人工复核；非真实客户数据
- query 归一化（去空白/标点/大小写）跨 split 零重复（近重复零泄漏，实测 60/60 唯一）

## 排除/标注规则
- 无答案类: 语料确实未覆盖的主题（发票冲红、欠费销户、以旧换新、硬件退货、异地销户、
  积分礼品、副卡注销、宽带提速包、路由器退货、礼品赔付），gold 留空
- 冲突类: 仅用第 9 周保留的 upgrade-east-v2 与 upgrade-notice-v4 冲突对
- 过期类: billing-general-v1（2025 版，已废弃）演示日期护栏
- 受限类: refund-supervisor-v2 主管专属；agent 期望 escalated 且**零泄漏**
  （不得输出条款正文/额度/文档存在性）；supervisor 对照组期望 answered

## 已知局限
- 自我封存的保留集 ≠ 独立盲测
- 无 unknown_role 案例（60 条均为 support_agent / supervisor；确定性基线如实输出无 refused）
- 金额/日期/套餐数值为演示值；引擎探针输入见 run_deterministic_baseline.py 的
  ENGINE_INPUTS 常量（区域=华东，日期/金额按类别固定）

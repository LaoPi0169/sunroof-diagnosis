# 检索失败登记 v1（retrieval-failure-register-v1）

第 11 周关键词/向量检索比较的失败诊断。数据来源：`week-11/keyword-results-v1.jsonl`、
`week-11/vector-results-v1.jsonl`（48 例 × 双检索器，ACL 下推，top_k=5）。
开发集诊断 4 条 + 校准集登记 2 条（NS-CAL-08/12 仅做失败层归因记录，未触发任何检索器修改；
校准集估计用途见 `retrieval-baseline-report-v1.md` §6），每条七字段，五层归因；
**诊断只用于报告与第 12 周决策，本周未做任何受控修改（预算 0/1 次执行）**。

## 失败层图例（五层，与 eval-plan-v1.md §5 对齐）

| 层 | 含义 | 可否由检索层修复 |
| --- | --- | --- |
| corpus | gold 不在语料/候选池（五态路由冻结、覆盖缺失） | 否（语料层动作） |
| acl | 角色无权看到 gold（设计如此，非故障） | 否（权限即需求） |
| retrieval | 池内 gold 未进 top-k | 是（检索器改动） |
| ranking_context | gold 在 top-k 内但名次被挤占/噪声混入 | 是（排序/加权改动） |
| label | 标注本身有误 | 否（走标注裁决） |

本批未登记 acl / label 层条目（受限类零泄漏、48 条公开标签装载校验全过）；图例保留备第 12 周复用。

## 登记条目

| case_id | retriever | 期望 | 实际 | 主失败层 | 根因假设 | 最小变化 | 反驳证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NS-DEV-21 | keyword | 无答案例不应产生高分证据（top-1 分数应显著低于可回答例区间） | top-1 = billing-general-v3，BM25=18.68，落入可回答例区间（11.31–25.10） | retrieval | 语料外查询与现行文档共享高频词元（套餐/计费/华东），BM25 无域外信号 | 检索层无单调修复；需生成层置信阈值（超出第 11 周预算） | 反例：NS-CAL-03 keyword top-1 仅 2.42，说明该层有方差但不可靠、不可作阈值 |
| NS-DEV-09 | keyword | 同上（supervisor 无答案例） | top-1 = billing-general-v3，BM25=12.79，混入可回答区间 | retrieval | 同 NS-DEV-21（共享词元主导 BM25 打分） | 同上 | 佐证：无答案例 keyword top-1 区间 2.42–18.69，与可回答例区间大范围重叠 |
| NS-DEV-02 | vector | gold billing-general-v3 应排第 1 | gold 排第 3，price-difference-east-v5 占前两位（top-1 余弦 0.305） | ranking_context | 哈希词元把共享术语（套餐/差价/2026）均摊到两文档，tf 归一化后名次边界窄 | 标题/字段加权（单变量受控修改候选）；本周未执行 | 反例：同族 NS-DEV-01 vector gold 排第 1（0.453），说明非系统性偏置 |
| NS-CAL-08 | vector | 冲突对 upgrade-east-v2 / upgrade-notice-v4 应可见，供生成层触发 needs_review | 两文档 0 chunk 不在任何候选池（第 9 周冻结）；top-1 = billing-general-v3，余弦 0.448 高分 | corpus | 五态路由把 conflict_review 文档冻结在候选池外（week09 设计），检索层无从看见冲突 | 知识所有者裁决冲突组后重建快照（语料层动作，非检索层可修） | 佐证：8 个冲突例 top-1 全部为 accepted 文档，全部存在单边误导风险（第 12 周必须显式处理） |
| NS-CAL-12 | keyword | gold refund-supervisor-v2 排第 1（supervisor 受限对照组） | gold 排第 2，billing-general-v3（24.98）居首 | ranking_context | 通用计费文档含大量共享词元，BM25 tf 饱和使长查询仍被通用文档压过 | 字段加权受控修改候选，未执行（校准集冻结） | 反例：NS-DEV-12 / NS-DEV-35 keyword gold 均排第 1 |
| NS-DEV-24 | vector | 无答案例 top-1 应低分且指向语义最近文档 | top-1 = refund-supervisor-v2（0.181）——主管有权可见但语义不相关 | ranking_context | 短查询 + 哈希碰撞使稀疏向量失焦；但分数低（无答案例 ≤0.290）可被生成层用作拒绝信号 | 换真实 embedding 服务或调 n-gram 阶数（超出本周预算，教学替身边界） | 佐证：8 个无答案例 vector top-1 全部 ≤0.290，与可回答例（≥0.304）完全分离 |

## 边界（诚实记录）

- 诊断基于本机语料（18 chunk）与教学替身向量化，结论不可外推到生产语料规模或真实 embedding；
- 登记条目的"期望"是检索质量目标，不等于答案正确性（生成层第 12 周裁决）；
- 登记与结果文件、回归种子由 `tests/test_retrieval_comparison.py` 交叉核验（条目存在性、
  层枚举、retrieval 层条目的 gold 缺失事实）。

# retrieval-failure-register-v1 —— 第 11 周检索失败登记（6 条，五层归因）

对账基准: `week-11/keyword-results-v1.jsonl` + `week-11/vector-results-v1.jsonl`
（48 例 × 2 检索器，与清单 sha256 绑定的协议一次正式运行）。

| # | 现象（实测） | 归因层 | 证据 | 可修性 |
| --- | --- | --- | --- | --- |
| F11-01 | conflict 族 6 例 Recall@5=0.0：gold（retrofit-bulletin-v2/notice-v4）为 conflict_review 文档，快照 chunk=0，候选池无任何该对 chunk | **corpus 层**（第 9 周设计：冲突件保留快照不出 chunk） | results 两文件 conflict 切片 | 检索层不可见不可修；所有者裁决并新建语料版本后重考 |
| F11-02 | expired 族 6 例 Recall@5=0.0：gold（service-general-v1）为 expired 文档，chunk=0 | **corpus 层**（谱系过期不出 chunk） | results expired 切片 | 同上；过期追问按第 10 周标签语义本就应 refused |
| F11-03 | no_answer 族 8 例：BM25 top-1 分数 7.82–18.88 与可回答族 21.18–65.77 无域外信号（量纲无界） | **retrieval 层** | keyword-results confidence_top1 | BM25 无置信语义；作降级路径时不承担 abstain 信号 |
| F11-04 | no_answer/answerable/conflict 的 vector top-1 区间存在薄重叠（answerable min 0.3712 < conflict max 0.3866 / no_answer max 0.3765） | **retrieval 层** | vector-results confidence_top1 | 单一绝对阈值 0.30 在本语料不可靠——任何类规则移交第 12 周保留集验证时必须携带此警示（第 11 周已预警） |
| F11-05 | restricted 族 pqe 4 例 gold=[]（分母 0），Recall@5 记 0.0 但语义是"无可见依据"，与真失败同格 | **metric 层** | results recall_denominator=0 行 | 已用 best_gold_rank/rank-1 与族切片区分；第 12 周状态门以 expected_state 裁决，不以 recall 裁决 |
| F11-06 | Recall@5=1.0 为 13/18-chunk 小池饱和值，两检索器无区分度 | **evaluation design 层** | results answerable 切片 | 不作为泛化声明；名次与置信信号作为辅助裁定依据 |

登记纪律：与结果文件逐条对账（脚本可复算）；不登记"感觉"。初稿两条与实况不符处
（族归属/阈值可分性）已对照结果文件纠正。

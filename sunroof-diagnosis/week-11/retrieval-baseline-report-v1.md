# retrieval-baseline-report-v1 —— 第 11 周检索基线报告

## 一、运行口径（分母/切片/版本）

- 案例分母: 48 = dev 36 + cal 12（holdout 12 条零执行，`not_executed`）；
- 语料版本: `sunroof-corpus-w09-v1`（chunk 池: pqe 13 / quality_supervisor 18，ACL 下推）；
- 考卷版本: `sunroof-eval-w10-v1`；协议: week-11 清单 v1（top_k=5，(-score, chunk_id) 定序）；
- 每检索器一次正式运行 + 第二遍确定性断言（规范投影排除 latency_ms 逐行一致）；
- Recall 分母 = 该例 gold 文档数（labeling-guide 公开标签附录）；gold=[] 的行
  （restricted-pqe 4 例）Recall@5 记 0.0 且在族切片中如实呈现（F11-05）。

## 二、切片结果（实测）

| 族 | n | kw Recall@5 | vec Recall@5 | kw rank-1 | vec rank-1 |
| --- | --- | --- | --- | --- | --- |
| answerable | 20 | 1.0 | 1.0 | 20 | 20 |
| no_answer | 8 | 0.0 | 0.0 | 0 | 0 |
| conflict | 6 | 0.0 | 0.0 | 0 | 0 |
| expired_only | 6 | 0.0 | 0.0 | 0 | 0 |
| restricted | 8 | 0.5 | 0.5 | 4 | 4 |

- answerable/recalled gold 全部 rank-1（13/18-chunk 小池饱和，两检索器无区分度）；
- conflict/expired 族 0 分不是检索失败而是**语料层事实**（这两类文档 chunk=0，F11-01/02）；
- restricted 族 0.5 = 主管 4 例 rank-1 命中 + pqe 4 例零分母；
- 置信信号: vector top-1 有界（0.24–0.61）但无答案/冲突/可回答族间存在薄重叠
  （0.3712–0.3866，F11-04）；BM25 无域外信号（F11-03）。

## 三、失败登记与回归种子

- 失败登记 6 条（F11-01..06，五层归因）见 `retrieval-failure-register-v1.md`，
  与两份 results 文件逐条对账；
- 回归种子 12 条（SR-01..12）见 `retrieval-regression-seeds-v1.jsonl`，锁定裁定行为。

## 四、口径勘误（如实）

初稿把 conflict/expired 0 分记为检索层失败——对照语料清单（chunk_count=0）纠正为
corpus 层；vector 置信分离初稿按参考模板写"完全分离"——实测存在薄重叠，已按
results 文件纠正并在 retriever-decision §2 中把晋级依据改为"有界可对照"而非"可完全分离"。

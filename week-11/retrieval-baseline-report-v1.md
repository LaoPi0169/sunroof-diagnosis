# 检索基线报告 v1（retrieval-baseline-report-v1）

第 11 周关键词 vs 向量检索基线比较（检索层评测，零生成、零业务效果）。
协议与配置见 `week-11/retrieval-comparison-manifest-v1.json`（运行前冻结）；
结果文件 `week-11/keyword-results-v1.jsonl` / `week-11/vector-results-v1.jsonl`；
失败归因见 `retrieval-failure-register-v1.md`；晋级裁定见 `retriever-decision-v1.md`。

## 1. 结论速览

- **晋级门（eval-plan §5 第 2 层，gold 进入候选比例 ≥ 0.7）：两个检索器均达标**——
  24 例分母上 Recall@5 micro = macro = 1.0。
- **1.0 是小语料饱和值，不是质量结论**：agent 池 13 chunks / supervisor 池 18 chunks，
  top_k=5 时 gold 几乎必然在池内。真正的区分度在名次与分数行为（§3/§5）。
- **硬门全过**：`restricted_candidate_count` 逐例 = 0（合计 0）；保留集未运行；
  第 10 周 12 件冻结物未改动（sha256 复核见测试与 CI）。
- **裁定：vector-hashed-ngram-v1 单路晋级第 12 周**，keyword 作为对照与降级路径
  （依据与被否选项见 `retriever-decision-v1.md`）。

## 2. 方法与协议（先冻结后运行）

- 输入绑定：语料快照 `northstar-corpus-w09-v1`（fixed-heading-chunker-v1）、
  考卷 `northstar-eval-w10-v1` 48 条（dev 36 + cal 12；holdout 12 条不解封不运行）、
  gold 仅取 `labeling-guide-v1.md` 附录 48 条公开标签（装载器校验覆盖与语料引用）。
- **ACL 下推**：第 9 周 `candidates_for` CANDIDATE_SQL 先按角色过滤允许池，
  打分只发生在池内；输出行另带独立核对（见测试 02）。
- 检索器：keyword = BM25(k1=1.5, b=0.75, CJK 单字+双字+ASCII 词元)；vector =
  确定性哈希词元向量（blake2b → 512 维，tf+L2，余弦）。并列以 (-score, chunk_id) 定序。
- **确定性**：两次独立运行比较排除 `latency_ms` 的规范投影（sort_keys JSON），一致后落盘；
  `latency_ms` 是遥测字段只记录不断言；本地实现 `cost_estimate_usd=0.0`（非生产成本口径）。
- **分数不可跨检索器比较**：BM25 与余弦量纲不同，所有跨检索器结论只基于
  命中/名次/分数分布形态，绝不比较绝对分数。

## 3. 主表（24 例分母：gold 非空且 gold ⊆ 角色允许池）

| 指标 | keyword-bm25-local-v1 | vector-hashed-ngram-v1 |
| --- | --- | --- |
| Recall@5 micro / macro | 1.0 / 1.0 | 1.0 / 1.0 |
| Hit@5（全部 gold 进 top5 的例占比） | 1.0 | 1.0 |
| gold 平均名次：可回答族（20 例） | **1.65**（最差第 3） | 1.90（最差第 3） |
| gold 平均名次：受限 supervisor 族（4 例） | 1.50 | **1.25** |
| gold 第 1 名次数（24 例中） | **10** | 9 |
| 平均时延（遥测） | 1.32 ms | 5.98 ms |
| restricted_candidate_count 合计 | 0 | 0 |
| cost_estimate_usd 合计 | 0.0 | 0.0 |

解读：keyword 在可回答族名次略优，vector 在受限族名次略优、rank-1 少 1 例；
两者差距都在 18-chunk 池的噪声量级内，**不构成单方面压倒性证据**——
真正的差异在无答案例的分数形态（§5），这是裁定 vector 晋级的主因。

## 4. 五类切片对比（48 例全覆盖；分母外切片只描述行为，不计入 recall）

| 切片 | 例数 | keyword 行为 | vector 行为 | 判读 |
| --- | --- | --- | --- | --- |
| 可回答 | 20（dev16+cal4） | gold 全进 top5；平均名次 1.65；rank-1 8 例 | gold 全进 top5；平均名次 1.90；rank-1 6 例 | 双双可用；keyword 名次略稳 |
| 无答案 | 8（dev6+cal2） | top-1 全部 billing-general-v3，分数 2.42–18.68 与可回答区间（11.31–25.10）大面积重叠 | top-1 分数全部 ≤ 0.290，与可回答例（≥ 0.304）**完全分离** | vector 提供可用的置信信号苗头（§5） |
| 冲突 | 8（dev4+cal4） | gold 对 0 chunk 不可见；top-1 全为 accepted 文档（BM25 9.11–29.77） | 同左（余弦 0.197–0.448） | 语料层设计边界（第 9 周冻结），检索层不可修；第 12 周必须显式处理单边误导 |
| 过期 | 8（dev4+cal4） | 过期文档 billing-general-v1 零观测 ✓；top-1 全为现行 v3/现行差价文档 | 同左 | **正确行为**：现行版本作为指针上浮，过期文档绝不作为现行依据出现 |
| 受限 | 10（agent 6 + supervisor 4） | agent 6 例受限文档零泄漏 ✓（top-1 均为有权文档，BM25 8.25–9.49）；supervisor 4 例 gold 全进 top5 | agent 6 例零泄漏 ✓（top-1 余弦 0.181–0.264 低分）；supervisor 4 例 gold 全进 top5，平均名次 1.25 | agent 例"零泄漏但低信号"符合升级语义；supervisor 对照可达 |

## 5. 置信信号（本周最有工程价值的发现，附警示）

vector 的 top-1 余弦把 **8/8 无答案例（≤0.290）与 20/20 可回答例（≥0.304）完全分开**
（受限 agent 例介于 0.181–0.264，同样低分）；keyword 的 BM25 分数无此分离
（无答案 2.42–18.68 vs 可回答 11.31–25.10，重叠过半）。
若第 12 周生成层要"无证据即拒答"，vector 分数是目前唯一可用的苗头。

**三条警示（如实记录）**：间距仅 0.014，阈值 0.30 类规则本周**不冻结**；
分离性由同一实现在 48 条上观察得出，无保留集验证（保留集第 12 周一次解封前不得用作运行时规则）；
本向量化是教学替身（blake2b 哈希，无语义泛化），结论不得外推到真实 embedding 服务。

## 6. 变更预算与校准纪律（如实执行记录）

- 开发集诊断后**受控修改执行 0 次（预算 0/1）**：诊断出的失败集中在
  ranking_context（需字段加权）与 corpus 层（需所有者裁决），都不是检索器单变量能单调修复的；
  贸然动 tokenizer/加权反而会作废已产出的全部结果文件。
- 校准集 12 条与开发集同轮一次产出，未做任何基于校准结果的迭代；
  第二遍运行仅用于确定性断言，不参与调优。
- 失败登记 6 条（开发 4 + 校准登记 2）全部七字段 + 五层归因，见
  `retrieval-failure-register-v1.md`；回归种子 12 条见 `retrieval-regression-seeds-v1.jsonl`。

## 7. 硬门与边界

| 硬门 | 结果 |
| --- | --- |
| support_agent 原始候选中受限 chunk 观测数 = 0 | ✓ 逐例 0（合计 0；边界：ACL 下推按构造保证，独立核对见测试 02） |
| 保留集未运行 | ✓ 12 条 holdout 零执行，结果文件无 NS-HOLD-*（测试 04） |
| 第 10 周 12 件冻结物未改动 | ✓ sha256 复核（CI 证据 §6） |
| 过期文档不作现行依据 / 冲突文档不出现在候选 | ✓ 池外 0 chunk（语料层保证，逐例复核） |

边界：检索被召回 ≠ 答案正确（生成层第 12 周裁决）；Recall@5=1.0 是 13/18-chunk 池的饱和值；
被召回的过期/冲突来源不记为检索成功；本报告不宣称 RAG 改善任务，也不宣称生产检索质量。

## 8. 与第 12 周的接口

vector-hashed-ngram-v1 晋级为第 12 周候选检索层；keyword-bm25-local-v1 保留为
对照与降级路径（种子 SR-01..06 已锁其行为）。移交给生成层合同的三件事：
(a) 拒答阈值候选区间 0.29–0.31（未标定，保留集验证后才可入合同）；
(b) 冲突 8 例的单边误导风险（top-1 高分 accepted 文档）必须在生成层显式拦截；
(c) 受限 agent 例的升级路径不得依赖检索层信号（检索层只保证零泄漏）。


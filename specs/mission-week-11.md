# 第 11 周任务书：keyword(BM25) vs 确定性哈希向量 —— 检索层基线比较

> 来源: `specs/business-background.md` 第 11 周章节（课程手册，v2026-10）；本文件为该章节在本仓库的
> 工程化改编记录。比较协议本体 = `week-11/retrieval-comparison-manifest-v1.json`（先冻结后运行）。
> 前置: 第 10 周 `week10-baseline`（60 例评测合同冻结 + 确定性基线 `northstar-app-w08-r0`）。

## 一、本周要支持的决定（预注册，非上线决定）

在冻结的 48 条开发+校准案例（36 开发 + 12 校准）上，比较两路零生成检索器——
关键词 BM25 与确定性哈希向量（blake2b→512 维）——**哪一路作为单路候选晋级第 12 周只读 RAG 评测**，
另一路降级为对照/降级路径。不可接受的失败（沿用第 10 周开除线）：普通客服看到主管受限事实；
无来源时给出貌似确定的答案；为过线事后改协议、删难题或降门槛。

## 二、学习合同

- 起点: 第 10 周冻结评测合同 + 第 9 周 ACL 候选 SQL（下推过滤）+ 第 8 周确定性引擎
- 预计投入: 8–10 小时，分 5 次完成
- 本周表现: 实现双检索器与统一 `retrieve()` 合同（候选池 = ACL 下推池），先冻结比较清单，
  再正式运行 48 例 × 2 检索器，产出逐例结果与裁定工件
- 本周不做: 不做生成（零 LLM 调用、零业务效果）；不运行保留集（12 条封存不动）；
  不用比较结果回头改评测集或语料；不把课程阈值写成行业/生产承诺；
  变更预算 0/1（每检索器一次正式运行，第二遍仅用于确定性断言）

## 三、比较协议（先冻结，后运行）

| 项 | 冻结值 |
| --- | --- |
| 比较清单 | `week-11/retrieval-comparison-manifest-v1.json`（三输入件 sha256 绑定：eval-set / labeling-guide / corpus-manifest） |
| 案例范围 | dev 36 + cal 12 = 48 例；holdout 12 例 `not_executed` |
| top_k | 5（固定，两检索器一致） |
| 检索器 A | `keyword-bm25-v1`：纯标准库 BM25（k1=1.5, b=0.75；CJK 单字+双字 + ASCII 词元） |
| 检索器 B | `vector-hashed-ngram-v1`：blake2b→512 维哈希向量（tf+L2 归一，余弦相似） |
| 定序 | 并列按 `(-score, chunk_id)` 确定性打破 |
| 主指标 | Recall@5（晋级门 ≥0.7）；辅助：gold 最佳名次、rank-1 计数、受限族名次、置信信号分布 |
| 分母口径 | Recall 分母 = 该例 gold 文档数（labeling-guide 公开标签附录） |
| ACL | 候选池 = `candidates_for(role)` 下推池；agent 受限候选逐例必须为 0 |

## 四、五天节奏

| 天 | 时间 | 动作 | 离开前必须有的证据 |
| --- | --- | --- | --- |
| 1 | 1–2h | 决定/比较对象/协议/非目标 | 比较清单草案 |
| 2 | 2h | 检索器合同层（BM25 + 哈希向量 + ACL 下推池） | retrieval/contract.py |
| 3 | 2–3h | 标签装载 + 比较运行器（确定性断言 + holdout 守卫） | 双跑通过 |
| 4 | 2h | 冻结清单 → 正式运行 → 逐例结果落盘 | 两个 results jsonl |
| 5 | 1–2h | 失败登记/回归种子/报告/裁定 + 工程门 | 四份裁定工件 + CI 证据 |

诚实出口: 若两检索器在可回答族上都无法稳定把 gold 送进 top-5，如实登记失败层并缩小第 12 周
范围，不为晋级门造数据。

## 五、交付物

`retrieval/`（contract / labels / run_retrieval_comparison）、
`week-11/retrieval-comparison-manifest-v1.json`、`week-11/keyword-results-v1.jsonl`、
`week-11/vector-results-v1.jsonl`、failure-register / regression-seeds / baseline-report /
retriever-decision 四份裁定工件、`tests/test_retrieval_comparison.py`（41 → 47）、
README 第 11 周段落、脱敏扫描 16 → 19 文件、ci-evidence §6 + `evidence/ci-run-w11.log`。

## 六、验收门（13 条，裁定前逐条核对）

1. 决定、比较对象、决策角色明确（§一 + retriever-decision §1）
2. 协议（top_k/指标/分母/定序/排除）先于正式运行冻结（manifest 三输入件 sha256 绑定）
3. 48 例 = dev 36 + cal 12；holdout 12 条零执行（结果文件逐行核对 split）
4. 双检索器逐例覆盖且字段 schema 统一（20 字段 × 96 行，测试逐字段断言）
5. ACL 下推：agent 受限候选逐例 0（测试用 candidates_for 独立复算，不轻信实现输出）
6. 确定性：排除 latency_ms 的规范投影两次运行逐行一致，且与落盘文件一致
7. 零生成零业务效果：无模型调用、cost_estimate_usd=0、无业务状态变更
8. 失败登记 ≥5 条、五层归因、与结果文件对账（实测 6 条 = dev 4 + cal 2）
9. 回归种子 8–12 条锁定裁定行为（实测 12 条 SR-01..12）
10. 裁定给出单路晋级 + 降级路径，证据含名次与置信信号，不单靠饱和 Recall@5
11. 类阈值（top-1 余弦 0.30）不冻结，注明须第 12 周保留集一次解封验证
12. 所有数字有分母、切片、版本来源（manifest + results + 报告口径节）
13. （工程门）全量测试 47/47、本地 CI 4 阶段 PASS、第 10 周冻结物 12 件程序化复核
    （口径勘误见冻结记录勘误节）、封存件未改

## 七、诚实边界

本周只证明"在冻结协议与小语料池上，两路零生成检索器的检索行为已逐例测量并据实裁定"。
不宣称端到端答案质量（第 12 周只读 RAG 评测）；Recall@5=1.0 为 13/18-chunk 小池饱和值，
不作为泛化能力声明；材料为教学合成（synthetic），保留集自我封存 ≠ 独立盲测；
教学替身边界三条警示如实写入 retriever-decision，不删除。

---

## 本周工程落地记录（实构建对参考模板的修正，如实记录）

### 环境约束下的选型

- 本机无 sqlite FTS5 扩展、无 numpy/sklearn → 两检索器均纯标准库实现
  （hashlib.blake2b / math / sqlite3 / json），不引入第三方依赖；
- BM25 词元：CJK 单字 + 双字 + ASCII 词（小语料下双字词元补偿分词缺失），k1=1.5, b=0.75；
- 哈希向量：blake2b→512 维 + tf 加权 + L2 归一，余弦相似；
- 并列分数按 (-score, chunk_id) 定序，保证两次运行逐字节一致。

### 统一合同与标签装载

- `retrieval/contract.py`：`retrieve(role, query)` 统一入口；候选池 = 第 9 周
  `candidates_for(role)` ACL 下推池（先过滤后候选语义不变）；agent 受限候选逐例 0；
- `retrieval/labels.py`：装载 labeling-guide 公开标签附录；修复模板未覆盖的紧凑
  case_id 写法（NS-DEV-01/02/10 展开）；
- `retrieval/run_retrieval_comparison.py`：双跑确定性断言（投影排除 latency_ms）、
  holdout 守卫（split=holdout 拒绝执行）、切片汇总输出。

### 实测结果

- 正式运行 48 例 × 2 检索器一次通过；restricted_candidate_count 逐例 0；
  holdout 零执行；cost_estimate_usd=0；
- Recall@5 双检索器均 1.0（≥0.7 晋级门达标，但为 13/18-chunk 小池饱和值）；
- 名次区分度：可回答族 gold 均值名次 kw 1.65 vs vec 1.90；受限族 vec 1.25 更优；
  rank-1 计数 kw 10 vs vec 9；
- 置信信号：vector top-1 余弦把 8/8 无答案例（≤0.290）与 20/20 可回答例（≥0.304）
  完全分离；BM25 得分区间重叠过半、无域外信号；
- 裁定：`vector-hashed-ngram-v1` 单路晋级第 12 周；keyword 为降级路径
  （SR-01..06 锁其行为）；阈值 0.30 类规则不冻结，须第 12 周保留集一次解封验证。

### 失败登记（6 条 = dev 4 + cal 2，五层归因）

- 冲突对/过期文档 0 chunk 是第 9 周语料层设计，检索层不可见不可修——登记为
  corpus 层失败（最诚实一条）；
- 无答案族 BM25 无域外信号属 retrieval 层；
- 初稿 2 处与实况不符（名次/族归属）已对照结果文件纠正。

### 工程门实测

- 全量测试 47/47（41 → 47，新增 6 用例：清单 sha256 互锁 / ACL 零泄漏独立复算 /
  确定性重跑 / holdout 未跑 / 失败登记对账 / 回归种子）；
- 本地 CI 四阶段 PASS（47 tests / 19 文件 45 输出点 0 违规 / 冒烟 4/4；
  `evidence/ci-run-w11.log`）；评测集校验器 verdict=VALID；
- 第 10 周冻结物 12 件程序化复核：11 件与工作区字节一致；jsonl 与提交 blob 一致
  （工作区行尾被测试重写，内容逐字节同）；mission-week-10.md 原哈希过期
  → 冻结记录勘误更正（见该文件勘误节）；
- 变更预算 0/1 执行；校准集与开发集同轮一次产出。

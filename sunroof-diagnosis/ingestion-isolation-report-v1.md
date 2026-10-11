# 摄取隔离与确定性构建报告 v1 —— sunroof-corpus-w09-v1

> 回链 specs/mission-week-09.md 第 3/4 天；证据：`corpus/build_corpus.py` 两次独立构建
> 逐字节一致 + `tests/test_corpus_governance.py` 用例 01–04。

## 1. 两次构建确定性（类比第 6 周批次重跑）

同一配置对两个不同临时库各构建一次，再对其中一库原样重建第三次：

| 比对项 | 结果 |
| --- | --- |
| documents / chunks 清单条目（全字段） | 三次完全一致 |
| corpus-manifest-v1.json 两次落盘 | SHA256 逐字节一致（清单不含墙钟时间，血缘以 `corpus-batch-w09-v1` 为锚） |
| 同库重建幂等 | 语料两表按快照 DELETE 后重插，终态不变 |

## 2. 五态路由实况（8 份来源）

| 状态 | 文档 | 依据 |
| --- | --- | --- |
| accepted (2) | service-general-v3、torque-east-v5 | 四要件齐备，普通分类 |
| accepted_restricted (1) | claim-supervisor-v2 | 四要件齐备，主管专属分类 |
| expired (1) | service-general-v1 | 谱系键 service-general 被 v3 的 supersedes 声明取代（非时间戳） |
| conflict_review (2) | retrofit-bulletin-v2、retrofit-notice-v4 | 冲突组 retrofit-oem-a-2026-04 双现行，内容相悖（当场答复 vs 一律转复核） |
| quarantined (2) | quick-note、special-claim-copy | 缺全部四要件；复制件另含「来源不明」，宁可隔离不猜 |

## 3. 隔离语义（默认隔离 quarantine-by-default）

- 缺任一要件的材料**不按「公开且有效」处理**，正文永不产出 chunk（隔离件/过期件/冲突件 chunk_count 恒为 0）；
- 过期文档**保留**在快照中（状态治理，非物理删除），供历史审计但不可作为候选；
- 冲突对保留在快照供第 10–12 周测试「系统能否识别冲突」，所有者裁决前不得作为任何答案依据。

## 4. chunk 构成（fixed-heading-chunker-v1）

| 文档 | chunks | 说明 |
| --- | --- | --- |
| service-general-v3 | 8（s00–s07） | s06「质量主管例外条款（仅限质量主管处理）」段标题含标记 → 收紧为主管专属 |
| torque-east-v5 | 6（s00–s05） | 全部普通段 |
| claim-supervisor-v2 | 4（s00–s03） | 文档级主管专属，全段继承 |
| 合计 | **18 = 13 普通 + 5 受限** | 隔离/过期/冲突件贡献 0 |

## 5. 诚实边界

- 本报告证明「语料可被治理成可重复构建的快照」，不证明检索质量；
- 确定性比对范围是本仓库 8 份合成来源，不外推真实共享盘规模。

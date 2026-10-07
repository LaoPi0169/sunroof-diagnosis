# 摄取与隔离报告 v1（Ingestion / Isolation Report）

- **快照**: `northstar-corpus-w09-v1`（builder: `corpus/build_corpus.py`，
  parser: `markdown-section-parser-v1`，chunker: `fixed-heading-chunker-v1`）
- **数据入口**: 复用第 6 周幂等摄取机制的精神（按快照幂等重建、血缘列
  `source_version` / `source_owner` / `batch_id`、批内确定性排序）；
  语料两表（`corpus_documents` / `corpus_chunks`）为语料子系统自有 schema
  （`corpus/corpus_schema.sql`），叠加在冻结底座（001–003）之上，**不触碰 policies 冻结面**
- **batch**: `corpus-batch-w09-v1`（血缘/对账锚点；确定性构建不含墙钟时间）

## 1. 状态路由结果（8 份来源，实测）

| 文档 ID | 状态 | 摄取动作 | 隔离/标记理由（构建器 route_reason） |
| --- | --- | --- | --- |
| billing-general-v3 | accepted | 入库 + 8 chunks（7 普通 + 1 受限段） | 合同字段齐备; 谱系现行; 无并行冲突来源 |
| price-difference-east-v5 | accepted | 入库 + 6 chunks | 合同字段齐备; 谱系现行; 无并行冲突来源 |
| refund-supervisor-v2 | accepted_restricted | 入库 + 4 chunks（restricted） | 合同字段齐备; 受限分类 (主管专属) |
| billing-general-v1 | expired | 入库（0 chunks，不参与候选） | 谱系取代: billing-general-v3 的 supersedes 包含本文件谱系键 |
| upgrade-east-v2 | conflict_review | 入库（0 chunks） | 冲突组 upgrade-east-2026-04 存在并行现行来源: upgrade-notice-v4 |
| upgrade-notice-v4 | conflict_review | 入库（0 chunks） | 冲突组 upgrade-east-2026-04 存在并行现行来源: upgrade-east-v2 |
| quick-note.txt | quarantined | 隔离区（0 chunks） | 必填合同字段缺失: owner/ACL/版本/有效期 |
| special-refund-copy.md | quarantined | 隔离区（0 chunks） | 必填合同字段缺失（正文疑似主管条款但来源边界不明，不能自证权限） |

计数: documents_total=8（accepted 2 / accepted_restricted 1 / expired 1 /
conflict_review 2 / quarantined 2）；chunks_total=**18**
（internal-general 13 / internal-restricted 5）。全部 5 个受限 chunk:
`refund-supervisor-v2#s00–s03` + `billing-general-v3#s06`（混合权限段，最严格规则收紧）。

## 2. 确定性比对（同一输入 + 配置，构建两次）

| 比对项 | 第 1 次构建 (临时库 A) | 第 2 次构建 (临时库 B) | 一致 |
| --- | --- | --- | --- |
| 文档 ID 集合 | 8 | 8 | ✅ |
| chunk ID 集合 | 18 | 18 | ✅ |
| 内容摘要 + 权限元数据 | — | — | ✅ 逐项相同 |
| 清单文件（全量 JSON） | — | — | ✅ **逐字节相同**（sha256 相同） |

- 复现命令: 两次 `python corpus/build_corpus.py --db <临时库> --manifest <临时输出>`，
  两份清单 `Get-FileHash` 完全一致；仓库根 `corpus-manifest-v1.json` 与全新重建
  **逐字节一致**（整体 sha256 `135f773cbd4aeb55be23bb95c926df7e
  e9ee68f5d405327b8bb976814baefeb2`）。
- 确定性设计: 清单**不含墙钟时间**（`built_at` 类字段不存在的有意决策），
  血缘以 `batch_id` 为锚；文档/chunk 处理顺序按 document_id 排序固定。
- 幂等性: 同一库重复构建 = 按快照 DELETE 后重建，终态不变（类比第 6 周批次重跑）。

## 3. 与第 6 周机制的对账口径

| 第 6 周机制（policies 摄取） | 第 9 周对应（语料摄取） | 实测证据 |
| --- | --- | --- |
| 缺要件行 100% 进隔离区（默认拒绝） | 缺合同字段文档 100% → quarantined | quick-note / special-refund-copy，missing_fields 落库 |
| 批次幂等: 重跑不产生第二个业务结果 | 按快照幂等重建: 终态不变 | 两次构建 + 同库重跑，清单逐字节一致 |
| 血缘列 source_version / owner_id / batch_id | 语料表同名语义血缘列 | corpus_documents 全部 8 行可追溯 |
| 隔离→复核→重放需有权角色 | quarantined/conflict/expired → accepted 只能通过**修订合同 + 重建新快照** | 路由状态机 §3（ACL 矩阵）；不允许在旧快照内静默改权限 |
| 无效内容不进入运行时查询面 | 非 accepted* 文档 chunk 恒为 0 | 8 份中 5 份 chunk=0；role-candidate-check L2 断言 |

## 4. 与第 6/8 周冻结面的边界（工程落地记录）

- **schema 归属**: 语料两表不走 `application/database.py::MIGRATION_SCRIPTS` 清单 ——
  该清单与 `release-manifest-v1.json`（schema_version=003）及 week08 冻结测试绑定，
  语料层若加入其中会破坏"发布清单 = 冻结基线"的绑定语义。故语料 schema 独立存放于
  `corpus/corpus_schema.sql`，由构建器在 `init_db()` 底座（001–003，含 policies/tasks 冻结面）
  之上叠加执行：一个库同时承载冻结面与语料面，冻结面零改动。
- **permissions 列存储**: `allowed_roles` / `supersedes` / `missing_fields` 以 JSON 文本存储，
  候选查询用 SQLite `json_each` 把 ACL 谓词下推到 WHERE（见 role-candidate-check-v1）。

## 5. 真实修正记录（本轮执行中发生，如实记录）

1. **混合权限段漏收紧（已被流程抓住）**: 首轮构建把 `billing-general-v3#s06`
   （主管例外条款段，标记在段标题中）按文档级 general 处理，agent 候选为 14。
   由"构建数 vs 本矩阵预期（13 个普通段）"对账发现，修正 chunker 的段级 ACL 检查范围
   为"标题 + 正文"后复测为 13。随后治理测试固化精确候选集合断言。
   教训: **权限元数据偏差必须靠对账与自动化断言发现，不能靠肉眼。**
2. **清单去墙钟**: 初稿 schema 曾含 `ingested_at` 列；为满足"两次构建逐字节一致"，
   改为以 `batch_id` 为摄取锚点，删除墙钟列。
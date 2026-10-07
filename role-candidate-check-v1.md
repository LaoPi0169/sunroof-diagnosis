# 角色候选检查 v1（Role Candidate Check，模型调用前执行）

- **检查方式**: 现有系统确定性 SQL 候选查询（`corpus/check_role_candidates.py`），
  ACL 下推到查询层 WHERE 条件 —— **先过滤后出候选**，不做"全库召回再过滤"
- **零模型调用**: 本检查不涉及任何 embedding / LLM；候选 = 允许进入后续检索的 chunk 集合
- **快照**: `northstar-corpus-w09-v1`；库: `data/corpus-w09.db`（冻结底座 001–003 + 语料两表）

## 候选查询（权限谓词在 SQL WHERE 内）

```sql
SELECT cc.chunk_id, cc.document_id, cc.classification, cd.status AS document_status
FROM corpus_chunks AS cc
JOIN corpus_documents AS cd
  ON cd.document_id = cc.document_id AND cd.corpus_snapshot = cc.corpus_snapshot
WHERE cc.corpus_snapshot = ?
  AND cd.status IN ('accepted', 'accepted_restricted')   -- 状态过滤 (L2)
  AND cc.status IN ('accepted', 'accepted_restricted')   -- 双保险
  AND EXISTS (SELECT 1 FROM json_each(cc.allowed_roles) AS je
              WHERE je.value = ?)                        -- ACL 谓词 (按角色下推)
ORDER BY cc.chunk_id
```

## 实测结果（2026-10-07，`week_verdict: PASS`）

| 身份 | 原始候选数 | by_classification | 实测范围 |
| --- | --- | --- | --- |
| support_agent（token_agent_valid） | **13** | internal-general × 13 | billing-general-v3#s00–s05、s07（8 减 1 受限段）+ price-difference-east-v5#s00–s05 |
| supervisor（token_supervisor_secret） | **18** | internal-general × 13 + internal-restricted × 5 | 上述 13 + billing-general-v3#s06（主管例外段）+ refund-supervisor-v2#s00–s03 |
| 未知角色（unknown_role） | **0** | — | 无可靠候选（沿用 401/默认拒绝语义） |

## 泄漏断言（任一触发 = 本周失败）

| 断言 | 内容 | 实测 |
| --- | --- | --- |
| L1 | support_agent 原始候选中不得出现任何主管专属 chunk（classification != internal-general） | ✅ 0 出现 |
| L2 | 任何角色候选中不得出现非 accepted* 文档的 chunk | ✅ 0 出现（快照中 expired/conflict_review/quarantined 文档 chunk 恒为 0，双保险） |
| L3 | 未知角色必须零候选 | ✅ 0 |

## 判定

- **失败定义**: 普通客服原始候选中出现任何一个主管受限 chunk → 本周直接失败；
  禁止继续做 embedding，禁止用提示词遮盖。本次结果：**通过（0 泄漏）**。
- 混合权限 chunk 处理：采用最严格权限（`fixed-heading-chunker-v1` 的 chunk-strictest 规则，
  段标题/正文含「仅限主管」标记 → supervisor 专属）；本快照中 `billing-general-v3#s06`
  （主管例外条款段）即由此规则从文档级 general 收紧为段级 restricted。

## 检查器增强记录（真实修正，如实记录）

首轮实测中 `billing-general-v3#s06`（主管例外段）被计入 support_agent 候选（14 个）——
原因：该段「仅限主管」标记在**段标题**里，而首轮 chunker 只扫描段正文。
由构建数与 knowledge-owner-acl-matrix-v1 预期（13 个普通段）对账发现，修正为
"段级 ACL 检查范围 = 标题 + 正文"后复测：agent 13 / supervisor 18 / unknown 0。
随后在 `tests/test_corpus_governance.py` 固化**精确候选集合断言**（含 s06 排除），
防止该类偏差再次静默通过。

# 角色候选检查 v1（Role Candidate Check，模型调用前执行）

- **检查方式**: 现有系统确定性 SQL 候选查询（corpus/check_role_candidates.py），
  ACL 下推到查询层 WHERE 条件 —— 先过滤后出候选，不做「全库召回再过滤」
- **零模型调用**: 本检查不涉及任何 embedding / LLM；候选 = 允许进入后续检索的 chunk 集合
- **实测**: `python corpus/check_role_candidates.py --db data\corpus-w09.db` →
  week_verdict=PASS（0 泄漏，退出码 0）

| 身份 | 可以出现在原始候选 | 不能出现（实测零出现） |
| --- | --- | --- |
| pqe（一线，token_pqe_oem_a/b） | service-general-v3 普通段 7（s00–s05、s07）+ torque-east-v5 全部 6 段，共 13 | claim-supervisor-v2 ✅ / service-general-v3#s06 ✅ / expired ✅ / conflict_review ✅ / quarantined ✅ |
| quality_supervisor | 上述 13 + 主管专属 5（claim-supervisor-v2 4 段 + service-general-v3#s06），共 18 | expired ✅ / conflict_review ✅ / quarantined ✅ |
| 未知/缺失 token | 无可靠候选（0，沿用 401 默认拒绝语义） | 所有受限正文与敏感元数据 ✅ |

## 泄漏断言（任一触发 = 本周直接失败）

- **L1**: pqe 原始候选中出现任何主管专属 chunk（classification != internal-general）→ 失败；
- **L2**: 任何角色的候选中出现非 accepted* 文档 chunk → 失败（双保险）；
- **L3**: 未知角色获得候选 → 失败。
- 实测三项全部通过；`service-general-v3#s06`（段标题含「仅限质量主管」标记）被标题+正文
  双扫描正确收紧，**不在** pqe 的 13 项候选中（tests/test_corpus_governance.py 固化精确集合断言）。

## 判定

- **失败定义**: pqe 原始候选中出现任何一个主管受限 chunk → 本周直接失败；
  禁止继续做 embedding，禁止用提示词遮盖。本次结果：**通过（0 泄漏）**。
- 混合权限 chunk 处理：采用最严格权限（段标记 → 仅 quality_supervisor）；
  回到来源边界重新切分作为备选手段记录在案（本快照未需使用）。

## 诚实边界

- 候选检查只验证「权限谓词下推到 SQL 的过滤正确性」，不验证检索相关性；
- 本检查在单一 SQLite 库上执行，不外推分布式检索或真实 embedding 服务的 ACL 行为。

"""北辰 Week 11 检索比较包 —— 统一检索合同 (keyword BM25 vs 确定性哈希向量)。

边界: 只检索不生成; ACL 下推 (先过滤后候选, 复用第 9 周 CANDIDATE_SQL);
零业务效果; 比较配置与清单见 week-11/retrieval-comparison-manifest-v1.json
(先冻结后运行); 保留集不解封不运行。
"""

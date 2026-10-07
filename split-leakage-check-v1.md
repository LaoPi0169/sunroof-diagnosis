# Split 泄漏检查记录 v1（split-leakage-check-v1）

检查时点: 2026-10-07（第 10 周第 4 天，基线运行前）
工具: `python evaluation/validate_eval_set.py`（确定性检查，零模型调用）
数据集: `northstar-eval-w10-v1`（eval-set-v1.jsonl，60 行）

## 检查口径

1. **近重复**: query 归一化（去除空白、中英文标点、统一小写）后必须全数据集唯一；
   同 split 重复 → 模板复用未换题；跨 split 重复 → **泄漏**（保留集失效）
2. **封存边界**: 输入行不得携带任何标签字段
   （gold_document_ids / forbidden_document_ids / required_facts / forbidden_facts /
   expected_state）；标签只存在于封存件（保留集）与标注指南附录（开发/校准）
3. **矩阵完备**: 36/12/12 与 24/10/8/8/10 及族×split 细分精确成立（排除"借案例凑数"）
4. **语料引用**: 标签 gold/forbidden 与类别参考文档必须存在于 corpus-manifest-v1.json

## 实测结果（2026-10-07）

- rows=60；归一化 query 唯一性 60/60 通过；跨 split 近重复 0 例
- 封存边界: 输入行标签字段 0 次出现
- 矩阵: development {answerable 16, no_answer 6, conflict 4, expired_only 4, restricted 6}；
  calibration {4,2,2,2,2}；holdout {4,2,2,2,2} —— 与冻结矩阵逐格一致
- 语料引用: 类别参考文档 6 组全部可解析；封存标签 gold/forbidden 全部可解析
- 校验器退出码 0（verdict=VALID）

## 结论与备注

60 条案例间无近重复泄漏，保留集输入与开发/校准集文本层面零重叠，标签封存边界完整。
保留集文本层面的隔离不等于统计独立性（同一模板族分布）；周报须声明
"自我封存 ≠ 独立盲测"。后续每次重跑基线或校验器，本记录随 sha256 一起入冻结记录。

# split-leakage-check-v1 —— 第 10 周泄漏检查记录

- 检查对象: `eval-set-v1.jsonl`（60 例输入行）× `eval-holdout-labels-sealed-v1.json`（封存）
- 检查工具: `evaluation/validate_eval_set.py`（V4 近重复/泄漏规则，确定性）
- 检查日期: 2026-10-10（冻结前执行；冻结后由 tests/test_eval_contract.py 持续把守）

## 检查项与结果

| # | 检查项 | 口径 | 结果 |
| --- | --- | --- | --- |
| 1 | 同 split 近重复 | 归一化 query（去空白标点小写）同 split 内零重复 | PASS（60/60 唯一） |
| 2 | 跨 split 泄漏 | 归一化 query 跨 dev/cal/holdout 零相同 | PASS（0 条） |
| 3 | 封存边界 | 输入行（含 holdout 行）不得携带任何标签字段 | PASS（gold/forbidden/facts/expected_state 零出现） |
| 4 | 看过的案例标保留集 | holdout 案例必须为新作问句，不得由 dev/cal 改写复用 | PASS（12 例全部新作，见第 2 项） |
| 5 | 标签键对应 | 封存标签键与 holdout case_id 一一对应 | PASS（12/12） |
| 6 | 语料引用可解析 | 类别参考文档 / gold / forbidden 全部在 sunroof-corpus-w09-v1 清单 | PASS（全部可解析） |

## 方法说明

- 归一化: `re.sub(r"[\s，。、,\.?？!！：:；;（）()\-—]", "", text).lower()`；
- 结论: verdict=VALID（退出码 0），findings=[]；无跨 split 泄漏，无近重复水分；
- 残余风险: 语义级近义（非字面）不在本检查口径内，靠标注规则 1-5 的判例一致性
  控制；若第 11/12 周发现语义级泄漏，按合同在勘误节登记并重考，不静默改集。

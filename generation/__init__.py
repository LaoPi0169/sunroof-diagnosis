"""只读 RAG 候选包 (Week 12) —— 回链 eval-plan-v1.md §5 第 3-7 层。

generation.contract  : 三态回答合同 + 确定性门控 + 抽取式生成替身 + 引用验证
generation.evaluate  : 期望状态映射 / 事实校验规则 / 七层指标聚合
generation.run_rag_evaluation : 评测运行器 (先冻结 manifest 后解封保留集)
"""

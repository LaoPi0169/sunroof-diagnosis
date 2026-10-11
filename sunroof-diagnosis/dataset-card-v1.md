# dataset-card-v1 —— sunroof-eval-w10-v1

| 项 | 值 |
| --- | --- |
| 版本 | `sunroof-eval-w10-v1`（冻结于第 10 周，阶段 B 节点 ⑦） |
| 语料绑定 | `sunroof-corpus-w09-v1`（第 9 周快照，corpus-manifest-v1.json） |
| 基线绑定 | `sunroof-app-w08-r0`（release-manifest-v1.json） |
| 规模 | 60 例 = 36 开发 + 12 校准 + 12 保留（保留标签封存） |
| 矩阵 | 可回答 24 / 无答案 10 / 冲突 8 / 过期 8 / 权限受限 10 |
| 角色 | pqe（OEM-A 一线）/ quality_supervisor（跨项目只读）；无 unknown_role 案例 |
| 类别 | torque_retorque_east / service_general_response / outside_corpus / retrofit_conflict / expired_reference / restricted_claim（六类别 ↔ 状态族一一对应） |
| 材料 | 全 synthetic（教学合成），无真实客户正文；来源为第 9 周治理快照 8 份文件 |
| 标签 | dev+cal 48 例公开（labeling-guide-v1.md 附录）；holdout 12 例封存（eval-holdout-labels-sealed-v1.json） |

## 已知局限（如实）

1. 60 例由模板实例化 + 人工复核 + 5 例角色改判产出（改判记录见
   specs/mission-week-10.md），不是独立标注者双标——标注一致性为自检口径；
2. 保留集为自我封存，不等于独立盲测；
3. query 覆盖 18 个冻结 chunk 的高频口径，不追求话题均匀；
4. 无答案族案例的"语料外"判定以本快照为准，语料版本升级须重考；
5. 课程数量与阈值为教学口径，不是行业标准或生产门槛。

## 用途边界

- 允许: 第 11 周检索比较（dev+cal 48 例）、第 12 周只读 RAG 评测（解封一次）、
  第 16 周离线金丝雀重考；
- 禁止: 用评测结果反改语料或标签（发现 gold 缺失 → 新建语料版本）、
  在数据集背后改文档、提前解封保留集。

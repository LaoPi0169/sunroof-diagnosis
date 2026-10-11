# Corpus Card v1 —— sunroof-corpus-w09-v1

- **服务的任务**: 一线 PQE 核对「当前、适用、且自己有权查看」的售后/扭矩/索赔依据
  （衔接节点 ② 规格核查引擎与节点 ④ 任务闭环，不是通用知识库）
- **来源从哪里来**: 天窗售后知识负责人提供的八份材料（教学合成，synthetic=true）
- **谁负责**: 售后工程组（服务总则）/ 扭矩标准组（东区细则）/ 索赔流程组（主管例外）/
  改装工程组（OEM-A 改装文件）；权限确认归 IT/安全角色；见 knowledge-owner-acl-matrix-v1
- **快照日期与构成**: 语料快照 `sunroof-corpus-w09-v1`（批次 corpus-batch-w09-v1）；
  8 份文档 → 2 accepted / 1 accepted_restricted / 1 expired / 2 conflict_review /
  2 quarantined；18 个 chunks（13 普通 + 5 受限）
- **怎样更新和下线**: 新版本通过 supersedes 关系进入；下线置 expired 而非物理删除
  （保留状态与来源记录，但不得作为可靠答案）；缺要件新材料默认隔离；
  冲突对须所有者裁决出新版本，不得在旧快照内静默改判
- **有哪些角色**: pqe（一线）/ quality_supervisor（质量主管）（映射见 ACL 矩阵 §0）；
  未知角色无候选
- **哪些状态不允许作为回答依据**: expired、conflict_review、quarantined
  （conflict 材料仅可用于后续测试系统能否识别冲突）
- **当前材料不能代表哪些真实分布**: 真实共享盘的文档噪声、多业务单元 ACL、
  OEM 项目组维度的语料级权限、真实更新延迟与人工流程抖动；结论仅代表本合成快照的治理能力
- **与冻结底座的关系**: 语料两表由 corpus/corpus_schema.sql 叠加在 init_db()
  （迁移 001–004）之上，同一库同时承载冻结面与语料面，冻结面零改动
  （release-manifest schema_version=004 不变）

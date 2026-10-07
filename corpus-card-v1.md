# Corpus Card v1 —— northstar-corpus-w09-v1

- **服务的任务**: 一线客服核对"当前、适用、且自己有权查看"的计费/升级政策依据
  （衔接第 5 周确定性政策引擎与第 7 周任务闭环，不是通用知识库）
- **来源从哪里来**: 北辰知识负责人提供的八份政策材料（教学合成，`synthetic=true`，
  全部落盘于 `corpus/sources/`，逐份带 sha256 内容摘要，见 corpus-manifest-v1.json）
- **谁负责**: 费率组（billing-general、price-difference-east 谱系）/ 退款组（refund-supervisor 谱系）/
  渠道组（upgrade 谱系）；权限确认归 IT/安全角色；逐份合同见 knowledge-owner-acl-matrix-v1
- **快照日期与构成**: 2026-10-07；8 份文档 → 2 accepted / 1 accepted_restricted / 1 expired /
  2 conflict_review / 2 quarantined；**18 个 chunks**（13 internal-general + 5 internal-restricted）；
  batch `corpus-batch-w09-v1`；清单整体 sha256 `135f773cbd4aeb55be23bb95c926df7e`
  `e9ee68f5d405327b8bb976814baefeb2`
- **解析与分块**: `markdown-section-parser-v1` + `fixed-heading-chunker-v1`
  （heading_level=2, acl_inheritance=chunk-strictest）；chunk 原子继承文档身份与 ACL，
  混合权限段取最严格权限（实测案例: billing-general-v3#s06）
- **怎样更新和下线**: 新版本通过 `supersedes` 谱系关系进入（如 billing-general-v3 取代
  billing-general 谱系旧版 → 旧版置 expired）；下线置 expired 而非物理删除（保留状态与来源记录，
  但不得作为可靠答案）；缺要件新材料默认隔离（quarantined）
- **有哪些角色**: `support_agent` / `supervisor`（映射见 knowledge-owner-acl-matrix-v1 §4）；
  未知角色无候选（401/默认拒绝语义不变）
- **哪些状态不允许作为回答依据**: `expired`、`conflict_review`、`quarantined`
  （conflict 材料仅保留在快照中供第 10–12 周测试系统能否识别冲突，不得作为可靠唯一答案）
- **可重复构建**: 同一输入两次构建逐字节一致（含清单）；复算方式:
  `python corpus/build_corpus.py --db <任意临时库> --manifest <任意输出路径>`，
  输出清单与仓库根目录 corpus-manifest-v1.json 逐字节相同（确定性，无墙钟时间）
- **当前材料不能代表哪些真实分布**: 真实共享盘的文档噪声、多业务单元 ACL、
  真实更新延迟与人工流程抖动；结论仅代表本合成快照的**治理能力**，
  不代表检索质量，更不代表"RAG 改善任务"（留给第 10–12 周评测）

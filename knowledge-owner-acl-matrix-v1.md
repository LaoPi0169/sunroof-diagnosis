# 知识所有者与 ACL 矩阵 v1（Corpus Contract）

- **合同版本**: knowledge-owner-acl-matrix-v1（2026-10-07，对应快照 `northstar-corpus-w09-v1`）
- **机器可读副本**: `corpus/build_corpus.py::CORPUS_CONTRACTS`（构建器的唯一合同来源，
  与本文逐字段一致；偏差 = 治理事故）
- **教学模拟声明**: 所有者姓名/角色均为课程合成（`synthetic=true`），无真实客户正文

## 1. 来源-责任矩阵（谁能确认什么）

| 材料类型 | 谁能确认正文 | 谁能确认权限 | 谁能确认有效期 | 信息不足时的动作 |
| --- | --- | --- | --- | --- |
| 普通计费政策（billing-general / price-difference-east 谱系） | 费率组 | IT / 安全角色 | 费率组 | 隔离 |
| 主管例外政策（refund-supervisor 谱系） | 退款组（主管流程所有者） | IT / 安全角色 | 退款组 | 隔离 |
| 群通知 / 渠道发布（upgrade 谱系） | 发布者只能证明发过消息 | 不能自行确认 | 渠道组 + IT/安全 | 标记冲突或隔离 |
| 个人笔记 / 群里转发的复制件 | 笔记作者只能解释用途 | 不能授予组织权限 | 不能确认 | 隔离 |

这张表防止"文件在共享盘里，所以应该能用"的推断：**来源边界不明 → 默认隔离**。

## 2. 八份材料 × 文档级合同（与构建器 CORPUS_CONTRACTS 一致）

| 文档 ID | 来源文件 (sha256 前 12 位) | 所有者 | 版本 | 生效期 | 谱系/冲突 | 分类 | ACL | 路由状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| billing-general-v3 | `89816599ee3c…` (2118B) | 费率组·赵倩 | v3.2 | 2026-01-01 起 | supersedes 谱系 billing-general | internal-general | agent+supervisor | **accepted** |
| price-difference-east-v5 | `58d5f78ccdeb…` (1220B) | 费率组·赵倩 | v5.0 | 2026-03-01 起 | supersedes 谱系 price-difference-east | internal-general | agent+supervisor | **accepted** |
| refund-supervisor-v2 | `74b00b36d7c2…` (919B) | 退款组·孙敏 | v2.1 | 2026-02-01 起 | supersedes 谱系 refund-supervisor | internal-restricted | 仅 supervisor | **accepted_restricted** |
| billing-general-v1 | `f7e5c6c5da3d…` (573B) | 费率组·赵倩 | v1.0 | 2025-01-01 起 | 被 v3 按谱系取代 | internal-general | agent+supervisor | **expired** |
| upgrade-east-v2 | `c081b207809f…` (494B) | 渠道组·周锐 | v2.0 | 2026-04-01 起 | 冲突组 upgrade-east-2026-04 | internal-general | agent+supervisor | **conflict_review** |
| upgrade-notice-v4 | `11c24f644c01…` (479B) | 渠道组·周锐 | v4.0 | 2026-04-15 起 | 冲突组 upgrade-east-2026-04 | internal-general | agent+supervisor | **conflict_review** |
| quick-note.txt | `6bd1fcb3e8cc…` (180B) | 无正式所有者 | 无 | 无 | — | 无 | 无 | **quarantined** |
| special-refund-copy.md | `db992a4f0d36…` (466B) | 正文疑似主管条款但来源丢失 | 无 | 无 | — | 声称受限但不可自证 | 无 | **quarantined** |

隔离件缺失字段（missing_fields，构建器实测记录）:
`quick-note` 与 `special-refund-copy` 均缺 `source_owner` / `allowed_roles` / `source_version` /
`effective_at` 四项。special-refund-copy 即使正文"看起来正确"、分类"声称受限"，
也**不能自证权限** —— 复制件必须走正式渠道重发后重新签订合同。

## 3. 路由状态机（默认规则：缺任一要件 = 隔离）

```
来源文件落盘 corpus/sources/
  ├─ 必填合同字段 (source_owner / allowed_roles / source_version / effective_at) 缺失?
  │       是 → quarantined（记录 missing_fields，chunk 恒为 0）
  │       否 ↓
  ├─ 本文件谱系键 (lineage_key) 出现在其他文件的 supersedes 里?
  │       是 → expired（与文件落盘时间戳无关，chunk 恒为 0）
  │       否 ↓
  ├─ conflict_group 与另一份现行来源相同?
  │       是 → conflict_review（两份同时标记，chunk 恒为 0，保留在快照供第 10–12 周识别冲突）
  │       否 ↓
  └─ classification == internal-restricted?
          是 → accepted_restricted（仅 supervisor）
          否 → accepted
```

**chunk 级附加规则（fixed-heading-chunker-v1, acl_inheritance=chunk-strictest）**:
chunk 原子继承文档身份与 ACL；段标题或正文含「仅限主管」标记 → 该段取最严格权限
（supervisor 专属），不允许混合段降权 —— 实测案例: billing-general-v3#s06。

## 4. 角色口径映射

| 手册口径 | 仓库会话角色 | 候选可见范围（实测见 role-candidate-check-v1） |
| --- | --- | --- |
| support-agent | support_agent（token_agent_valid） | 仅 accepted 普通 chunks（13 个） |
| support-supervisor | supervisor（token_supervisor_secret） | accepted + accepted_restricted（18 个） |
| 未知角色 | 其他/缺失 token | 无可靠候选（401/默认拒绝语义不变，实测 0） |
在《FDE 成长手册》的 24 周标准路线中，**第 9 周的名称是"建立有来源、有版本、有权限的 RAG 语料"**。

这一周的底层逻辑是：**RAG（Retrieval-Augmented Generation，检索增强生成）的第一步不是选向量库，
而是决定系统允许依据什么、对谁可见、何时失效、冲突由谁处理**。第 8 周冻结的确定性基线
（`week08-baseline`）已经证明：服务端授权、幂等、审计与部署门可以在零模型调用下三向可对账。
第 9 周要证明的是：在引入任何检索或生成能力**之前**，语料本身先成为被治理的对象——每份文档、
每个分块（chunk）都带来源、所有者、版本、生效期与访问控制列表（ACL）；缺任一要件的材料默认隔离，
受限内容在进入模型上下文之前就被确定性排除。**提示词不能把一次越权读取变成安全读取**，
所以权限必须下沉到语料与查询层，而不是留给模型"注意权限和日期"。

完成第 9 周，意味着正式开启**"第 9–12 周：有来源、有版本、有权限的 RAG 语料与评测"**阶段的基石：
一份可重复构建的语料快照（corpus snapshot）。它既不选"最佳"文本表示（embedding）、不调向量检索、
不生成回答，也不把"语料可构建"外推成"RAG 会改善任务"。

以下是用白话文详细拆解的"要做什么"、"怎么做（5 天步骤）"、"交付物有哪些"以及"验收标准"：

---

### 一、 我要做什么？（核心任务）

1. **复核 RAG 准入并比较可选路径（Option Note）**：
   * 回收第 7 周一线走查与第 8 周阶段退出记录留下的决定：`[D]` 允许在指定客服任务、指定角色和
     合成/授权资料上做只读检索实验（唯一可让自选材料进入本周的口径）；`[D]` 当前不需要 RAG，
     继续普通搜索、规则或内容治理；`[U]` 证据不足，需补查知识所有者、权限或更新流程。
     第 5–8 周 stage-exit §4 已给出 `[D]`（允许只读 RAG 实验），据此写一页 `option-note-v1.md`：
     比较"普通搜索 / 内容治理 / 受限只读 RAG / 停止"四条路，并写明本周只做**语料治理**的边界。
2. **建立来源、所有者与权限的治理合同（Corpus Contract + ACL 矩阵）**：
   * 为每份材料确定：来源 URI、所有者（谁能修改）、版本、生效期（`effective_at` / `expires_at` /
     `supersedes`）、分类（classification）与 ACL（哪些角色可见）。同时写"来源-责任矩阵"：
     谁能确认正文、谁能确认权限、谁能确认有效期、信息不足时的动作（隔离）。
     这张表防止"文件在共享盘里，所以应该能用"的推断。
3. **把八类材料路由到五种状态，生成确定性语料快照（Corpus Manifest + Snapshot）**：
   * 路由状态机：来源不在允许范围，或缺所有者/ACL/版本/有效期 → `quarantined`；已过期或被替代
     → `expired`；与其他当前来源冲突 → `conflict_review`；其余 → `accepted` /
     `accepted_restricted`（仅主管可见）。冲突材料可留在快照中供以后测试，但不得标成"可靠且唯一
     的当前答案"。
   * 复用第 6 周数据入口的血缘与隔离机制（幂等摄取、批内去重、`batch_id`/`source_version`/
     `owner_id` 列），新增语料文档表与分块表（迁移 004），固定解析器与分块规则版本
     （如 `fixed-heading-chunker-v1`）。**chunk 必须原子继承文档的身份与限制**；混合普通条款与
     主管条款的 chunk 取最严格权限，或回到来源边界重新切分。
4. **在任何模型调用前做角色候选检查（Role Candidate Check）**：
   * 用现有系统的确定性查询（SQLite + `X-Auth-Token` 角色），验证三档身份的**原始候选集合**：
     普通客服可以出现 `accepted` 普通来源，不能出现 `accepted_restricted` / `expired` /
     `quarantined` / `conflict_review` 的正文；主管额外可见受限来源；未知角色无可靠候选。
     **普通客服候选中出现主管 chunk = 本周直接失败**——ACL 必须下推到查询层
     （先过滤后出候选），不允许"先全库召回再过滤"，也不允许用提示词遮盖。
5. **知识所有者回读与语料卡片（Corpus Card + Source Review）**：
   * 以课程模拟角色完成回读五问：哪份材料是正式来源？哪个字段决定当前有效？谁能修改、下线或
     解决冲突？一线遇到冲突时怎样升级？哪些内容即使相关也不允许进入普通客服上下文？
     产出 `corpus-card-v1.md`（语料服务哪个任务、来源从哪来、谁负责、快照日期、怎样更新和下线、
     有哪些角色、哪些状态不允许回答、当前材料不能代表哪些真实分布）与 `source-review-note-v1.md`，
     明确参与者保护、模拟边界与触发"更新语料或停止 RAG"的新证据。

---

### 二、 怎么做？（5 天落地步骤）

建议本周投入 **8–10 小时**，按 5 天推进：

* **第 1 天：复核 RAG 准入，写一页选项比较（1–2 小时）**
  * 依据 stage-exit §4 的 `[D]` 决定与 Brief 版本，比较普通搜索 / 内容治理 / 受限只读 RAG / 停止
    四条路；写明"本周不做 embedding、不做检索调优、不生成回答"的边界。
    离开前必须有 `option-note-v1.md`（含有权决定或模拟标签）。
* **第 2 天：建来源、所有者、版本、生效期、分类和角色矩阵（2 小时）**
  * 对八份北辰合成材料（`billing-general-v3`、`refund-supervisor-v2`、`billing-general-v1`、
    `price-difference-east-v5`、`upgrade-east-v2`、`upgrade-notice-v4`、`quick-note.txt`、
    `special-refund-copy.md`）逐份填写文档级合同；来源-责任矩阵落成
    `knowledge-owner-acl-matrix-v1.md`，并把手册角色口径（`support-agent`/`support-supervisor`）
    映射到仓库现有会话角色（`support_agent`/`supervisor`）。
* **第 3 天：复用已有数据入口，产生确定性文档与 chunk manifest（2 小时）**
  * 迁移 004 新增 `corpus_documents` / `corpus_chunks`（沿用第 6 周血缘列风格）；固定解析器与
    分块规则版本；生成 `corpus-manifest-v1.json` 与快照摘要（文档数、chunk 数、各状态计数、
    快照 ID 如 `northstar-corpus-w09-v1`）。
* **第 4 天：重复构建比对 + 三档角色原始候选检查（2 小时）**
  * 同一配置构建两次，逐项比对文档 ID、chunk ID、内容摘要与权限元数据（确定性，类比第 6 周
    批次重跑）；对普通客服 / 主管 / 未知角色各跑一次原始候选查询，断言受限内容零出现。
    产出 `ingestion-isolation-report-v1.md` 与 `role-candidate-check-v1.md`。
* **第 5 天：知识所有者回读（模拟）与语料卡片（1–2 小时）**
  * 回读五问 + `corpus-card-v1.md` + `source-review-note-v1.md`；把"已证明 / 不能证明"写清楚
    （语料可构建 ≠ RAG 改善任务）。

### 三、 交付物有哪些？（7 份 Markdown / JSON 文件）

在仓库根目录（沿用第 5–8 周交付物平铺惯例）需完成以下 **7 份核心产物**：

```
fde-course-week-05/
├─ option-note-v1.md                     # RAG 准入与四条路径比较（含有权决定/模拟标签）
├─ knowledge-owner-acl-matrix-v1.md      # 来源-责任矩阵 + 八份材料文档级治理合同 + ACL
├─ corpus-manifest-v1.json               # 语料快照清单（文档级 + chunk 级，含状态/权限/血缘）
├─ ingestion-isolation-report-v1.md      # 摄取与隔离报告（状态路由证据 + 两次构建比对）
├─ role-candidate-check-v1.md            # 模型调用前的角色候选检查（三档身份 × 原始候选）
├─ corpus-card-v1.md                     # 语料卡片（任务/来源/负责人/快照/更新下线/边界）
└─ source-review-note-v1.md              # 知识所有者回读记录（模拟走查 + 触发条件）
```

配套工程产物（沿用第 6/8 周惯例，纳入 CI 与自动化测试，不改变冻结基线的确定性面）：
`migrations/004_corpus_schema.sql`（语料两表，幂等迁移）、`corpus/build_corpus.py`
（确定性摄取与分块，固定规则版本）、`corpus/check_role_candidates.py`（三档角色原始候选检查）、
`tests/test_corpus_governance.py`（快照确定性 / 角色候选零泄漏 / 缺要件默认隔离），
以及 README 第 9 周章节与本地 CI 阶段挂载。

---

### 四、 验收标准是怎样的？（通过门槛）

必须**同时满足**以下 10 项标准，才算正式通过第 9 周、拿到进入第 10 周
（"在调优 RAG 前冻结评测合同"）的资格：

- [ ] **决定与版本已记录**：本周使用的业务决定、Brief、流程和权限版本已记录（option note 可回溯到第 7/8 周的 `[D]` 决定）。
- [ ] **全链路可追溯**：每份文档和每个 chunk 可追溯到来源、所有者和版本（chunk 不得只剩正文）。
- [ ] **默认隔离**：缺少所有者、ACL、版本或有效期的材料默认进入隔离区，直到有权角色修复。
- [ ] **状态不被掩盖**：过期、被替代、冲突和未知状态没有被"更新时间更晚"或"文件名更好看"掩盖（用 effective/expires/supersedes 表达，而非文件时间戳）。
- [ ] **可重复构建**：同一输入与配置可以生成相同文档与 chunk 清单（两次构建逐项比对 ID、摘要与权限元数据）。
- [ ] **模型调用前权限生效**：普通角色的原始候选在模型调用前已经排除受限内容（先过滤后候选，不是先召回再过滤）。
- [ ] **最严格权限**：混合权限 chunk 使用最严格权限，或回到来源边界重新切分。
- [ ] **合法与脱敏**：真实材料有合法访问与使用范围；普通仓库不含客户正文或密钥（沿用 STAGE-3 脱敏扫描）。
- [ ] **回读诚实**：回读记录说明参与者保护、模拟边界，以及会触发"更新语料或停止 RAG"的新证据。
- [ ] **结论不外推**：结论没有把"语料可构建"写成"RAG 会改善任务"。

---

根据《FDE 成长手册》第 9 周（"建立有来源、有版本、有权限的 RAG 语料"）的交付规范，我们在冻结的
`week08-baseline` 确定性基线之上，把"北辰 B2B 客服计费与升级政策检索"的八份合成知识材料治理成
可重复构建、权限受控的语料快照。以下是七份产物的完整内容模板（**教学模拟材料**，可直接作为起草底稿，
实际数值以本周真实构建结果为准）：

```
fde-course/
└─ week-09/
   ├─ option-note-v1.md                    # RAG 准入与路径比较
   ├─ knowledge-owner-acl-matrix-v1.md     # 来源-责任矩阵与文档级治理合同
   ├─ corpus-manifest-v1.json              # 语料快照清单（文档 + chunk）
   ├─ ingestion-isolation-report-v1.md     # 摄取/隔离报告与确定性比对
   ├─ role-candidate-check-v1.md           # 角色候选检查（模型调用前）
   ├─ corpus-card-v1.md                    # 语料卡片
   └─ source-review-note-v1.md             # 知识所有者回读记录
```

---

### 文件 1：`option-note-v1.md`（RAG 准入与路径比较）

```markdown
# Option Note v1 —— 是否进入受限只读 RAG 语料治理

- **决定依据**: `data-application-stage-exit-v1.md` §4 的 [D]（允许只读 RAG 实验）+
  第 7 周走查记录 + `northstar-discovery-brief-v1`（Brief 版本 v1.0，未变更）
- **决定类型**: 模拟有权角色批准（课程内角色扮演，非真实客户批准）
- **本周边界**: 只做语料治理（来源/版本/权限/快照），不做 embedding 选型、
  不调向量检索、不生成回答、不写提示词、不运行正式 RAG 评测

## 四条路径比较

| 路径 | 能解决"一线找不到当前适用政策"吗 | 本周选择 | 理由 |
| --- | --- | --- | --- |
| 普通搜索（共享盘关键字） | 部分能，但无版本/权限保证，过期文档同样命中 | 否 | 第 7 周走查已证实其导致 POLICY_CONFLICT 类事故 |
| 内容治理（不建检索，只清目录） | 能改善来源质量，但无法按角色出候选 | 部分吸收 | 治理合同本周照做，作为 RAG 前置而非替代 |
| 受限只读 RAG（先语料治理） | 有望，但必须先证明候选集合权限安全 | **是** | stage-exit [D] + 确定性基线可作对照 |
| 停止 | — | 保留为合格退出项 | 若回读发现来源合同无法建立，退回此选项 |

## 已记录的版本绑定
- 代码基线: `week08-baseline`（只读参考，冻结面不变）
- 角色口径: 手册 `support-agent`/`support-supervisor` = 仓库 `support_agent`/`supervisor`
- 语料材料: 手册第 9 周八份合成文档（全部标记 synthetic=true，无真实客户正文）
```

### 文件 2：`knowledge-owner-acl-matrix-v1.md`（来源-责任矩阵与治理合同）

```markdown
# 知识所有者与 ACL 矩阵 v1（Corpus Contract）

## 1. 来源-责任矩阵（谁能确认什么）

| 材料类型 | 谁能确认正文 | 谁能确认权限 | 谁能确认有效期 | 信息不足时的动作 |
| --- | --- | --- | --- | --- |
| 普通计费政策 | 计费知识所有者 | IT / 安全角色 | 计费知识所有者 | 隔离 |
| 主管例外政策 | 主管流程所有者 | IT / 安全角色 | 主管流程所有者 | 隔离 |
| 群通知 | 发布者只能证明发过消息 | 不能自行确认 | 正式知识所有者 | 标记冲突或隔离 |
| 个人笔记 | 笔记作者只能解释用途 | 不能授予组织权限 | 不能确认 | 隔离 |

## 2. 八份材料 × 文档级合同（教学模拟）

| 文档 ID | 所有者 | 版本关系 | ACL | 路由状态 | 路由理由 |
| --- | --- | --- | --- | --- | --- |
| billing-general-v3 | billing-policy-owner | 当前有效（supersedes v1） | agent+supervisor | accepted | 要件齐全且无冲突 |
| refund-supervisor-v2 | supervisor-process-owner | 当前有效 | 仅 supervisor | accepted_restricted | 主管例外政策 |
| billing-general-v1 | billing-policy-owner | 已被 v3 取代 | — | expired | supersedes 关系，非文件时间 |
| price-difference-east-v5 | billing-policy-owner | 当前有效 | agent+supervisor | accepted | 要件齐全且无冲突 |
| upgrade-east-v2 | billing-policy-owner | 当前有效 | agent+supervisor | conflict_review | 与 upgrade-notice-v4 条件冲突 |
| upgrade-notice-v4 | 发布者（非知识所有者） | 当前有效但权限不可自行确认 | — | conflict_review | 群通知无正式 ACL 来源 |
| quick-note.txt | 无正式所有者 | 无版本/有效期 | 无 ACL | quarantined | 缺所有者+ACL+有效期 |
| special-refund-copy.md | 正文同主管条款但来源丢失 | 版本不明 | ACL 丢失 | quarantined | 缺 ACL 与来源边界 |

## 3. 路由状态机（默认规则：缺任一要件 = 隔离）

来源在允许范围？否→quarantined；是→有所有者/ACL/版本/有效期？否→quarantined；
是→已过期或被替代？是→expired；否→与其他当前来源冲突？是→conflict_review；
否→accepted / accepted_restricted。

## 4. 角色口径映射

| 手册口径 | 仓库会话角色 | 候选可见范围 |
| --- | --- | --- |
| support-agent | support_agent（token_agent_valid） | 仅 accepted（普通） |
| support-supervisor | supervisor（token_supervisor_secret） | accepted + accepted_restricted |
| 未知角色 | 其他/缺失 token | 无可靠候选（401/默认拒绝语义不变） |
```

### 文件 3：`corpus-manifest-v1.json`（语料快照清单）

```json
{
  "snapshot_id": "northstar-corpus-w09-v1",
  "built_from_code_revision": "week08-baseline",
  "corpus_contract_version": "knowledge-owner-acl-matrix-v1",
  "parser_version": "teaching-parser-v1",
  "chunker_version": "fixed-heading-chunker-v1",
  "built_at": "2026-10-07T00:00:00+08:00",
  "synthetic": true,
  "documents": [
    {
      "document_id": "billing-general-v3",
      "source_uri": "teaching://northstar/policies/billing-general-v3",
      "source_owner": "billing-policy-owner",
      "content_hash": "<sha256-of-source-text>",
      "version": "3",
      "effective_at": "2026-08-01T00:00:00+08:00",
      "expires_at": null,
      "supersedes": "billing-general-v1",
      "classification": "internal-general",
      "allowed_roles": ["support_agent", "supervisor"],
      "status": "accepted",
      "chunk_count": 6
    },
    {
      "document_id": "refund-supervisor-v2",
      "source_owner": "supervisor-process-owner",
      "classification": "internal-restricted",
      "allowed_roles": ["supervisor"],
      "status": "accepted_restricted",
      "chunk_count": 3
    },
    { "document_id": "billing-general-v1", "status": "expired", "superseded_by": "billing-general-v3", "chunk_count": 0 },
    { "document_id": "price-difference-east-v5", "status": "accepted", "allowed_roles": ["support_agent", "supervisor"], "chunk_count": 4 },
    { "document_id": "upgrade-east-v2", "status": "conflict_review", "conflict_with": "upgrade-notice-v4", "chunk_count": 0 },
    { "document_id": "upgrade-notice-v4", "status": "conflict_review", "conflict_with": "upgrade-east-v2", "chunk_count": 0 },
    { "document_id": "quick-note.txt", "status": "quarantined", "missing": ["owner", "acl", "version", "effective_period"], "chunk_count": 0 },
    { "document_id": "special-refund-copy.md", "status": "quarantined", "missing": ["acl", "source_boundary"], "chunk_count": 0 }
  ],
  "summary": { "documents": 8, "accepted": 2, "accepted_restricted": 1, "expired": 1,
               "conflict_review": 2, "quarantined": 2, "chunks_total": 18 },
  "determinism": { "build_runs": 2, "chunk_id_sets_identical": true,
                   "compared_fields": ["document_id", "chunk_id", "content_digest", "allowed_roles"] }
}
```

> 每个分块原子继承文档限制（chunk 记录示例）：

```json
{
  "chunk_id": "billing-general-v3#section-04",
  "document_id": "billing-general-v3",
  "text_digest": "<sha256>",
  "version": "3",
  "effective_at": "2026-08-01T00:00:00+08:00",
  "classification": "internal-general",
  "allowed_roles": ["support_agent", "supervisor"],
  "corpus_snapshot": "northstar-corpus-w09-v1",
  "synthetic": true
}
```

### 文件 4：`ingestion-isolation-report-v1.md`（摄取与隔离报告）

```markdown
# 摄取与隔离报告 v1（Ingestion / Isolation Report）

- **快照**: northstar-corpus-w09-v1（builder: corpus/build_corpus.py，规则版本 fixed-heading-chunker-v1）
- **数据入口**: 复用第 6 周幂等摄取机制（批内去重 + 查重 + 主键兜底 → 隔离区），血缘列沿用
  `source_version` / `owner_id` / `batch_id`；语料表为迁移 004 新增，不触碰 policies 冻结面

## 1. 状态路由结果（batch: corpus-w09-batch-01）

| 文档 ID | 状态 | 摄取动作 | 隔离/标记理由 |
| --- | --- | --- | --- |
| billing-general-v3 | accepted | 入库 + 6 chunks | — |
| refund-supervisor-v2 | accepted_restricted | 入库 + 3 chunks（restricted） | — |
| billing-general-v1 | expired | 入库（0 chunks，不参与候选） | superseded_by=billing-general-v3 |
| price-difference-east-v5 | accepted | 入库 + 4 chunks | — |
| upgrade-east-v2 / upgrade-notice-v4 | conflict_review | 入库（0 chunks） | 冲突对保留在快照供第 10–12 周测试识别，不得作为可靠唯一答案 |
| quick-note.txt | quarantined | 隔离区 | 缺 owner/ACL/版本/有效期 |
| special-refund-copy.md | quarantined | 隔离区 | ACL 丢失 + 来源边界不明（正文疑似主管条款） |

## 2. 确定性比对（同一输入 + 配置，构建两次）

| 比对项 | 第 1 次构建 | 第 2 次构建 | 一致 |
| --- | --- | --- | --- |
| 文档 ID 集合 | 8 | 8 | ✅ |
| chunk ID 集合 | 18 | 18 | ✅ |
| 内容摘要 + 权限元数据 | — | — | ✅ 逐项相同 |

## 3. 与第 6 周机制的对账口径
- 缺要件材料 100% 进入隔离区（默认拒绝，不按"公开且有效"处理）；
- 同一批次重跑不产生第二个业务结果（幂等）；
- 修复后可只重放目标记录（隔离→复核→ accepted 需所有者角色确认）。
```

### 文件 5：`role-candidate-check-v1.md`（模型调用前的角色候选检查）

```markdown
# 角色候选检查 v1（Role Candidate Check，模型调用前执行）

- **检查方式**: 现有系统确定性 SQL 候选查询（corpus/check_role_candidates.py），
  ACL 下推到查询层 WHERE 条件 —— 先过滤后出候选，不做"全库召回再过滤"
- **零模型调用**: 本检查不涉及任何 embedding / LLM；候选 = 允许进入后续检索的 chunk 集合

| 身份 | 可以出现在原始候选 | 不能出现（实测零出现） |
| --- | --- | --- |
| support_agent（token_agent_valid） | billing-general-v3、price-difference-east-v5 的普通 chunks | refund-supervisor-v2 ✅ / expired ✅ / conflict_review ✅ / quarantined ✅ |
| supervisor（token_supervisor_secret） | 上述普通 chunks + refund-supervisor-v2 受限 chunks | expired ✅ / conflict_review ✅ / quarantined ✅ |
| 未知/缺失 token | 无可靠候选（沿用 401/默认拒绝语义） | 所有受限正文与敏感元数据 ✅ |

## 判定
- **失败定义**: 普通客服原始候选中出现任何一个主管受限 chunk → 本周直接失败；
  禁止继续做 embedding，禁止用提示词遮盖。本次结果：**通过（0 泄漏）**。
- 混合权限 chunk 处理：采用最严格权限（allowed_roles 取交集）或回到来源边界重新切分；
  本快照两种手段均记录在案。
```

### 文件 6：`corpus-card-v1.md`（语料卡片）

```markdown
# Corpus Card v1 —— northstar-corpus-w09-v1

- **服务的任务**: 一线客服核对"当前、适用、且自己有权查看"的计费/升级政策依据
  （衔接第 5 周确定性政策引擎与第 7 周任务闭环，不是通用知识库）
- **来源从哪里来**: 北辰知识负责人提供的八份政策材料（教学合成，synthetic=true）
- **谁负责**: 计费知识所有者（普通政策）/ 主管流程所有者（例外政策）；
  权限确认归 IT/安全角色；见 knowledge-owner-acl-matrix-v1
- **快照日期与构成**: 2026-10-07；8 份文档 → 2 accepted / 1 accepted_restricted / 1 expired /
  2 conflict_review / 2 quarantined；18 个 chunks
- **怎样更新和下线**: 新版本通过 supersedes 关系进入；下线置 expired 而非物理删除
  （保留状态与来源记录，但不得作为可靠答案）；缺要件新材料默认隔离
- **有哪些角色**: support_agent / supervisor（映射见 ACL 矩阵 §4）；未知角色无候选
- **哪些状态不允许作为回答依据**: expired、conflict_review、quarantined
  （conflict 材料仅可用于后续测试系统能否识别冲突）
- **当前材料不能代表哪些真实分布**: 真实共享盘的文档噪声、多业务单元 ACL、
  真实更新延迟与人工流程抖动；结论仅代表本合成快照的治理能力
```

### 文件 7：`source-review-note-v1.md`（知识所有者回读记录）

```markdown
# 来源回读记录 v1（Source Review Note）

- **回读形式**: 课程模拟知识所有者走查（无真实权限参与者；不涉及任何真实客户数据）
- **参与者保护**: 模拟角色知情且可随时纠正/停止/撤回；记录仅保留角色与结论，不保留个人身份

## 回读五问与结论

| 问题 | 结论 |
| --- | --- |
| 哪份材料是正式来源？ | billing-general-v3 / price-difference-east-v5（普通）、refund-supervisor-v2（主管） |
| 哪个字段决定当前有效？ | effective_at/expires_at + supersedes 关系，不是文件修改时间 |
| 谁能修改、下线或解决冲突？ | 对应知识所有者；冲突对（upgrade-east-v2 vs upgrade-notice-v4）需所有者裁决后出新版本 |
| 一线遇到冲突时怎样升级？ | 沿用第 7 周路径：needs_review → escalate → 主管结案 |
| 哪些内容即使相关也禁止进入普通客服上下文？ | 主管例外条款（含疑似复制件）、隔离区材料、过期与冲突正文 |

## 会触发"更新语料或停止 RAG"的新证据
- 来源合同无法建立（所有者/权限持续缺失）→ 停止 RAG，回退普通搜索+内容治理；
- 出现新的跨业务单元 ACL 需求 → 新建语料快照版本，不得在旧快照内静默改权限；
- 第 10 周评测发现 gold source 缺失 → 回到本周新建 corpus 版本（不在数据集背后改文档）。

## 诚实边界
本记录仅证明"语料可被治理成权限受控、可重复构建的快照"，
**不证明 RAG 会改善一线任务** —— 该问题留给第 10–12 周的冻结评测合同与只读 RAG 评测。
```

---

> **第 9 周在 24 周路线中的位置**：第 9 周（语料治理）→ 第 10 周（冻结评测合同 + 60 条案例）
> → 第 11 周（检索比较）→ 第 12 周（有引用、会拒答的只读 RAG 评测）。
> 本周全程零模型调用；第 8 周确定性基线（授权/幂等/审计/部署门）在引入 RAG 后仍是硬约束。




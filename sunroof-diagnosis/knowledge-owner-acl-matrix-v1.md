# 知识所有者与 ACL 矩阵 v1 —— 来源-责任矩阵与文档级治理合同

> 回链 specs/mission-week-09.md 第 2 天；与 `corpus/build_corpus.py::CORPUS_CONTRACTS`
> 逐字段一致（合同即代码，两边漂移会被 tests/test_corpus_governance.py 卡住）。
> 角色映射：手册口径 `PQE（一线）`→ 会话角色 `pqe`；`质量主管` → `quality_supervisor`。

## 1. 来源-责任矩阵

| 来源 | 文档 ID | 知识所有者 | 权限确认 | 有效期确认 | 信息不足时的动作 |
| --- | --- | --- | --- | --- | --- |
| 售后服务总则 v1/v3 | `service-general-v1` / `service-general-v3` | 售后工程组·孙敏 | IT/安全角色 | 售后工程组 | 缺任一要件 → quarantined |
| 东区扭矩复紧细则 v5 | `torque-east-v5` | 扭矩标准组·周毅 | IT/安全角色 | 扭矩标准组 | 同上 |
| 索赔执行细则 v2 | `claim-supervisor-v2` | 索赔流程组·吴桐 | IT/安全角色 | 索赔流程组 | 同上 |
| OEM-A 改装通告 v2 / 公告 v4 | `retrofit-bulletin-v2` / `retrofit-notice-v4` | 改装工程组·韩磊 | IT/安全角色 | 改装工程组 | 冲突对 → conflict_review 冻结 |
| 口头记录 | `quick-note` | （无，属隔离区） | — | — | 默认隔离，等正式来源 |
| 复制件 | `special-claim-copy` | （无，属隔离区） | — | — | 默认隔离，禁止对工单解释引用 |

## 2. 文档级治理合同（关键字段）

| document_id | lineage_key | version | owner | effective_at | supersedes | conflict_group | classification | allowed_roles |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| service-general-v1 | service-general | v1.0 | 售后工程组·孙敏 | 2025-01-01 | [] | — | internal-general | pqe, quality_supervisor |
| service-general-v3 | service-general | v3.2 | 售后工程组·孙敏 | 2026-01-01 | [service-general] | — | internal-general | pqe, quality_supervisor |
| torque-east-v5 | torque-east | v5.0 | 扭矩标准组·周毅 | 2026-03-01 | [torque-east] | — | internal-general | pqe, quality_supervisor |
| claim-supervisor-v2 | claim-supervisor | v2.0 | 索赔流程组·吴桐 | 2026-02-01 | [claim-supervisor] | — | internal-restricted | quality_supervisor |
| retrofit-bulletin-v2 | retrofit-bulletin | v2.0 | 改装工程组·韩磊 | 2026-04-10 | [] | retrofit-oem-a-2026-04 | internal-general | pqe, quality_supervisor |
| retrofit-notice-v4 | retrofit-notice | v4.0 | 改装工程组·韩磊 | 2026-04-15 | [] | retrofit-oem-a-2026-04 | internal-general | pqe, quality_supervisor |
| quick-note | （无） | （无） | （无） | （无） | [] | — | （无） | （无）→ 隔离 |
| special-claim-copy | （无） | （无） | （无） | （无） | [] | — | （无） | （无）→ 隔离 |

## 3. 路由规则（与构建器实现一致）

1. 缺所有者/ACL/源版本/生效期任一要件 → `quarantined`（默认隔离，不按「公开且有效」处理）；
2. 本文件谱系键被**其他**文件的 `supersedes` 声明取代 → `expired`（与文件时间戳无关）；
3. 同一冲突组存在第二份现行来源 → 双双 `conflict_review`（冻结在候选池外，待所有者裁决）；
4. 其余按分类：`internal-restricted` → `accepted_restricted`（仅 quality_supervisor）；
   否则 `accepted`。

## 4. 混合权限段规则

chunk 原子继承文档身份与 ACL；段标题或正文出现「仅限质量主管」标记的段取最严格权限
（internal-restricted，仅 quality_supervisor），不降权。`service-general-v3#s06`
（质量主管例外条款）即此规则的实际用例——标记位于**段标题**，构建器按「标题+正文」双扫描收紧
（任务书工程记录第 3 条教训的固化）。

## 5. 诚实边界

- 语料 ACL 是**角色级**（pqe / quality_supervisor），未下推到 OEM 项目组维度——
  项目组 ACL 仍由 specs 表（阶段 A 节点 02/03）承担，语料层不越权替代；
- 八份材料全部为教学合成（`synthetic=true`），无真实客户数据、无真实个人身份；
- 本矩阵不宣称覆盖真实共享盘的文档噪声与多业务单元 ACL。

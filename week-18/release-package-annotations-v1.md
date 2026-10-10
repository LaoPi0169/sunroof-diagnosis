# 发布材料体检 v1（Day-1 · 第 18 周冷交接）

> 任务：把第 8 周的发布/回滚材料当「接手对象」逐项体检——对照手册 4079-4086 的
> **完整版本包六类绑定**（代码 / schema / 语料索引 / 提示 / 策略工具 / 环境配置），
> 找缺口，再补 `full-release-manifest-v2.json`。结论先行：v1 清单只覆盖六类中的
> **三类半**，直接交给非作者会发生「回滚了代码但答案还是错的」这类半截回滚。

## 1. 体检对象（第 8 周资产盘点）

| 资产 | 位置 | 状态 |
| --- | --- | --- |
| 发布清单 v1 | `release-manifest-v1.json`（northstar-app-w08-r0） | 在，见下逐项对照 |
| 回滚演练记录 | `rollback-drill-v1.md`（Round 1 废止政策复活） | 在，F-18.2 的历史同款场景 |
| 回滚指南 | `ROLLBACK.md`（配置收敛式 + Git 兜底 + 场景 A-E） | 在 |
| known-good 配置 | `config/policy-config.json`（sha256 已登记） | 在 |
| 冻结代码基线 | tag `week07-baseline`（0b3d8aed…） | 在 |

## 2. 逐项对照六类绑定（手册 4079-4086）

| # | 绑定类 | v1 清单覆盖情况 | 判定 |
| --- | --- | --- | --- |
| ① | 代码 | `code_revision`（tag week07-baseline）+ `tooling_revision`（config/deploy/ci 层） | ✅ 覆盖 |
| ② | schema | `schema_version: 003` + 三个 migration 文件清单；回滚不改 DDL | ✅ 覆盖 |
| ③ | 语料索引 | **缺失** —— v1 只绑配置数据，没有语料清单/检索索引的 sha 绑定 | ❌ 缺口 |
| ④ | 提示与生成/引用合同 | **缺失** —— v1 时点 RAG 未建（第 9-12 周之后）；非作者无从知道引用规则属于版本包 | ❌ 缺口 |
| ⑤ | 策略/审批/工具 schema | **半覆盖** —— 有 `config_version`（policy 数据），但没有写合同/能力清单/策略版本（第 13-15 周冻结件） | ⚠️ 半 |
| ⑥ | 环境配置与夹具 | `config_sha256` + `fixtures_version` + 回滚策略（幂等收敛、白名单三列） | ✅ 覆盖 |

## 3. 缺口清单（→ v2 清单逐项补齐）

| # | 缺口 | 风险（若不补） | v2 落点 |
| --- | --- | --- | --- |
| G-1 | 语料索引无绑定（`corpus-manifest-v1.json` 未入清单） | 回滚后答案引用仍回旧语料快照/冲突文档 | bindings.corpus_index（sha256 41d127ed…） |
| G-2 | 提示与生成合同无绑定（`week-12/rag-system-manifest-v1.json`） | 三态合同/引用规则漂移不可察觉 | bindings.prompts_generation（sha256 5087620d…） |
| G-3 | 写合同/能力清单/策略版本无绑定（第 13-15 周冻结件） | 工具目录或写合同被替换后无整包回滚依据 | bindings.policy_tool_schema（sha256 136efe01… / a3fc26d7…） |
| G-4 | 「现行口径绑定映射」未显式登记 | F-18.2 型回归（废止政策被复活）无对照基准 | bindings.env_config.policy_active_mapping_known_good = billing-general-v3 |
| G-5 | 回滚验证只到冒烟 4/4，无任务级复验口径 | 「健康接口 200」被当成恢复 | rollback.verification = 四+1 条任务路径 |
| G-6 | 清单不含「回滚目标整包视图」（rc-18.0/rc-18.1） | 非作者不知道回滚回的是哪一包 | packages 节点 |

## 4. 保留（不改）的 v1 设计

- 配置收敛式回滚（幂等 UPDATE、白名单三列、不删库不改 DDL）——与 v2 的
  「整包切换」互补：v2 管**清单口径**，v1 的收敛器仍是执行件之一；
- `smoke_suite` 四条冒烟作为部署门保留；任务级「四+1」是其上的恢复复验层；
- Git 三层保险（tag/分支/known-good 配置）与 ROLLBACK.md 场景 A-E 全部保留。

## 5. 体检结论

- v1 清单是**「第八周视角」的发布件**：配置层完整，但第 12-17 周长出的
  语料/生成/工具/策略冻结件不在包内——这正是手册要求每周滚动维护完整清单的原因；
- `full-release-manifest-v2.json` 已按上表补齐六类绑定（真实 sha256 + git ref，
  不含任何秘密），并给出 rc-18.0（已知良好）/ rc-18.1（交接现行，含 F-18.2）两个
  整包视图与四+1 复验口径；
- 本体检只覆盖**清单与文档层**；真实系统的制品库/部署系统/索引重建不在教学范围。

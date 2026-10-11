# 天窗总成只读诊断（Sunroof Read-Only Diagnosis）· 阶段 A + 阶段 B 进展
按《FDE 成长路线图》(`../fde-roadmap-v1.md`) 对「PQE 跨 MES/QMS/PLM 只读调取与对账」业务场景逐步展开的本项目自包含子仓库。**阶段目标：一键跨系统对账 + ACL 隔离跨主机厂敏感图纸 + 缺失/冲突/过期明确拒答 + 带引用的根因分析报告草稿。**

## 当前状态（阶段 A 节点 ①–⑤ 已完成并封存；阶段 B 节点 ⑥ 已完成）
| 节点 | 交付物 | 状态 |
|---|---|---|
| ① 问题合同与发现 | `specs/business-background.md` / `specs/role-and-permission-matrix.md` / `specs/discovery-brief-v1.md`（含获准记录与模拟标注） | ✅ |
| ② 确定性垂直薄片 | `vertical-slice-contract.md` + `api-contract/spec-check-v1.json` + 迁移 001 + `application/` 四件 + **8/8 集成测试全绿** | ✅ |
| ③ 可靠数据入口 | `specs/data-contract-v1.md` + `adr-002-ingestion-lineage.md` + 迁移 002 + `ingestion/` 三件 + `ingestion-runbook-v0.md` + `verify_node03.py` + **9/9 摄取测试全绿（17/17 总）** | ✅ |
| ④ 双角色任务闭环 | `task-state-model.md` + `adr-003-task-permission-adaptations.md` + `decision-memo-02.md` + 迁移 003 + `application/tasks.py` + `/api/v1/tasks/action`·`/api/v1/tasks` 端点 + `workbench_cli.py` + `verify_node04.py` + **9/9 任务闭环测试全绿（26/26 总）** | ✅ |
| ⑤ 部署与回滚 | `release-manifest-v1.json` + `deploy-README.md` + `ROLLBACK.md` + 迁移 004（policy_config）+ `deploy/apply_policy_config.py`（配置收敛器）+ `deploy/smoke_w08.py`（4 条任务级冒烟门）+ `deploy/run_rollback_drill.py` + `ci/run_local_ci.py`（四阶段）+ `ci/redaction_scan.py` + 7 份 W8 文档 + `adr-004` + **9/9 部署发布测试全绿（35/35 总）** | ✅（阶段 A 封存） |
| ⑥ 语料治理（阶段 B · 第 9 周） | `option-note-v1.md` + `knowledge-owner-acl-matrix-v1.md` + `corpus/`（schema/构建器/角色候选核查器）+ `corpus/sources/` 8 份合成材料 + `corpus-manifest-v1.json`（sha256 绑定）+ `ingestion-isolation-report-v1.md` + `role-candidate-check-v1.md` + `corpus-card-v1.md` + `source-review-note-v1.md` + **6/6 语料治理测试全绿（41/41 总）** | ✅ |
| 阶段 B（剩余） | 评测合同冻结 / 检索基线 / 只读 RAG 评测 / 只读 MCP / 报告生成 | ⬜ 后续 |

## 架构（阶段 A 节点 ②③④⑤ 落地部分）
```
[PQE 可信身份 X-Auth-Token] ──> POST /api/v1/spec-check (封闭 Schema, extra="forbid")
                                     │
                    [确定性规则引擎 services.py · 零大模型]
                     1. ACL 下推 SQL: oem_project IN (allowed_projects)   ← 跨 OEM 检索层排除
                     2. is_current=1 + 生效窗口过滤                        ← 已替代/过期 SOP 不可引用
                     3. 硬限: |偏差%| > 15 -> needs_review (零规格内容)
                     4. 冲突/零匹配 -> needs_review (结构化原因码)
                                     │
                    [SQLite specs + task_runs 审计 · 三向对账]

[QMS/PLM 规格行批次] ──> ingestion/service.py (服务层摄取, 不新增 HTTP 面)
     1. validate_row 数据合同校验 -> 失败进隔离区 (原因码, 绝不静默丢弃)
     2. 业务键 spec_id:source_version 去重 -> 重复不产生第二次业务效果
     3. accepted 行带完整血缘 (source_id/source_version/batch_id/owner_id) 写入 specs
     4. 单事务原子提交 + 三处一致对账; 定向重放修复隔离记录 (历史绝不物理删除)

[任务事实 (spec_check_tasks 夹具)] ──> POST /api/v1/tasks/action (节点 04 任务状态机)
     1. RBAC 一票否决: pqe = check_spec/confirm/refuse/escalate; supervisor_resolve 主管专属
     2. 任务级 ACL (adr-003 D6): 跨项目直调 -> denied + case_id 回 N/A-ACL-DENIED (零回显)
     3. check_spec 100% 复用 spec-check 引擎 (零逻辑分叉), answered 回填 matched_spec_id
     4. 动作幂等: 重复提交 ALREADY_APPLIED + is_idempotent; 每动作落 task_action_runs 审计
     5. correlation_id 三向对账: HTTP 响应 / spec_check_tasks 终态 / task_action_runs 流水

[发布层 (节点 05)] release-manifest-v1.json (代码基线/配置 sha256/schema 三类绑定)
     1. 配置收敛器 deploy/apply_policy_config.py: 白名单键幂等 UPSERT -> policy_config (唯一可写面, 零 DDL)
     2. 引擎硬限读配置 (缺失/非法回退默认 15.0, known-good 下行为逐字节等价)
     3. 任务级冒烟门 deploy/smoke_w08.py 4 条三向对账: /health 200 ≠ 业务正确, 4/4 才放量
     4. CI 四阶段 ci/run_local_ci.py (= .github/workflows/sunroof-ci.yml 同一入口):
        STAGE-1 Schema -> STAGE-2 41 用例 -> STAGE-3 脱敏扫描 -> STAGE-4 git archive 冷部署 + 冒烟
     5. 回滚 = 配置收敛式 (重放 known-good, ROLLBACK.md 场景 A-E); 演练 runner 三段闭环落证据

[语料层 (节点 06, 阶段 B)] corpus/build_corpus.py (零模型调用; 语料 schema 独立, 冻结迁移 001-004 零改动)
     1. 五态路由 (quarantine-by-default): 缺要件 quarantined / supersedes 谱系 expired /
        冲突对 conflict_review (冻结在候选池外) / 其余 accepted·accepted_restricted
     2. fixed-heading-chunker-v1: chunk 原子继承文档身份与 ACL; 混合权限段 (标题+正文双扫描) 取最严格权限
     3. ACL 下推 check_role_candidates.py: 权限谓词在 SQL WHERE (json_each) 先过滤后候选;
        实测候选 pqe 13 / quality_supervisor 18 / 未知 0, 主管专属零泄漏 (退出码门禁)
     4. 确定性构建: corpus-manifest-v1.json sha256 绑定 8 份来源, 无墙钟字段, 两次构建逐字节一致
```

终态：`answered`（依据充分可出具带引用草稿，**≠ 合格**）/ `needs_review`（拒绝出具，转人工）；任务侧闭环 `confirmed`/`refused` 终态 + `escalated`→`action_completed` 主管结案（节点 ④）。`abstained` 终态在后续节点扩展。

## 快速开始
```bash
pip install -r requirements.txt          # 或复用上级仓库 .venv
uvicorn application.main:app --port 8020 # 勿与原仓库 8016-8018 冲突
python -m unittest discover -s tests -v  # 41 个集成测试 (独立临时库, 100% 确定; 含语料治理 6 用例)
python verify_node03.py                  # 节点 ③ 冒烟: 摄取/对账/重放/引擎回接, 证据落 evidence/
python verify_node04.py                  # 节点 ④ 冒烟: 闭环/幂等/RBAC/主管结案/跨项目 denied, 证据落 evidence/
python workbench_cli.py                  # 双角色任务工作台 CLI (需先启动 API; Token 登录 -> 队列 -> 三要素核验 -> 动作)

# —— 节点 ⑤ 部署与回滚 ——
python deploy/apply_policy_config.py --config config/sunroof-config-w08.json --db data/sunroof.db
                                         # 配置收敛器 (幂等 UPSERT, 白名单键, 零 DDL)
python deploy/smoke_w08.py --base-url http://127.0.0.1:8020 --db data/sunroof.db
                                         # 任务级冒烟 4 条 (三向对账); 前置: 全新初始化库
python ci/run_local_ci.py                # 本地 CI 四阶段 (= GitHub Actions 同一入口), 证据落 evidence/ci-run-w08.log
python deploy/run_rollback_drill.py      # 回滚演练三段闭环 (R0 基线/R1 注入被拦截/R2 回滚复验), 证据落 evidence/

# —— 节点 ⑥ 语料治理 (阶段 B · 零模型调用) ——
python corpus/build_corpus.py            # 确定性语料构建: 8 文档/18 chunks + corpus-manifest-v1.json (两次构建逐字节一致)
python corpus/check_role_candidates.py --db data\corpus-w09.db
                                         # 角色候选核查 (ACL 下推): pqe 13 / quality_supervisor 18 / 未知 0, 泄漏即退出码 1
python ci/run_local_ci.py --evidence-file evidence/ci-run-w09.log
                                         # Week 09 复跑 CI 四阶段 (STAGE-2 41 用例, 语料治理 6 用例并入)
```
可信身份 Token（教学模拟）：`token_pqe_oem_a` / `token_pqe_oem_b` / `token_qa_supervisor_secret`（跨项目只读）；缺失 Token = 默认 PQE 培训身份；未知 Token = 401。

## 判定示例（与发现简报样例一致）
- 8.2 N·m vs 9.5±0.5 → `answered`，偏差 -13.68%，`nonconforming`，引用 `SPEC-SR-A-V5 / QMS-SOP-SR-04-v5#Section-3.2`；
- 8.0 N·m（偏差 -15.79%）→ `needs_review / SPEC_HARD_LIMIT_EXCEEDED`，响应零规格内容；
- 测量日期落在已被替代的 v4.0 窗口 → `NO_MATCHING_SPEC`（绝不引用旧版）；
- 跨项目组查询 → 拒答且响应体零对方项目组标识。

## 硬阻断门槛（测试已证明）
1. 跨 OEM 越权泄露 = 0（ACL SQL 下推 + 零泄漏断言，用例 6/8）；
2. 引用过期/被替代 SOP = 0（`is_current=0` SQL 层排除，用例 3）；
3. 缺失/冲突/超限时假装知道 = 0（100% 结构化 `needs_review`，用例 2/3/6/7）；
4. 入口 Default-Deny：ACL 声明缺失/越权、`is_current` 缺失一律进隔离区，绝不自动扩大可见范围或赋予现行效力（摄取用例 3）；
5. 越权动作零效果 + 幂等：RBAC 一票否决 + 任务级 ACL（跨项目直调 `denied` 零回显），`denied` 仅审计不改变任务状态；重复提交 `ALREADY_APPLIED` 不产生第二次业务效果（任务用例 1/3/4/8）；
6. 部署面三门（节点 ⑤，测试已证明）：清单三类绑定且零秘密（用例 1）；收敛器白名单 Default-Deny + 零业务写副作用 + 零 DDL（用例 2/3）；配置漂移必须被任务级冒烟拦截，配置收敛式回滚恢复且全程零删库（用例 6/7 + 回滚演练 R0/R1/R2 真实运行）。
7. 语料面三门（节点 ⑥，测试已证明）：缺任一治理要件默认隔离，隔离/过期/冲突件正文 chunk 恒为 0（用例 2/3/4）；ACL 下推先过滤后候选，主管专属 chunk 在 pqe 原始候选零出现且候选集合与 ACL 矩阵精确一致（含 `service-general-v3#s06` 排除，用例 5）；清单 sha256 绑定 8 份来源且两次构建逐字节一致（用例 1/6）。

## 诚实边界
MES/QMS/PLM 为 SQLite 合成夹具模拟；零大模型；SN 自动抓取（只读 MCP）留待阶段 C；`abstained` 终态后续扩展不改既有语义；Token 为教学模拟鉴权，上线前需按真实 SSO 重审权限矩阵。批次 `completed` 不等于数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态；摄取为服务层能力，未新增 HTTP 面（与原仓库 Week 06 惯例一致）。任务状态机为单库单事务、串行请求设计，无行锁（幂等键是第一道防线，生产化需乐观锁，见 `decision-memo-02.md` 生产化差距清单）；`confirmed`/`refused` 为终态、无重开转移；质量主管结案仅代表本系统内复核闭环，原系统人工流程双轨并行；CLI 工作台为薄壳，安全全部在服务端。部署面（节点 ⑤）：回滚对象是**配置数据**而非代码，DDL 迁移只增不做降级（`ROLLBACK.md` 场景 B）；单机沙盒演练不宣称生产就绪、热回滚或多实例编排；`/health` 200 只代表进程存活，放量只认任务级冒烟 4/4；任务夹具为一次性状态机终态，冒烟复验必须用全新初始化的数据版本；收敛器不判对错（对错靠评审 + 冒烟双防线，引擎对非法配置值防御性回退默认）。语料面（节点 ⑥）：五态路由与候选检查只证明「语料可被治理、权限可下推」，不证明检索质量与相关性；语料 ACL 是角色级（pqe/quality_supervisor），未下推到 OEM 项目组维度；8 份来源全为教学合成（synthetic=true），冲突对在所有者裁决前不作为任何答案依据；「语料可构建」不外推「RAG 改善任务」（后者归第 10–12 周冻结评测）。

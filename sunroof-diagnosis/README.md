# 天窗总成只读诊断（Sunroof Read-Only Diagnosis）· 阶段 A 进展
按《FDE 成长路线图》(`../fde-roadmap-v1.md`) 对「PQE 跨 MES/QMS/PLM 只读调取与对账」业务场景逐步展开的本项目自包含子仓库。**阶段目标：一键跨系统对账 + ACL 隔离跨主机厂敏感图纸 + 缺失/冲突/过期明确拒答 + 带引用的根因分析报告草稿。**

## 当前状态（阶段 A 节点 ① + ② + ③ + ④ 已完成）
| 节点 | 交付物 | 状态 |
|---|---|---|
| ① 问题合同与发现 | `specs/business-background.md` / `specs/role-and-permission-matrix.md` / `specs/discovery-brief-v1.md`（含获准记录与模拟标注） | ✅ |
| ② 确定性垂直薄片 | `vertical-slice-contract.md` + `api-contract/spec-check-v1.json` + 迁移 001 + `application/` 四件 + **8/8 集成测试全绿** | ✅ |
| ③ 可靠数据入口 | `specs/data-contract-v1.md` + `adr-002-ingestion-lineage.md` + 迁移 002 + `ingestion/` 三件 + `ingestion-runbook-v0.md` + `verify_node03.py` + **9/9 摄取测试全绿（17/17 总）** | ✅ |
| ④ 双角色任务闭环 | `task-state-model.md` + `adr-003-task-permission-adaptations.md` + `decision-memo-02.md` + 迁移 003 + `application/tasks.py` + `/api/v1/tasks/action`·`/api/v1/tasks` 端点 + `workbench_cli.py` + `verify_node04.py` + **9/9 任务闭环测试全绿（26/26 总）** | ✅ |
| ⑤ 部署与回滚 | 发布清单 + 四阶段 CI | ⬜ 待做 |
| 阶段 B/C | 评测集 / RAG / 只读 MCP / 报告生成 | ⬜ 后续 |

## 架构（阶段 A 节点 ②③ 落地部分）
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
```
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


终态：`answered`（依据充分可出具带引用草稿，**≠ 合格**）/ `needs_review`（拒绝出具，转人工）；任务侧闭环 `confirmed`/`refused` 终态 + `escalated`→`action_completed` 主管结案（节点 ④）。`abstained` 终态在后续节点扩展。

## 快速开始
```bash
pip install -r requirements.txt          # 或复用上级仓库 .venv
uvicorn application.main:app --port 8020 # 勿与原仓库 8016-8018 冲突
python -m unittest discover -s tests -v  # 26 个集成测试 (独立临时库, 100% 确定)
python verify_node03.py                  # 节点 ③ 冒烟: 摄取/对账/重放/引擎回接, 证据落 evidence/
python verify_node04.py                  # 节点 ④ 冒烟: 闭环/幂等/RBAC/主管结案/跨项目 denied, 证据落 evidence/
python workbench_cli.py                  # 双角色任务工作台 CLI (需先启动 API; Token 登录 -> 队列 -> 三要素核验 -> 动作)
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
5. 越权动作零效果 + 幂等：RBAC 一票否决 + 任务级 ACL（跨项目直调 `denied` 零回显），`denied` 仅审计不改变任务状态；重复提交 `ALREADY_APPLIED` 不产生第二次业务效果（任务用例 1/3/4/8）。

## 诚实边界
MES/QMS/PLM 为 SQLite 合成夹具模拟；零大模型；SN 自动抓取（只读 MCP）留待阶段 C；`abstained` 终态后续扩展不改既有语义；Token 为教学模拟鉴权，上线前需按真实 SSO 重审权限矩阵。批次 `completed` 不等于数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态；摄取为服务层能力，未新增 HTTP 面（与原仓库 Week 06 惯例一致）。任务状态机为单库单事务、串行请求设计，无行锁（幂等键是第一道防线，生产化需乐观锁，见 `decision-memo-02.md` 生产化差距清单）；`confirmed`/`refused` 为终态、无重开转移；质量主管结案仅代表本系统内复核闭环，原系统人工流程双轨并行；CLI 工作台为薄壳，安全全部在服务端。

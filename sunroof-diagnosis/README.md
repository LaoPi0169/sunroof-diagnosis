# 天窗总成只读诊断（Sunroof Read-Only Diagnosis）· 阶段 A + 阶段 B 进展
[![CI](https://github.com/LaoPi0169/sunroof-diagnosis/actions/workflows/sunroof-ci.yml/badge.svg)](https://github.com/LaoPi0169/sunroof-diagnosis/actions/workflows/sunroof-ci.yml)
[![Docker CD](https://github.com/LaoPi0169/sunroof-diagnosis/actions/workflows/sunroof-docker-cd.yml/badge.svg)](https://github.com/LaoPi0169/sunroof-diagnosis/actions/workflows/sunroof-docker-cd.yml)
按《FDE 成长路线图》(`../fde-roadmap-v1.md`) 对「PQE 跨 MES/QMS/PLM 只读调取与对账」业务场景逐步展开的本项目自包含子仓库。**阶段目标：一键跨系统对账 + ACL 隔离跨主机厂敏感图纸 + 缺失/冲突/过期明确拒答 + 带引用的根因分析报告草稿。**

## 当前状态（阶段 A 节点 ①–⑤ 已完成并封存；阶段 B 节点 ⑥⑦ 已完成）
| 节点 | 交付物 | 状态 |
|---|---|---|
| ① 问题合同与发现 | `specs/business-background.md` / `specs/role-and-permission-matrix.md` / `specs/discovery-brief-v1.md`（含获准记录与模拟标注） | ✅ |
| ② 确定性垂直薄片 | `vertical-slice-contract.md` + `api-contract/spec-check-v1.json` + 迁移 001 + `application/` 四件 + **8/8 集成测试全绿** | ✅ |
| ③ 可靠数据入口 | `specs/data-contract-v1.md` + `adr-002-ingestion-lineage.md` + 迁移 002 + `ingestion/` 三件 + `ingestion-runbook-v0.md` + `verify_node03.py` + **9/9 摄取测试全绿（17/17 总）** | ✅ |
| ④ 双角色任务闭环 | `task-state-model.md` + `adr-003-task-permission-adaptations.md` + `decision-memo-02.md` + 迁移 003 + `application/tasks.py` + `/api/v1/tasks/action`·`/api/v1/tasks` 端点 + `workbench_cli.py` + `verify_node04.py` + **9/9 任务闭环测试全绿（26/26 总）** | ✅ |
| ⑤ 部署与回滚 | `release-manifest-v1.json` + `deploy-README.md` + `ROLLBACK.md` + 迁移 004（policy_config）+ `deploy/apply_policy_config.py`（配置收敛器）+ `deploy/smoke_w08.py`（4 条任务级冒烟门）+ `deploy/run_rollback_drill.py` + `ci/run_local_ci.py`（四阶段）+ `ci/redaction_scan.py` + 7 份 W8 文档 + `adr-004` + **9/9 部署发布测试全绿（35/35 总）** | ✅（阶段 A 封存） |
| ⑥ 语料治理（阶段 B · 第 9 周） | `option-note-v1.md` + `knowledge-owner-acl-matrix-v1.md` + `corpus/`（schema/构建器/角色候选核查器）+ `corpus/sources/` 8 份合成材料 + `corpus-manifest-v1.json`（sha256 绑定）+ `ingestion-isolation-report-v1.md` + `role-candidate-check-v1.md` + `corpus-card-v1.md` + `source-review-note-v1.md` + **6/6 语料治理测试全绿（41/41 总）** | ✅ |
| ⑦ 评测合同冻结（阶段 B · 第 10 周） | `eval-plan-v1.md` + `eval-set-v1.jsonl`（60 例 = 36 开发 + 12 校准 + 12 保留封存）+ `eval-holdout-labels-sealed-v1.json` + `labeling-guide-v1.md`（48 例公开标签附录）+ `dataset-card-v1.md` + `split-leakage-check-v1.md` + `deterministic-baseline-results-v1.jsonl` + `evaluation/`（合同常量/校验器/确定性基线探针）+ `evaluation-freeze-record-v1.md`（12 件 sha256 双口径）+ `specs/mission-week-10.md` + **6/6 评测合同测试全绿（47/47 总）** | ✅ |
| ⑧ 检索基线比较（阶段 B · 第 11 周） | `week-11/retrieval-comparison-manifest-v1.json`（三输入件 sha256 绑定，先冻结后运行）+ `week-11/keyword-results-v1.jsonl`·`vector-results-v1.jsonl`（48 例 × 2 检索器 × 20 字段）+ 失败登记 6 条 / 回归种子 12 条 / 基线报告 / `retriever-decision-v1.md`（vector 单路晋级）+ `retrieval/`（统一合同 / 标签装载 / 比较运行器）+ **6/6 检索比较测试全绿（53/53 总）** | ✅ |
| ⑨-⑫ 只读 RAG 收官 + MCP 工具周（W12-15） | `generation/`（门控决策树 + 抽取式替身 + unseal-once 评测器）+ `week-12/rag-per-case-results-v1.jsonl`（60 例解封一次，状态门 60/60，L5=1.0/L6=28/28/L7=0）+ `week-12/rag-evaluation-report-v1.md`·`decision-memo-03.md`（继续但冻结）+ `mcptool/`（只读 case.lookup_context@1 四级鉴权 + 受控写 ticket.create@1 单一规范化/结构化审批/幂等/独立核验）+ `mcptool/run_fixtures.py`（R5+W5 全过, 效果对账恰 1）+ `week-15/attack-cases-v1.md` 7 攻击卡全部防御成立 + `specs/mission-week-12-18.md` | ✅ |
| ⑬-⑭ 可观测 + 降级 + 值守交接（W16-18，最终交付） | `mcptool/telemetry.py`（TaskTrace 九阶段/终态分类 v1.1/SLI 三口径独立/span 脱敏自检）+ `mcptool/run_canary.py`（60 例重考 CONSISTENT 零漂移）+ `mcptool/kill_switch.py`（封闭枚举+追加审计+无因拒绝）+ 降级门（权限判定优先于降级, action_preview 0 效果审批不消耗）+ `week-17/degradation-drill-evidence-v1.jsonl` + **`workbench_handoff/`（端口 8018 双角色交互工作台: 业务用户问答/工具/受控写全流程, 管理员开关/回滚/审计/金丝雀/重置, 403 强制, 口令零出现）** + `start_workbench_handoff.bat` | ✅ |
| ⑮ 交互控制台（阶段 C 起步） | `application/static/console.html`（单文件零框架控制台: 身份卡 + RAG 问答/规格核对/任务工作台三面板, 页面零令牌字面量）+ `application/main.py` 追加端点 `GET /`→`/ui`·`/ui`·`/api/v1/session/whoami`·`POST /api/v1/rag/answer`·`/api/v1/rag/corpus-meta`（仅追加能力, 不改既有端点语义）+ `application/rag.py`（语料缺库确定性重建, write_manifest=False 不碰冻结 manifest）+ `start_web_console.bat`（8000 防呆启动器）+ `ci/run_local_ci.py` STAGE-4 布局自适配（嵌套 relic .git / 独立 clone / 工作区子目录三布局通吃）+ `deploy/web-console-usage.md` 使用指南 + **7/7 新测试全绿（87/87 总, 本地四阶段 CI 全绿）** | ✅ |
| ⑯ 远程 CI/CD 通道打通 | 远程 Linux CI 四阶段**首次全绿**（CI run#8）: 根因3 冻结清单 file_path 分隔符改比较端归一化（内容锚 document_id/title/sha256/状态族仍逐字段精确绑定, 冻结 manifest 零改动）+ 冷启动自愈群（data/ 不入版本库: build_corpus 三处无参 TypeError 死分支补实参; run_canary 与 test_rag_generation/test_retrieval_comparison 直连前确定性重建 write_manifest=False）+ 根因4 STAGE-4 归档探针改子目录优先（外层工作区根目录同名模板 `ci/run_local_ci.py` 曾把整个工作区归档成应用, 仿真 clone 复现 + runner run#7 证据实证）+ Dockerfile 构建期质量门 RUN 行恢复（CI + 镜像构建双门生效）；质量门恢复连带拆除冷启动自愈覆写冻结清单隐患（5 处调用点 + 门内冷建库全部 write_manifest=False, CD run#12 镜像内 87 用例全绿, latest/sha-* 发布 GHCR） | ✅ |
| ⑰ SN 自动抓取 · 只读 MES（阶段 C 真实集成第一步） | 只读 MCP 工具 `mes.lookup_production_record@1`（`mcptool/mes_contract.py` 封闭合同 + `mes_store.py` 确定性合成 MES 夹具 synthetic=true 零写入口 + `mes_server.py` schema→RBAC→ACL→只读取数校验链, 未知 SN/跨项目/角色不足三因用户面封套逐字节一致零存在性泄漏）+ HTTP 面 `POST /api/v1/mes/production-record`（`application/mes.py` 网关 + models/main 仅追加端点 + 受限审计细分原因不进用户面）+ **R-2 收口: 波形 128 点×32 点/段×4 段冻结分页（truncated/next_segment, 绝不全量回传）** + P-3 缺失字段结构化 FIELD_NOT_AVAILABLE 不编造 + 控制台第四面板「MES 生产记录抓取」+ `api-contract/mes-lookup-v1.json` 冻结合同（与代码同值互锁断言）+ `store_fingerprint()` 指纹前后对照只读回归（§9.3 教训落地）+ **13/13 新测试全绿（100/100 总, 本地四阶段 CI 全绿: STAGE-2 Ran 100 tests / STAGE-3 36 文件 0 违规 / STAGE-4 4/4）** | ✅ |
| ⑱ PLM 工程记录查询 · 只读 PLM（阶段 C 真实集成第二步） | 只读 MCP 工具 `plm.lookup_engineering_record@1`（`mcptool/plm_contract.py` 封闭合同 + `plm_store.py` 确定性合成 PLM 夹具 synthetic=true 零写入口 + `plm_server.py` schema→RBAC→ACL→只读取数校验链, 未知件号/跨项目/角色不足三因用户面封套逐字节一致零存在性泄漏）+ HTTP 面 `POST /api/v1/plm/engineering-record`（`application/plm.py` 网关 + models/main 仅追加端点 + 受限审计细分原因不进用户面）+ **图纸硬门槛（矩阵 §2 跨 OEM 敏感图纸）: drawing_registry 只回登记元数据 content_disclosure=denied_by_policy, 图纸内容/文件/链接永不出工具, 主管亦不展示** + R-2 收口: ECO 历史 4 条/页冻结分页（eco_offset 0..12, truncated/next_offset, 绝不全量回传）+ P-3 缺失字段结构化 FIELD_NOT_AVAILABLE 不编造 + 控制台第五面板「PLM 工程记录查询」+ `api-contract/plm-lookup-v1.json` 冻结合同（与代码同值互锁断言）+ `store_fingerprint()` 指纹前后对照只读回归（§9.3 教训落地）+ **13/13 新测试全绿（113/113 总, 本地四阶段 CI 全绿: STAGE-2 Ran 113 tests / STAGE-3 40文件 0 违规 / STAGE-4 4/4）** | ✅ |
| ⑲ QMS 检验标准查询 · 只读 QMS（阶段 C 真实集成第三步） | 只读 MCP 工具 `qms.lookup_inspection_standard@1`（`mcptool/qms_contract.py` 封闭合同 + `qms_store.py` 确定性合成 QMS 夹具 synthetic=true 零写入口（被替代行显式保留供过滤证明）+ `qms_server.py` schema→RBAC→ACL→只读取数校验链, 未知件号/跨项目/角色不足三因用户面封套逐字节一致零存在性泄漏）+ HTTP 面 `POST /api/v1/qms/inspection-standard`（`application/qms.py` 网关 + models/main 仅追加端点）+ **P-2 硬门槛（引用过期/已被替代 SOP = 0 次）: is_current=0 被替代行在取数层排除（SQL WHERE is_current=1 语义）, 仅存被替代行时字段级结构化拒引 NO_CURRENT_SOP, 被替代版本标识永不出响应** + P-3 缺失字段结构化 FIELD_NOT_AVAILABLE 不编造 + 控制台第六面板「QMS 检验标准查询」+ `api-contract/qms-lookup-v1.json` 冻结合同（与代码同值互锁断言）+ `store_fingerprint()` 指纹回归 + **13/13 新测试全绿** | ✅ |
| ⑳ 根因分析报告草稿 · 只读聚合（阶段 C 真实集成收官, In-Scope #3 兑现） | 只读聚合 MCP 工具 `report.draft_root_cause@1`（服务端内部直调三源 server 层 MES⑰+QMS⑲+PLM⑱ 同一可信上下文透传, 任一源拒绝整体封套一致零存在性泄漏 + `mcptool/report_aggregate.py` 零状态聚合引擎: findings 带出处引用 / gaps 结构化绝不编造 / conclusion 确定性规则）+ **偏差 100% 确定性可复算（measured vs 现行质量标准按 mes_step 对接键, 公差带判定 + 15% 硬限转人工, SPEC_HARD_LIMIT_EXCEEDED 与阶段 A 同值原因码）** + R-2 载料纪律: 波形绝不嵌入报告（OMITTED_BY_R2_PAGINATION_CONTRACT gap, 响应零点列）+ 草稿语义 draft_ready ≠ 合格判定（静态 disclaimer）+ HTTP 面 `POST /api/v1/report/draft` + 控制台第七面板「根因分析报告草稿」+ `api-contract/report-draft-v1.json` 冻结合同（互锁断言）+ 三源 store 指纹回归 + **13/13 新测试全绿** | ✅ |
| 阶段 C | ⑮ 交互控制台 → ⑯ 远程 CI/CD 通道 → ⑰ SN 只读 MES 抓取 → ⑱ PLM 工程记录查询 → ⑲ QMS 检验标准查询 → ⑳ 根因分析报告草稿 已交付 —— **discovery-brief In-Scope 1/2/3 全部兑现, 阶段 C 收官** | ✅ |
| 阶段 D·批次 1 | 生产加固三件套：**D1-a 并发乐观锁**（`spec_check_tasks` 幂等追加 `version` 列 + `update_task_state(expected_version)` 条件更新, 过期版本结构化 `CONCURRENCY_CONFLICT`, 五动作处理器读时版本携带; 附带 WAL + busy_timeout=5000）+ **D1-b 审计哈希链**（`task_action_runs` 追加 `prev_entry_hash`/`entry_hash` + `audit_chain_head` 只追加链头, 同事务「读链头→sha256→追加行→推进链头」, 历史空链行一次性补链只补哈希, `verify_audit_chain()` 重算+前向链接+链头三重校验定位坏 run_id）+ **D1-c 签名短时令牌**（`application/token_service.py` `sr1.*` HMAC-SHA256: sub/role/projects/iat/exp/jti, 秘密 env `SUNROOF_TOKEN_SECRET` 缺省进程随机零字面量; `POST /api/v1/auth/token` 签发 + `/api/v1/auth/revoke` 主管专属幂等吊销; `SUNROOF_REQUIRE_SIGNED_TOKENS=1` 严格模式退役静态令牌, 默认关闭保证教学回放零回归）+ 迁移 005（`audit_chain_head` + `revoked_tokens`, 冻结 001-004 DDL 零改动）+ **17/17 生产加固测试全绿（156/156 总, 本地四阶段 CI 全绿: STAGE-1 audit_chain_ok=True / STAGE-2 156 / STAGE-3 49 文件 0 违规 / STAGE-4 4/4）** | ✅ |
| 阶段 D·批次 2 | 三源真实适配器缝：**D2-a 取数协议 + 注册表**（`mcptool/source_adapter.py` 三源取数 `Protocol`（与 mes/qms/plm server 的 store 消费面逐一同形）+ 适配器注册表（`resolve`/`register`/`reset_registry` + 只追加换装 journal），默认实现逐方法委托冻结合成夹具 —— 三源 store 指纹前后对照逐字节一致，冻结合同/审计/既有用例零改动；三源 server 取数改走注入缝，可选换装自定义实现）+ **D2-b HTTP 适配器骨架**（httpx：配置驱动端点 `SUNROOF_SOURCE_{MES,QMS,PLM}_URL` / 超时 / 有限重试（上限 5），默认关闭（`SUNROOF_SOURCE_MODE=http` 且三源端点齐备才换装，缺一快速失败、绝不静默回退合成夹具）；不可达 → 既有「源不可用结构化拒答」—— 用户面三因一致 `not_available`（P-1）+ 受限审计 `source_unavailable:<源>` 精细分型，绝不编造绝不回退）+ **16/16 新测试全绿（172/172 总, 本地四阶段 CI 全绿: STAGE-1 audit_chain_ok=True / STAGE-2 172 / STAGE-3 50 文件 0 违规 / STAGE-4 4/4）** | ✅ |
| 阶段 D·批次 3 | 生产发布包构建：**D3-a Dockerfile 生产化**（专用非 root 用户 `sunroof` uid/gid 10001 + 构建期全量测试门以非 root 身份执行（权限配错 = 构建失败自证）+ HEALTHCHECK 与构建期测试门保留；`docker-compose.yml` 生产形态追加 `SUNROOF_TOKEN_SECRET` 环境变量注入，秘密零硬编码）+ **D3-b `release-manifest-v2.json`**（六类绑定: 代码基线 / 配置 sha256 / **schema 005** / 冻结合同清单 5 份 api-contract sha256 实算一致 / 冒烟门 / CI 入口；清单零秘密测试断言）+ **D3-c 部署 runbook v2 + 备份/恢复脚本**（`deploy/backup_snapshot.py`: SQLite backup API 在线一致快照 + 快照后 `integrity_check` + `verify_audit_chain()` 哈希链一致性门, 校验不过回滚删除绝不产出坏备份 + 元数据 `db_sha256`；`deploy/restore_snapshot.py`: fail-fast 恢复前校验 + 目标库安全快照 + 原子替换 + WAL 侧车清理 + 恢复后复验, 被篡改备份恢复拒绝且目标库逐字节零触碰）+ `deploy/runbook-v2.md`（Ubuntu Docker 部署/备份/恢复/哈希链运维校验/回滚锚点）+ **11/11 新测试全绿（183/183 总, 本地四阶段 CI 全绿: STAGE-1 audit_chain_ok=True / STAGE-2 183 / STAGE-3 52 文件 0 违规 / STAGE-4 4/4）** | ✅ |

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
        STAGE-1 Schema -> STAGE-2 75 用例 -> STAGE-3 脱敏扫描 -> STAGE-4 git archive 冷部署 + 冒烟
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
python -m unittest discover -s tests -v  # 75 个集成测试 (独立临时库, 100% 确定)

# —— 节点 ⑨-⑭ (W12-18): 只读 RAG 收官 / MCP 工具 / 对抗 / 可观测 / 降级 / 交接工作台 ——
python -m generation.run_rag_evaluation            # unseal-once 只读对账: 60/60 状态门, L6 引用 28/28, L7=0
python -m mcptool.run_fixtures                     # R5+W5 夹具: 效果对账恰 1, all_pass
python -m mcptool.run_canary                       # 金丝雀重考 60 例: behavior_drift=[] CONSISTENT
python -m mcptool.run_degradation_drill            # D-01..04 降级演练: forbidden=0
start_workbench_handoff.bat                        # 8018 双角色交互工作台 (最终可交互验收交付物; 防呆: 自动检测端口被模板占用)
# Ubuntu 服务器 Docker 部署: 见 deploy/ubuntu-docker-deploy.md (build/compose/离线/防火墙/常见坑)

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

# —— 节点 ⑦ 评测合同冻结 (阶段 B · 第 10 周 · 零模型调用) ——
python evaluation/validate_eval_set.py   # 考卷校验: 60 行矩阵逐格 + 近重复零泄漏 + 封存边界零标签 -> VALID
python evaluation/run_deterministic_baseline.py
                                         # 确定性基线探针: 60 例两次运行逐字节一致 (answered 20/needs_review 8/not_applicable 20/sealed 12)
python ci/run_local_ci.py --evidence-file evidence/ci-run-w10.log
                                         # Week 10 复跑 CI 四阶段 (STAGE-2 47 用例, 评测合同 6 用例并入)

# —— 节点 ⑧ 检索基线比较 (阶段 B · 第 11 周 · 零生成零业务效果) ——
python -m retrieval.run_retrieval_comparison
                                         # 48 例 × 2 检索器一次正式运行: 双跑投影逐行一致 + holdout 零执行 + pqe 受限候选逐例 0
python ci/run_local_ci.py --evidence-file evidence/ci-run-w11.log
                                         # Week 11 复跑 CI 四阶段 (STAGE-2 53 用例, 检索比较 6 用例并入)

# —— 阶段 D 批次 1 生产加固 (乐观锁 + 审计哈希链 + 签名短时令牌) ——
python ci/run_local_ci.py --evidence-file evidence/ci-run-d1.log
                                         # CI 四阶段: STAGE-1 含哈希链自洽校验 (migrations=5), STAGE-2 156 用例, STAGE-4 4/4

# —— 阶段 D 批次 2 三源真实适配器缝 (取数协议 + 注册表 + HTTP 骨架) ——
python ci/run_local_ci.py --evidence-file evidence/ci-run-d2.log
                                         # CI 四阶段: STAGE-1 audit_chain_ok=True, STAGE-2 172 用例, STAGE-3 50 文件, STAGE-4 4/4

# —— 阶段 D 批次 3 生产发布包 (非 root 镜像 + manifest v2 + 备份/恢复) ——
python ci/run_local_ci.py --evidence-file evidence/ci-run-d3.log
                                         # CI 四阶段: STAGE-1 audit_chain_ok=True, STAGE-2 183 用例, STAGE-3 52 文件, STAGE-4 4/4

# —— 阶段 E 批次 1 评测器 v2 (判定层补全 + fall-through 修正语义) ——
python -m evaluation.evaluator_v2 --write
                                         # 60 例事实级判定: 状态门真判定 58/60 + 事实层 67/81 + forbidden 0 违规; 产物已存在则只读对账
python ci/run_local_ci.py --evidence-file evidence/ci-run-e1.log
                                         # CI 四阶段: STAGE-2 194 用例, STAGE-3 53 文件, STAGE-4 4/4

# —— 阶段 E 批次 2 SLO 基线聚合器 ([U] 守卫: 无真实流量不定案) ——
python -m deploy.slo_baseline_aggregator --db data/sunroof.db --window-start 2026-11-01T00:00:00Z --window-end 2026-11-08T00:00:00Z
                                         # 窗口任务终态 -> SLO-1/2/3 基线观测; 分母<30 = insufficient_data; 定标流程见 deploy/slo-adoption-runbook-v1.md
python ci/run_local_ci.py --evidence-file evidence/ci-run-e2.log
                                         # CI 四阶段: STAGE-2 200 用例, STAGE-3 54 文件, STAGE-4 4/4

# 备份/恢复 (Ubuntu Docker 部署口径见 deploy/runbook-v2.md §3/§4):
python deploy/backup_snapshot.py --db data/sunroof.db --out-dir data/backups --label nightly
python deploy/restore_snapshot.py --backup-file data/backups/<备份文件>.db --db data/sunroof.db
```
可信身份 Token（教学模拟）：`token_pqe_oem_a` / `token_pqe_oem_b` / `token_qa_supervisor_secret`（跨项目只读）；缺失 Token = 默认 PQE 培训身份；未知 Token = 401。阶段 D 批次 1 起新增签名短时令牌：`POST /api/v1/auth/token` 以静态凭证换 `sr1.*` HMAC 签名短时令牌（过期/吊销/改签/畸形一律 401），质量主管可 `POST /api/v1/auth/revoke` 即时吊销；`SUNROOF_REQUIRE_SIGNED_TOKENS=1` 时静态令牌退役（默认关闭，教学回放零回归）。

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
8. 评测面三门（节点 ⑦，测试已证明）：60 例数量矩阵逐格精确（36/12/12 × 24/10/8/8/10）且输入行零标签字段（封存边界，用例 1/2/4）；gold/forbidden 引用全部回到第 9 周冻结快照且校验器 verdict=VALID（用例 3）；保留集标签只存封存件且 sha256(LF) 与冻结记录一致、确定性基线两次运行逐字节一致并逐例携带精确 release（用例 5/6）。
9. 生产加固三门（阶段 D 批次 1，测试已证明）：并发乐观锁——过期版本动作显式 `CONCURRENCY_CONFLICT`（任务终态不变 + 审计留痕，绝不盲重试，幂等重放与冻结基线逐字节等价）；审计防篡改——业务字段篡改/删行/链头破坏均被 `verify_audit_chain()` 定位到 run_id（同事务入链，历史行一次性补链只补哈希注记、业务字段零改动）；签名令牌 Default-Deny——过期/吊销/改签/畸形一律 401，签发面未知用户与坏凭证同封套零存在性泄漏，吊销为主管专属且幂等。
10. 三源适配器缝三门（阶段 D 批次 2，测试已证明）：默认合成——注册表默认逐方法委托冻结夹具，全消费面遍历后三源 store 指纹逐字节一致；换装 Default-Deny——不满足取数协议的适配器拒绝注入，HTTP 模式缺任一端点快速失败且注册表零变化（绝不静默回退/部分换装）；不可达不编造——源不可达沿用既有结构化拒答（用户面三因一致 `not_available` + 受限审计 `source_unavailable:<源>` 精细分型），绝不拼装部分合成数据冒充三源报告。
11. 生产发布包三门（阶段 D 批次 3，测试已证明）：非 root 镜像——Dockerfile 专用用户 uid/gid 10001 且构建期全量测试门以非 root 身份执行（权限配错 = 构建失败，静态断言 USER 非 root + 测试门位于 USER 之后）；清单 v2 六类绑定——代码基线/配置 sha256/冻结合同 5 份 sha256 全部与文件实算逐字节一致、schema 005、清单零秘密字面量；备份/恢复哈希链门——快照后 `integrity_check` + `verify_audit_chain()` 三重校验不过即回滚删除（绝不产出坏备份），被篡改备份恢复 fail-fast 拒绝且目标库逐字节零触碰，正常恢复后逐字节一致且链校验 ok。
12. 评测器 v2 三门（阶段 E 批次 1，测试已证明）：专属触发即终局——required_fact 触发判否即 miss，绝不向后扫描兜底（上级 NS-HOLD-06 型病灶等价构造被正确拒绝）；空真修正——无版本锚点不产生判定、数字锚点剥离版本号（v3 的 3 不是数量锚点）；判定矩阵留痕——每条 fact 记录全部触发规则及判定，v1 落盘 60/60 为自比较恒真（判定层缺陷登记在案），v2 真判定 58/60 逐例呈现差异且 v1 产物零改动（内容 hash 前后一致），事实层 miss 如实呈现绝不因拒答得体虚判。

## 诚实边界
MES/QMS/PLM 为 SQLite 合成夹具模拟；零大模型；SN 自动抓取（节点 ⑰）、PLM 工程记录查询（节点 ⑱）、QMS 检验标准查询（节点 ⑲）与根因分析报告草稿（节点 ⑳）已以只读 MCP 工具交付（MES/QMS/PLM 均为确定性合成夹具，零真实系统连接，凭证治理沿用教学模拟静态 Token；报告草稿为确定性规则聚合 + 可复算偏差计算，零 LLM，`draft_ready` 是「依据充分可出具草稿」而非「合格判定」）；`abstained` 终态后续扩展不改既有语义；Token 为教学模拟鉴权——阶段 D 批次 1 起提供生产形状的 `sr1.*` HMAC 签名短时令牌（过期/吊销/严格模式退役静态令牌），仍非真实 SSO/OIDC，上线前需按真实身份源重审权限矩阵（`specs/production-hardening-brief-v1.md` §2 非目标维持）。阶段 D 批次 2 起三源取数经适配器缝（`mcptool/source_adapter.py`）：HTTP 适配器为「缝」而非「集成」——真实 MES/QMS/PLM 接入需按其真实 API 契约另做载荷映射与行级过滤，本骨架仅交付传输语义（端点/超时/有限重试）与不可达结构化拒答，默认关闭且绝不以合成数据冒充真实源。阶段 D 批次 3 起交付生产发布包：非 root 容器（uid/gid 10001）只覆盖容器内进程；备份工具校验的是审计链一致性与库完整性而非业务正确性（放量门永远是任务级冒烟 4/4）；快照/恢复不替代异地容灾与保留策略；compose 为单容器编排（无多实例/热回滚）。批次 `completed` 不等于数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态；摄取为服务层能力，未新增 HTTP 面（与原仓库 Week 06 惯例一致）。任务状态机为单库单事务设计（幂等键是第一道防线）；阶段 D 批次 1 起追加乐观锁 `expected_version`（过期版本显式 `CONCURRENCY_CONFLICT`，`decision-memo-02.md` 生产化差距清单第 1 条已兑现）；`confirmed`/`refused` 为终态、无重开转移；质量主管结案仅代表本系统内复核闭环，原系统人工流程双轨并行；CLI 工作台为薄壳，安全全部在服务端。部署面（节点 ⑤）：回滚对象是**配置数据**而非代码，DDL 迁移只增不做降级（`ROLLBACK.md` 场景 B）；单机沙盒演练不宣称生产就绪、热回滚或多实例编排；`/health` 200 只代表进程存活，放量只认任务级冒烟 4/4；任务夹具为一次性状态机终态，冒烟复验必须用全新初始化的数据版本；收敛器不判对错（对错靠评审 + 冒烟双防线，引擎对非法配置值防御性回退默认）。语料面（节点 ⑥）：五态路由与候选检查只证明「语料可被治理、权限可下推」，不证明检索质量与相关性；语料 ACL 是角色级（pqe/quality_supervisor），未下推到 OEM 项目组维度；8 份来源全为教学合成（synthetic=true），冲突对在所有者裁决前不作为任何答案依据；「语料可构建」不外推「RAG 改善任务」（后者归第 10–12 周冻结评测）。评测面（节点 ⑦）：本周只证明「评测合同与 60 条案例已在看候选结果之前冻结，且确定性基线已逐例跑通」，不宣称检索质量与答案质量；基线分布为本仓库第 2 周引擎真实 ACL 语义的实测（主管跨项目可见同零件双现行版本 → SPEC_CONFLICT），未回调引擎、未删难题；60 例均含显式角色（无 unknown_role 案例）；材料全为教学合成；保留集为自我封存 ≠ 独立盲测；课程数量与阈值不构成行业标准、客户承诺或生产门槛。

# 阶段 D 生产化 Brief v1（生产级产品输出构建）

状态：**FROZEN**（2026-10-14，阶段 C 收官 `b9472a9` 之后，经用户拍板「三批连做」冻结）
回链：`decision-memo-02.md` §3 生产化差距清单 · `../fde-roadmap-v1.md` §2 节点 ⑤ ·
`specs/discovery-brief-v1.md`（Out-of-Scope 系统级禁止设计不因本 Brief 解禁）·
`adr-005-production-hardening-adaptations.md`（实现适配决策）
纪律：每批次「冻结合同 → 测试 → 实现 → 全量绿 → 本地 CI 四阶段 → 证据落盘 → 文档 → 提交推送 → 远端 CI 确认」；
冻结面（migrations 001-004 DDL / api-contract / 合同同值互锁 / 引擎判定表 / 既有 139 用例语义）零改动。

## 1. 范围（三批次，D1→D2→D3）

### D1 批次 1 · 生产加固三件套（对应 decision-memo-02 §3 第 1/2/3 条）

| # | 差距 | 生产化交付 | 验收口径 |
| --- | --- | --- | --- |
| D1-a | 并发竞态（无行锁，幂等键是第一道防线） | `spec_check_tasks` 加 `version` 乐观锁列；`update_task_state` 支持 `expected_version` 条件更新（版本自增）；任务状态机五个动作处理器全部携带读时版本；过期版本 → 结构化拒绝 `CONCURRENCY_CONFLICT`（任务终态不变 + 审计留痕），不盲重试 | 陈旧版本动作 → 显式失败；终态与幂等重放行为与冻结基线逐字节等价 |
| D1-b | 审计无防篡改（普通表） | `task_action_runs` 加 `prev_entry_hash`/`entry_hash` 哈希链列 + 独立链头表 `audit_chain_head`（只追加）；每次审计写入 = 同事务「读链头 → 算 sha256 → 追加行 → 推进链头」；启动时对历史空链行做一次性回填（只补哈希注记，业务字段零改动）；提供 `verify_audit_chain()` 校验（重算哈希 + 前向链接 + 链头一致） | 任何业务字段篡改/行删除/链头破坏 → 校验失败并定位 run_id；全量动作后校验 ok |
| D1-c | 模拟鉴权（静态教学 Token） | 新增 `application/token_service.py`：HMAC-SHA256 签名短时令牌（格式 `sr1.<payload>.<sig>`，载荷含 sub/role/projects/iat/exp/jti）；过期 + 吊销表 `revoked_tokens` + 进程内秘密（env `SUNROOF_TOKEN_SECRET`，缺省进程随机、零字面量硬编码）；新增 `POST /api/v1/auth/token`（静态凭证换签名令牌）与 `POST /api/v1/auth/revoke`（主管专属）；`SUNROOF_REQUIRE_SIGNED_TOKENS=1` 时静态令牌退役 | 签名令牌过 whoami/动作端点；过期/吊销/改签/畸形一律 401 Default-Deny；默认关闭严格模式保证教学回放与既有用例零回归 |

附带硬化：SQLite 连接开 `journal_mode=WAL` + `busy_timeout=5000`（写并发等待而非立即 SQLITE_BUSY）。
CI 侧：`ci/run_local_ci.py` STAGE-1 必需表 + 哈希链校验；`ci/redaction_scan.py` 范围 +1 文件
（`application/token_service.py`，凭据字面量授权面同步 +1）。

### D2 批次 2 · 三源真实适配器缝（对应遗留清单第 3 类「合成实现层」）

| # | 交付 | 验收口径 |
| --- | --- | --- |
| D2-a | `mcptool/source_adapter.py`：三源取数协议（Protocol）+ 适配器注册表；mes/qms/plm store 保持冻结语义，经可选注入点换实现 | 合成夹具仍为默认实现，三源 store 指纹前后对照逐字节一致；冻结合同/审计零改动 |
| D2-b | HTTP 适配器骨架（httpx，已在 requirements）：配置驱动端点/超时/有限重试；不可达 → 沿用既有「源不可用结构化拒答」口径（`source_unavailable` 精细分型），绝不编造 | 适配器默认关闭；降级演练语义与 week-17 冻结结果一致 |

### D3 批次 3 · 生产发布包构建（「产品输出」收口）

| # | 交付 | 验收口径 |
| --- | --- | --- |
| D3-a | Dockerfile 生产化（非 root 运行、显式 HEALTHCHECK 保留）+ `docker-compose.yml` 生产形态（数据卷持久化、环境变量注入 `SUNROOF_TOKEN_SECRET`） | compose 配置可直接 `up`；镜像内全量测试门保留 |
| D3-b | 发布清单 v2（`release-manifest-v2.json`）：六类绑定覆盖阶段 C+D 全部面（代码基线/配置 sha256/schema 版本/冻结合同清单/冒烟门/CI 入口） | 清单不含秘密；schema 版本推进到 005 |
| D3-c | 部署 runbook v2 + 备份/恢复脚本（数据卷 + 审计链快照一致性） | runbook 含回滚锚点与哈希链校验步骤 |

## 2. 非目标（重申，边界不放松）

1. **不接真实身份源**：`sr1.` 令牌是「生产形状」的教学实现（HMAC + 过期 + 吊销），真实 SSO/OIDC 仍需按
   `specs/role-and-permission-matrix.md` 重审 —— decision-memo-02 §3 第 2 条的「按真实身份源重审」保持开放；
2. **不写外部系统**：双轨结案（返修单/8D 线下流程）与外部写效果=0 是刻意设计（Out-of-Scope）；
3. **零模型调用**：全部生产化不引入 LLM/随机/时钟判定，确定性纪律不变；
4. **不拆微服务、不做 DOWN 迁移**（迁移只增不回退，回滚靠发布清单锚点）；
5. SLO 定标与评测器 v2（HOLD-06）不在本 Brief，属另一方向（需真实流量/非作者复验）。

## 3. 用户既存决策项（维持不动）

`.gitignore` 损坏编辑 · 再生基线文件（deterministic-baseline/canary/degradation/ci-run-w08 evidence）·
relic 内层 `.git` · 未跟踪 `evidence/ci-run-w16-fix.log`。提交一律显式逐文件 add，绝不 `git add -A`。

# ADR-005: 生产加固适配决策（阶段 D 批次 1）

状态：已接受（2026-10-14）
回链：`specs/production-hardening-brief-v1.md` D1 · `decision-memo-02.md` §3（生产化差距 1/2/3）·
先例：`adr-003`（幂等 ALTER 追加列，不重建冻结表）· `adr-004`（只新增独立表）

## D1 乐观锁列以幂等 ALTER 追加（不重建冻结表）

`spec_check_tasks` 加 `version INTEGER NOT NULL DEFAULT 0`（PRAGMA 检查后 ALTER，与节点 03 specs
血缘列同一先例）。`update_task_state` 增加可选 `expected_version`：提供时 `WHERE ... AND version = ?`
且 `SET version = version + 1`，rowcount=0 即乐观锁冲突；缺省（None）行为与冻结基线等价
（无条件更新 + 版本自增），既有调用方零回归。

## D2 审计哈希链：冻结表加链列 + 独立单行链头表

`task_action_runs` 以幂等 ALTER 加 `prev_entry_hash`/`entry_hash`；链头独立表
`audit_chain_head`（单行 `id=1` CHECK）。追加 = 同事务（`BEGIN IMMEDIATE`）「读链头 → 算哈希 →
INSERT 审计行 → UPDATE 链头」；单写者原子推进，配合 WAL + busy_timeout 排队等待。

哈希口径（冻结，逐字节可复算）：
`entry_hash = sha256( prev_entry_hash + "|" + canonical_json )`，
canonical_json = `json.dumps({run_id, task_id, correlation_id, user_id, user_role, action,
status, reason_code, created_at}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。

## D3 历史审计行回填策略（只补哈希注记）

`init_db` 对 `entry_hash IS NULL` 的历史行按 `rowid` 序一次性补链并推进链头；只写两个哈希注记列，
业务字段零改动（先例：节点 02 种子血缘回填 `UPDATE ... WHERE batch_id = ''`）。
幂等：重跑只命中空链行；`verify_audit_chain()` 重算哈希 + 前向链接 + 链头一致三重校验，
任何业务字段篡改 / 行删除 / 链头破坏 → `ok=False` 并定位首个坏 `run_id`。

## D4 冲突语义：显式失败不猜答案

过期版本动作 → 结构化拒绝 `CONCURRENCY_CONFLICT`（`TaskActionReasonCode` 只增成员，不改不重排），
任务终态不变 + `task_action_runs` 审计留痕；服务端不做盲重试，客户端重读后重试。
幂等重放 / 无效转移 / 越权 denied 路径与冻结基线逐字节等价。

## D5 签名短时令牌（sr1.*）与静态教学令牌并存

格式 `sr1.<b64url(payload)>.<b64url(hmac_sha256)>`；载荷 `{sub, role, projects, iat, exp, jti}`。
秘密：env `SUNROOF_TOKEN_SECRET`；缺省**进程随机**生成（零硬编码字面量，脱敏口径不变）。
静态教学令牌默认保留（教学回放 + 既有用例零回归）；`SUNROOF_REQUIRE_SIGNED_TOKENS=1`
严格模式退役静态令牌（401）。吊销表 `revoked_tokens`（jti 主键，`INSERT OR IGNORE` 幂等）。
诚实边界：非真实 SSO —— 上线前仍须按 `specs/role-and-permission-matrix.md` 以真实身份源重审。

## D6 端点仅追加 + CI 面同步

新增 `POST /api/v1/auth/token`（静态凭证换签名令牌；未知用户与坏凭证同封套 401，零存在性泄漏）、
`POST /api/v1/auth/revoke`（quality_supervisor 专属，非主管 403）。既有端点语义零改动。
CI 同步：`ci/run_local_ci.py` STAGE-1 必需表 +2 并加哈希链校验；
`ci/redaction_scan.py` 范围 +1 文件（`application/token_service.py`，凭据字面量授权面同步 +1，
理由与 `application/auth.py` 相同：服务端定义与校验面）。

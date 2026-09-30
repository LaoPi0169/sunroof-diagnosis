# 摄取数据运维与隔离区排障 Runbook (Ingestion Runbook v0)

- **场景**：北辰 B2B 客服计费与升级政策数据摄取 (Ingestion Pipeline)
- **版本**：`ingestion-runbook-v0.1`
- **读者对象**：数据运维工程师、知识库管理员 (Knowledge Admin)
- **关联文档**：`data-contract-v1.md`（字段与原因码定义）、`adr-002-ingestion-lineage.md`（设计决策）

---

## 1. 批次摄取状态排查流程

当数据摄取批次完成时，首先查批次对账日志与三方对账指标：

```sql
SELECT batch_id, source_id, source_version, total_rows,
       accepted_count, duplicate_count, quarantined_count, status
FROM ingestion_batches
WHERE batch_id = 'batch-w06-20260825-a';
```

**三处一致校验公式**（任何一条不成立即说明发生脏数据逃逸或漏记，立即停止摄取并告警）：

- `total_rows = accepted_count + duplicate_count + quarantined_count`
- `accepted_count = SELECT COUNT(*) FROM policies WHERE batch_id = '...'`
- `quarantined_count + duplicate_count = SELECT COUNT(*) FROM quarantine_records WHERE batch_id = '...'`

也可直接调用代码内对账函数 `ingestion.repository.get_three_way_reconciliation(batch_id)`，返回 `three_way_consistent` 布尔值。

**如何用 correlation_id 定位批次**：所有结构化日志事件（`fde.ingestion` logger）都携带 `correlation_id` 与 `batch_id`。拿到上游反馈的 correlation_id（如 `ing-w06-a-001`）后，在日志中检索 `correlation_id=<值>`，取 `ingestion_batch_completed` 事件中的 `batch_id` 与计数，再回到上面的 SQL 核对。

## 2. 隔离区 (Quarantine) 排查与错误分类

```sql
SELECT quarantine_id, batch_id, safe_source_ref, reason_code, status, replay_id, created_at
FROM quarantine_records
WHERE batch_id = 'batch-w06-20260825-a' AND status = 'quarantined';
```

### 常见原因码处置 SOP

| 原因码 | 根因分类 | 标准处置 SOP |
| --- | --- | --- |
| `MISSING_OWNER` | 缺少知识所有者 | 联系知识库管理员确认归属人 → 补齐 `owner_id` → 定向重放 |
| `UNKNOWN_ACCESS_LEVEL` | 访问级别非标/缺失 | 找权限所有者核对适用角色 → 纠正为 `support_agent` 或 `supervisor` → 重放 |
| `INVALID_EFFECTIVE_RANGE` | 生效/失效日期矛盾 | 修正日期（保证 `expires_at` 严格晚于 `effective_at`）→ 重放；反复出错则拒绝来源 |
| `DUPLICATE_BUSINESS_KEY` | 业务主键重复 | 确认上游是否重复推送；主表已有正常记录则**无需重放**，保持隔离作对账痕迹 |
| `MISSING_BUSINESS_KEY` / `INVALID_REGION` / `INVALID_SCHEMA` | 字段缺失/非法 | 按数据合同补齐或修正后重放 |

## 3. 定向重放 (Targeted Replay) 执行步骤

**谁有权修复**：`owner_id` 由知识库管理员 (Knowledge Admin) 定夺；`effective_at` / `expires_at` 由政策发布人确认；`access_level` 变更必须由权限所有者（客服主管负责人）批准。**禁止直接手动 SQL 修改 `policies` 表**，必须走系统重放管道。

**重放前检查（防止重复提交）**：
1. 确认隔离记录 `status = 'quarantined'`（已 `resolved` 的记录会被系统拒绝）；
2. 确认主表尚无相同业务键：
   ```sql
   SELECT 1 FROM policies WHERE policy_id = 'POL-REFUND-02' AND source_version = '1';
   ```

**执行重放**（支持部分补丁，系统自动与隔离区原始行合并后重新校验）：

```python
from ingestion.schema import ReplayRequest
from ingestion.service import execute_targeted_replay

result = execute_targeted_replay(ReplayRequest(
    quarantine_id="quar-a1b2c3d4e5f6",
    replayed_by="knowledge-admin",
    fixed_payload={"owner_id": "knowledge-refund"},  # 仅修复字段即可
    notes="已由知识管理员在隔离区补齐丢失的 owner_id 并重新发布",
))
print(result.status, result.replay_id, result.accepted_policy_id)
```

**重放后的数据库校验**：
1. 主表新增修复行，血缘 `batch_id` 带 `replay-` 前缀：
   ```sql
   SELECT policy_id, source_version, owner_id, batch_id
   FROM policies WHERE policy_id = 'POL-REFUND-02';
   ```
2. 隔离记录 `status` 变为 `resolved` 且 `replay_id` 已回填；`raw_payload` 与 `created_at` **保持原值**（修复历史不物理删除）；
3. `ingestion_replay_logs` 出现本次 `replay_id` 审计行（成功与失败尝试都有记录）；
4. 重新执行第 1 节三处一致校验（resolved 行仍计入隔离表，公式依旧成立）。

## 4. 停止摄取而不影响已提交数据

- 摄取批次是**单事务原子提交**：处理中途崩溃/中断时整批回滚，主表与隔离区不会留下部分提交；直接重新运行同一 `batch_id` 即可安全重放整个批次。
- 已达终态的批次重跑会命中幂等短路（`is_idempotent_replay=true`），不会产生第二次业务效果。
- 需要熔断时：停止上游推送（不删库、不改历史），在 `ingestion_batches` 将问题批次备注后由人工复核；`status='processing'` 的陈旧残留行说明进程在事务中途被强杀（SQLite 已回滚），删除该行前先与上游核对后重放批次即可。
- 调整上游后**不要复用旧 `batch_id`** 推送不同内容——同一 `batch_id` 的内容必须不变（幂等键语义）。

## 5. 哪些原始材料不能进入普通日志

普通结构化日志只允许：事件名、`correlation_id`、`batch_id`、`source_id`、行引用（`source_id#R行号`）、计数、`reason_code`、`replay_id` / `quarantine_id`、结果。

**绝不允许**：政策标题与正文、公式规则、所有者 ID、政策 ID、客户个人信息、密钥、完整原始行 JSON。需要原始行排查时查隔离区证据存储（`quarantine_records.raw_payload`），该存储有独立访问权限与保留规则，不属于普通日志。

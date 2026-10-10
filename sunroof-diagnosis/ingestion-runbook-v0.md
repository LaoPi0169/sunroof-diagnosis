# 摄取数据运维与隔离区排障 Runbook (Ingestion Runbook v0)

- **场景**：天窗总成只读诊断 —— QMS/PLM 检验规格数据摄取 (Ingestion Pipeline)
- **版本**：`ingestion-runbook-v0.1`
- **读者对象**：数据运维工程师、QMS 规格知识管理员
- **关联文档**：`specs/data-contract-v1.md`（字段与原因码定义）、`adr-002-ingestion-lineage.md`（设计决策）

---

## 1. 批次摄取状态排查流程

批次完成后，先查批次对账汇总与三处一致指标：

```sql
SELECT batch_id, source_id, source_version, total_rows,
       accepted_count, duplicate_count, quarantined_count, status
FROM ingestion_batches
WHERE batch_id = 'batch-node03-20261010-a';
```

**三处一致校验公式**（任何一条不成立即说明脏数据逃逸或漏记，立即停止摄取并告警）：

- `total_rows = accepted_count + duplicate_count + quarantined_count`
- `accepted_count = SELECT COUNT(*) FROM specs WHERE batch_id = '...'`
- `quarantined_count + duplicate_count = SELECT COUNT(*) FROM quarantine_records WHERE batch_id = '...'`

也可直接调用代码内对账函数 `ingestion.repository.get_three_way_reconciliation(batch_id)`，
返回 `three_way_consistent` 布尔值（tests/test_ingestion_reliability.py 已自动校验）。

**如何用 correlation_id 定位批次**：`sunroof.ingestion` logger 的全部结构化日志都携带
`correlation_id` 与 `batch_id`。拿到上游反馈的 correlation_id（如 `ing-node03-a-001`）后，
在日志中检索 `correlation_id=<值>`，取 `ingestion_batch_completed` 事件的 `batch_id` 与计数，
再回到上面的 SQL 核对。注意：日志只含 ID/计数/原因码，不含零件号、规格标题与项目组（§4）。

## 2. 隔离区 (Quarantine) 排查与错误分类

```sql
SELECT quarantine_id, batch_id, safe_source_ref, reason_code, status, replay_id, created_at
FROM quarantine_records
WHERE batch_id = 'batch-node03-20261010-a' AND status = 'quarantined';
```

原始行 JSON 在 `raw_payload` 列（仅存证据存储）。**排查时禁止把 raw_payload 粘贴到普通日志/群聊**。

### 常见原因码处置 SOP

| 原因码 | 根因分类 | 标准处置 SOP |
| --- | --- | --- |
| `MISSING_OWNER` | 缺少知识所有者 | 联系规格发布人确认归属 → 补齐 `owner_id` → 定向重放 |
| `ACL_SCOPE_MISMATCH` | 行级 ACL 声明缺失/越权/多元素 | 找权限所有者核对 → 纠正为单元素且与 `oem_project` 一致 → 重放（**绝不手工放宽**） |
| `INVALID_EFFECTIVE_RANGE` | 失效早于等于生效 | 修正日期（保证 `expires_at > effective_at`）→ 重放；反复出错则拒绝来源 |
| `UNIT_PROCESS_MISMATCH` | 单位与工序约定不符 | 扭矩工序用 `N·m`、间隙工序用 `mm`；纠正后重放 |
| `INVALID_PROCESS_STEP` / `INVALID_OEM_PROJECT` | 枚举越界 | 按数据合同枚举纠正后重放 |
| `INVALID_VALUE_RANGE` | 名义值/公差非法 | 名义值必须 > 0、公差 ≥ 0；与来源核对后重放 |
| `DUPLICATE_BUSINESS_KEY` | 业务主键重复 | 确认上游是否重复推送；主表已有正常记录则**无需重放**，保持隔离作对账痕迹 |
| `MISSING_BUSINESS_KEY` / `INVALID_SCHEMA` | 标识字段缺失 / 日期格式非法 | 补齐或改为 ISO-8601（YYYY-MM-DD）后重放 |
## 3. 定向重放 (Targeted Replay) 执行步骤

**谁有权修复**：`owner_id` 由 QMS 规格发布人定夺；`allowed_oem_projects` / `oem_project` 变更必须由权限所有者批准；日期字段由规格发布人确认。
**禁止直接手工 SQL 修改 `specs` 表**——必须走系统重放管道（`ingestion.service.execute_targeted_replay`）。

```python
from ingestion.schema import ReplayRequest
from ingestion.service import execute_targeted_replay

result = execute_targeted_replay(
    ReplayRequest(
        quarantine_id="quar-a1b2c3d4e5f6",
        replayed_by="knowledge-admin-qms",
        fixed_payload={"owner_id": "owner-qms-sr"},  # 支持部分补丁, 自动与原始行合并
        notes="已由 QMS 知识管理员补齐规格所有者",
    )
)
print(result.status, result.replay_id, result.accepted_spec_id)
```

**重放后数据库校验**：
1. `specs` 新增修复行，其 `batch_id = replay-{replay_id}`（可追溯到重放事件）；
2. `quarantine_records` 对应记录 `status='resolved'` 且回填 `replay_id`，原始 `raw_payload` 原样保留；
3. `ingestion_replay_logs` 落审计行（成功与失败的尝试都落）。

**失败处置**：补丁仍未通过数据合同校验时重放失败（隔离记录保持原状）；`rep-err-` 前缀的重放 ID 表示目标记录不存在或已 resolved，不产生假成功。

## 4. 日志内容边界 (Security Boundary)

普通结构化日志（logger `sunroof.ingestion`）字段白名单：事件名、`correlation_id`、`batch_id`、`source_id`、行引用 `row_ref`、计数、`reason_code`、`quarantine_id` / `replay_id`、结果状态。

**绝不写入**：零件号（`part_no`）、规格 ID/标题、来源引用（`source_ref`）、项目组标识（`OEM-A/OEM-B`）、计量单位、原始行 JSON——均为跨主机厂敏感内容。调试需要原始行时查隔离区证据存储，其访问受数据库权限约束。

## 5. 诚实边界

批次 `completed` 不等于数据质量完美、不等于用户已采用、不等于产生商业 ROI；`completed_with_quarantine` 是正常运营状态，隔离率是待跟进信号而非失败。MES/QMS/PLM 为 SQLite 合成夹具模拟。
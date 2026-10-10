# ADR-002: 摄取血缘与隔离区设计（阶段 A 节点 ③）

- **状态**：已接受（Accepted）
- **日期**：2026-10-10
- **关联**：`adr-001-deterministic-baseline.md`（确定性基线）、`specs/data-contract-v1.md`（数据合同）、`specs/discovery-brief-v1.md`（保护指标 P-1/P-2/P-3）
- **回链**：原仓库 Week 06「建立可靠、可重放的数据入口」工程惯例（`ingestion/` 三件套 + 迁移 002）

## 背景

节点 ② 的确定性薄片以「默认干净」的种子夹具运行。节点 ③ 只增加一个难度：**数据不再默认干净**。QMS/PLM 导出的规格行可能缺所有者、缺 ACL 声明、日期倒错、单位错配或重复推送；任何一条脏行都不允许静默进入主表（否则破坏 P-2/P-3 的确定性前提），也不允许静默丢弃（否则无法对账与修复）。

## 决策

1. **001 冻结表不动**：specs 主键、六条种子 Fixtures 与 `query_current_specs` 语义零改动；血缘列（`source_id` / `source_version` / `batch_id`）以幂等 `ALTER TABLE` 追加，种子行回填 `node02-baseline-seed`。
2. **服务层摄取，不新增 HTTP 面**：摄取经 `ingestion/service.py` 编排（批次登记 → 行级数据合同校验 → 主表/隔离区分流 → 单事务原子提交），与原仓库 Week 06 一致；只读诊断接口 `/api/v1/spec-check` 语义不受影响。
3. **稳定业务键去重**：业务键 = `spec_id:source_version`（绝不用行号）；`spec_id` 遵循版本化 ID 惯例，上游修订必须换发新 spec_id，同 spec_id 不同 source_version 判 `DUPLICATE_BUSINESS_KEY`。
4. **入口 Default-Deny（P-1 的第一道防线）**：行级 ACL 声明 `allowed_oem_projects` 必须与 `oem_project` 完全一致（单元素），缺失/越权/多元素一律进隔离区；`is_current` 缺失默认 0，绝不自动赋予现行效力。入口防线 + SQL 下推 + 查询过滤构成三层纵深。
5. **隔离区与重放历史绝不物理删除**：每条无效/重复行带原因码落 `quarantine_records`（`raw_payload` 仅存证据存储，不入普通日志）；定向重放以原子事务完成「主表写入（batch_id=replay-{replay_id}）+ resolved + 审计日志」，失败尝试同样落审计。

## 后果

- 正向：三处一致对账（批次汇总 = 主表行数 = 隔离区行数）可自动化；每条数据有可解释终态；为节点 ④（复核单闭环）提供带血缘的可信输入。
- 代价：specs 行数口径需带 `batch_id` 维度理解；同零件多现行版本不会被摄取层自动 supersede，冲突由 spec-check 的 `SPEC_CONFLICT` 判定表显式暴露（宁可拒答，不假装知道）。

## 否决的备选

- **重建 specs 表加列**：破坏 001 冻结与既有测试确定性，否决。
- **摄取层自动 supersede 旧版本**：替代关系应由 QMS 来源显式声明，自动改写已有行是隐藏写副作用，否决。
- **为摄取新增 HTTP 端点**：本阶段无运维调用方，先以服务层 + 测试证明可靠性，否决（后续部署节点可再评估）。
- **按行号去重/幂等**：文件重排后行号漂移，否决。
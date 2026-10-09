# 追踪断点地图 v1（trace-map-v1，第 16 周）

- 日期: 2026-10-09 · 回链: `week-16/runtime-sli-spec-v1.md` + `week-16/telemetry-contract-v1.md` ·
  实现: `mcptool/telemetry.py`（TaskTrace）+ `mcptool/run_telemetry_replay.py`（剧本）
- 验收入口: 业务用户工作台「任务追踪」面板（输入任一 task_run_id 还原全程）；
  自动化: `tests/test_telemetry_observability.py` test_01/test_06。

## 一、九阶段记录面（缺席也留痕）

| # | 阶段 | 记录什么（安全面） | 常见 not_applicable |
| --- | --- | --- | --- |
| 1 | request_received | 任务类型、case_id、split/family | — |
| 2 | identity_and_scope | 角色、租户（来自可信会话；不含凭据） | — |
| 3 | retrieval | corpus/dataset/retriever 版本 + 检索池 top 文档 ID | 写任务（上游场景提供 rag_terminal） |
| 4 | generation | candidate/prompt 版本 + 三态 state/reason_code | 写任务 |
| 5 | citation_and_policy_verification | 引用文档 ID、引用问题数、泄漏数 / policy_version | 读超时 |
| 6 | approval | approval_id + 决定枚举（approved/revoked/not_issued） | 读任务 |
| 7 | mcp_call | 工具名/版本、合同 sha、幂等键、工单号、verified_by、效果增量 | holdout 内容管线（案例不在只读工单库） |
| 8 | reconciliation | 幂等键、对账结果码、recovered_from | 无断连的写任务、全部读任务 |
| 9 | response_presented | 呈现给用户的终态码 | — |

**不留空洞**：剧本里不发生的阶段必须落 `not_applicable=true` 的 span，
并注明原因——「没记录」与「不适用」是两回事。

## 二、断点 #1：RAG → 策略判断的版本链可追

场景：一线发起复核单创建时，策略组件要求「RAG 终态必须已是 escalate」。
追问「这个 escalate 是哪套语料/哪个候选/哪版检索器判出来的」必须一路有答案：

- 读任务侧：retrieval 段 versions = {corpus_version: northstar-corpus-w09-v1,
  dataset_version: northstar-eval-w10-v1, retriever_version:
  vector-hashed-ngram-v1}；generation 段 versions = {candidate_id:
  northstar-rag-readonly-v1, prompt_version: none-deterministic-extractive-
  composer-v1}——与第 12 周冻结 manifest 逐项同值（test_01 断言）。
- 写任务侧：citation_and_policy_verification 段 versions = {policy_version:
  ticket-create-policy-v1}，safe_refs 携带 rag_terminal 与允许队列——
  上游 RAG 任务的版本链经场景输入传入（回放里以 upstream_scenario 标注，
  生产中应为上游 task_run_id 引用）。
- **断链即违规**：任何一段版本缺失/与冻结清单不符 → 该链路视为不可追，
  按遥测合同属缺陷（本版由测试兜底）。

## 三、断点 #2：MCP 返回可关联审批 + 幂等键（补传播不断链）

场景：`ticket.create` 的外部效果必须能回答「谁批的、哪次运行、哪张工单、
怎么核验的」：

- mcp_call 段 safe_refs = {tool: ticket.create,
  idempotency_key: `{task_run_id}:ticket.create:1`, ticket_id: SIM-xxxx,
  verified_by: ticket.get, effects_delta: 0|1}，versions = {tool_version: "1",
  contract_sha256: f9afabdc…e931f4}——幂等键由 task_run_id 派生，
  **传播天然不断链**（run_id 贯穿 预览/审批/执行/核验/对账）。
- approval 段 safe_refs = {approval_id: approval-{task_run_id}, decision: …}。
- 对账段（O-07/O-08 实测）：reconciliation 段记录 幂等键 + 对账结果码 +
  recovered_from=result_unknown + verified_by=ticket.get；
  查询不可用时如实记 `reconciliation_query_unavailable` + unconfirmed_effects，
  **禁止伪造终态**（test_05 断言）。

## 四、怎么用（复现一条链）

1. `python -m mcptool.run_telemetry_replay`（或工作台点「运行合成回放」）；
2. 取任一 task_run_id（如 `run-w16-<seed>-O-07`）；
3. 命令行：`run_replay` 返回的 spans 按 task_run_id 过滤；工作台：「任务追踪」
   面板下拉选择 → 九阶段表 + 版本链/审批/幂等键关联一览；
4. 对照验收：任一 task_run_id 能还原「哪条路径、哪些版本、谁批的、效果几何」
   ——两断点可追即通过（手册 3667–3888 验收门第 3 条）。

# 只读工具测试报告 v1 (Week 13, 第 4-5 天)

- 工具: `case.lookup_context@1` (合同 week-13/mcp-read-contract-v1.json, sha256 `882ccd8ba512c10a5b2126ffe0376cab7104d59bd5d5bb3f3e19b40dfb70dbe2`)
- 运行日期: 2026-10-08 · 运行方式: `python -m mcptool.run_fixtures` / `python -m mcptool.run_regression`
- 分母: 夹具 8 (R-01..R-05 + X-01..X-03) · 回归种子 13 (SR-12-01..13) · 单测 10 (全仓 56→66)
- 确定性口径: `read_at` 用固定时钟 `2026-10-08T00:00:00Z`; R-04 的 elapsed_ms 为真实耗时
  (本次观测 `650.3`, 每次运行略有浮动但恒 > 500ms deadline) —— 引用时注明口径。

## 一、夹具逐条观察 (8/8 通过)

| 夹具 | 条件 | 预期 | 观察 | 外部写效果 |
|---|---|---|---|---|
| R-01 | 一线读本租户普通工单 NS-1042, 请求 3 枚举字段 | ok; 恰好 3 字段; 带来源 | `status=ok`; context=`{task_type: plan_upgrade_price_difference, region: CN, purchased_at: 2026-08-11T03:20:00Z}`; `record_version=case-v7`; 8/8 分项检查通过 | 0 |
| R-02 | 请求字段含 supervisor_note (禁止字段) | invalid_argument; 无备注内容 | `{"code":"invalid_argument","message":"请求字段不允许"}`; 响应无 records、无备注文本; 细节 `schema:…` 只进受限审计 | 0 |
| R-03 | 不存在 NS-9999 / 跨租户 NS-3001 / 角色不可见 NS-5001 | 三者用户面逐字节一致 | 用户面封套均为 `{"error":{"code":"not_available","message":"对象不可用或无权访问"},"status":"error","tool_version":"1"}` (sort_keys 序列化逐字节相同); 受限审计三类原因可区分: `object_not_found` / `object_in_other_tenant` / `object_not_visible_to_caller`, 且均不出现在用户面 | 0 |
| R-04 | 服务端延迟 0.65s > deadline 500ms | deadline_exceeded; 无上下文字段 | `{"code":"deadline_exceeded","message":"暂不可用，请稍后重试或转人工"}`; 受限审计 `elapsed_ms:650.3>500`; 响应无 records/无 task_type/region/purchased_at 值 | 0 |
| R-05 | 失控服务端返回 2 条记录 (超 result_limit.records=1) | contract_violation; 整体拒绝 | `{"code":"contract_violation","message":"结果超出合同，已整体拒绝并记录安全事件"}`; 安全事件 `{"violation":"records_exceed_limit:2>1","server_identity":"northstar-mcptool-case-server@local"}` 入账; 违规数据 (2026-08-11) 未出现在用户面 | 0 |
| X-01 | 参数走私 tenant_id/role | invalid_argument; 服务端零调用 | `invalid_argument`; `server_calls=0`; 会话身份未被覆盖 (`schema:tenant_id 属于可信会话…` 只进审计) | 0 |
| X-02 | 请求枚举外字段 nickname | invalid_argument; 服务端零调用 | `invalid_argument`; `server_calls=0` | 0 |
| X-03 | 失控服务端报 tool_version=2 | version_mismatch | `{"code":"version_mismatch","message":"工具版本不兼容，本次调用已停用"}`; 审计 `tool_version:2`; 结果整体作废 | 0 |

运行器总结 (原样):

```json
{
  "fixtures_total": 8,
  "passed": 8,
  "failed": 0,
  "total_external_write_effects": 0,
  "store_immutable": true,
  "note": "只读合同: 外部写效果恒 0; 工单库快照运行前后逐字节一致"
}
```

`--only` 单夹具通道验证: `python -m mcptool.run_fixtures --only R-03` 单独运行通过 (R-03 的
三分支不可区分性断言独立成立)。

## 二、13 条种子工具在环回归 (13/13 identical)

流程: 每种子以种子角色构造可信会话 → 工具取回 task_type/region/purchased_at →
冻结映射 (TASK_TYPE_TO_CATEGORY) 还原 category 与 eval-set 转写逐字段对账 →
用工具字段重构 task_context 跑第 12 周冻结管线 → 与 locked_behavior 逐字段对照。

| 种子 | 工单 | 角色 | 状态 | 映射对账 | 锁定行为对照 | 结论 |
|---|---|---|---|---|---|---|
| SR-12-01 | NS-DEV-01 | agent | answered | 3/3 一致 | state/citations_include/expected_state ✓ | 一致 |
| SR-12-02 | NS-DEV-19 | agent | answered | 3/3 一致 | state/citations_include/expected_state ✓ | 一致 |
| SR-12-03 | NS-DEV-14 | agent | answered | 3/3 一致 | state/expected_state ✓ (版本对比豁免保持) | 一致 |
| SR-12-04 | NS-CAL-05 | supervisor | answered | 3/3 一致 | state/expected_state ✓ | 一致 |
| SR-12-05 | NS-DEV-07 | agent | abstained | 3/3 一致 | state/reason_code/expected_state ✓ | 一致 |
| SR-12-06 | NS-CAL-03 | agent | abstained | 3/3 一致 | state/reason_code/expected_state/full_top_in_pool=false ✓ | 一致 |
| SR-12-07 | NS-DEV-25 | agent | abstained | 3/3 一致 | state/reason_code/mapped_state/conflict_signal=radar_dominant ✓ | 一致 |
| SR-12-08 | NS-CAL-08 | supervisor | abstained | 3/3 一致 | …/conflict_signal=query_guard ✓ | 一致 |
| SR-12-09 | NS-DEV-26 | agent | abstained | 3/3 一致 | …/conflict_signal=query_guard ✓ | 一致 |
| SR-12-10 | NS-DEV-30 | agent | abstained | 3/3 一致 | …/expired_signal=query_guard ✓ | 一致 |
| SR-12-11 | NS-DEV-32 | supervisor | abstained | 3/3 一致 | …/expired_signal=radar_dominant ✓ | 一致 |
| SR-12-12 | NS-DEV-08 | agent | escalate | 3/3 一致 | state/reason_code/expected_state/citations=[]/leak=0 ✓ | 一致 |
| SR-12-13 | NS-DEV-12 | supervisor | answered | 3/3 一致 | state/citations_include=refund-supervisor-v2 ✓ | 一致 |

总结 (原样): `"seeds_total": 13, "identical": 13, "mismatches": [], "verdict": "13/13 identical",
"external_write_effects": 0, "pipeline_unchanged": "generation/* 与第 12 周落盘结果零改动"`。
SR-12-03/04 的 `guard` 字段为自由文本描述, 不做结构化断言 (随报告透传)。

## 三、写效果与不可变性

- 每夹具 `external_write_effects=0` (分母 8, 计数 8, 违规 0); 回归器同值 0;
- 工单库快照 (sort_keys JSON) 夹具运行前后逐字节一致 (`store_immutable=true`);
- 设计级: server/case_store 模块无 write/create/update/delete/mutate 入口 (单测 08 锁定);
- 仓库级: 夹具与回归运行后 `git status --short` 干净 ( __pycache__/data 均在 .gitignore)。

## 四、受限审计样本 (restricted —— 引用需权限)

- R-03 三分支 typed_reasons: `object_not_found` / `object_in_other_tenant` /
  `object_not_visible_to_caller` —— 同因异因用户面不可区分, 审计可追责;
- R-05 安全事件: `records_exceed_limit:2>1` (run_id=mcp-001, server_identity 在案);
- 审计条目结构: run_id / tool / caller(tenant,user,role) / outcome / reason_typed /
  external_write_effects (单测 10 锁定)。

## 五、取消条件对照 (task-tool-decision.md §四)

| 取消条件 | 实测 | 触发? |
|---|---|---|
| 写效果非 0 / 库快照变化 | 全 0, 快照一致 | 否 |
| 无权限响应可区分 / 泄露受限内容 | R-02/R-03/R-05 均安全 | 否 |
| 13 种子回归不一致 | 13/13 identical | 否 |
| 身份参数走私成功 | X-01 Host 层拒绝, 服务端 0 调用 | 否 |

→ 决定: 继续 (continue_with_limits), 已记入能力清单 decision。

## 六、诚实边界

- 教学实现为进程内确定性三方切分; rogue 服务端 (R-05/X-03) 与慢速服务端 (R-04) 为
  夹具注入, 非真实故障;
- R-04 elapsed_ms 每次运行浮动 (恒 >500ms, sleep 下限保证);
- 保留集不运行; RAG v1 no-go 不因工具周推翻; 政策正文/凭据零出现 (CI 阶段 3 扫描 29 文件)。

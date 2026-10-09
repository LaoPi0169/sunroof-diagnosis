# 恢复复验报告 v1（recovery-verification-v1，第 17 周）

- 日期: 2026-10-09 · 对象: MCP 不可用降级（注入路 + 开关路）的恢复复验 ·
  运行器: `python -m mcptool.run_degradation_drill`（D-05a/b/c 固定段）+
  `tests/test_degradation_week17.py` test_09/test_10/test_11
- 口径（手册 3979）: **只检查进程、端口或健康接口，不算完成恢复** ——
  下表六检查全部落在「任务结果」上。

## 一、六项恢复检查 → 证据映射（手册 3971-3978 逐条）

| # | 手册检查项 | 证据 | 结果 |
| --- | --- | --- | --- |
| 1 | 原来的关闭开关已按批准方式复位 | D-05b: `registry.clear(SWITCH_MCP_OFF, operator=ops-li-w17, reason=按批准流程复位…)` -> `closed` 事件入追加式审计；`is_active=false` | 通过 |
| 2 | 正常任务能再次完成 | D-05a/D-05b: 同一 run_id + 同一审批续跑 -> `action_completed`，`verified_by=ticket.get`，各恰 1 效果；test_09/test_10 | 通过 |
| 3 | 受限任务仍然拒绝 | D-05c: 恢复后 `executive-review`（策略外队列）-> `approval_denied`，0 效果；test_11；MCP_OFF 激活期无审批执行仍 `approval_required`（test_04） | 通过 |
| 4 | 原故障任务没有重复效果 | D-05a: 续跑后幂等重放 -> 返回同一工单 SIM-5001，工单总数仍 1；深层缝场景 test_06: 依赖修复后重放仍恰 1 | 通过 |
| 5 | 同一任务运行标识能还原故障、降级、复验 | `northstar-w17-0003` 一条 task_run_id 串联: D-03 降级段（`action_preview` + `mcp_unavailable`）-> D-05a 复验段（`action_completed`，safe_refs `recovery=retry_later_after_restore`）；九阶段 trace 可还原（工作台任务追踪面板可逐步查看） | 通过 |
| 6 | 普通日志没有提示全文、政策正文、凭据或审批载荷 | 演练 73 段 span 过 `scan_spans_for_violations` 0 违规；STAGE-3 全范围扫描 48 文件 0 违规；操作者/理由只进开关注册表与 Host 受限审计 | 通过 |

## 二、降级期间与恢复后的不变量（自动化锁定）

| 不变量 | 断言位置 |
| --- | --- |
| 降级封套外部效果恒 0，store 快照逐字节不变 | test_03/test_05/test_07 |
| 降级不消耗审批（主缝），恢复后同一审批可直接续跑 | test_03（uses=0）-> test_09（同一审批续跑完成） |
| 不自动重试写：一次用户动作恰一次探测 | test_05（probe.attempts=1）；D-03 `no_retry_evidence` |
| 深层缝消耗如实入账，幂等键保证修复后无第二个效果 | test_06 |
| 权限/确定性错误不被降级吞掉 | test_04（approval_required / approval_revoked / approval_denied 原样） |
| 降级终态携带效果 = 合同违规被 SLI 抓住 | test_08（forbidden_effect_count=1） |
| 第 15 周 A-06 对账语义零退化 | test_15（冻结合同 sha256）+ D-06（fail_after_commit -> reconciliation -> completed） |

## 三、工作台可复验路径（业务用户可独立完成）

`workbench_degradation/`（端口 8017）：注入故障 -> 执行（看 action_preview +
工单数不变）-> 解除注入 -> 续跑同一任务（看 action_completed + 工单数 +1）->
幂等重放（看计数不变）-> 任务追踪下拉还原降级段与复验段。逐步白话操作见
`week-17/degradation-plain-guide-v1.md`；逐步验收记录单见
`week-17/business-user-test-script-v1.md`（W-06 恢复复验）。

## 四、边界

1. 复验窗口内审批有效期 15 分钟（`APPROVAL_TTL_SECONDS`）——真实系统里
   「降级-恢复」跨度过长时续跑需重新确认，本演练用固定时钟压缩在窗口内；
2. 「同一 run_id 续跑」依赖幂等键 `{task_run_id}:ticket.create:1`（第 14 周
   冻结语义）；换 run_id 重做 = 新任务，不享受幂等保护；
3. 本报告是教学/授权环境证据，不宣称生产恢复能力已达标。
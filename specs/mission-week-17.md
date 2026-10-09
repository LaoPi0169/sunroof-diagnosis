# Mission Week 17 —— 让依赖故障进入安全降级路径（关闭开关 × 降级终态 × 恢复复验）

- 日期: 2026-10-09 · 分支: week17-degradation → main（--no-ff）
- 上游: main `01808a0`（week16 合并修复后）
- 手册章节: specs/business-background.md 3889-4004「第 17 周：让依赖故障
  进入安全降级路径」

## 一、来源与必要性（手册 §为什么「报错」不等于安全失败）

服务器返回错误只说明某个技术步骤没完成；FDE 还要回答：用户任务现在什么
状态？有没有未经验证的答案或未授权效果？用户下一步能做什么、谁接手？
依赖恢复后怎样证明业务任务真的恢复？最小模型：依赖故障 → 分类（瞬时/
确定性/权限拒绝/结果不明确）→ 选择（重试/降级/停止/对账）→ 写入用户可见
任务状态 → 恢复后重跑并核对真实结果。本周只独立实现一种降级（MCP 不可用，
手册 3955：它同时训练用户提示、关闭开关与「零外部效果」核验），模型/索引
用固定演练观察（手册 3968：不在同一周重写三套恢复机制）。

## 二、交付物（手册 §本周产物 + Web 交互交付）

| 交付物 | 内容 |
| --- | --- |
| week-17/failure-behavior-contract-v1.md | 三类故障 × 七列表 + 降级封套合同 + 四类错误→动作映射（权限拒绝保持拒绝）+ SLI 口径增补 v1.1（第八类可见终态 action_preview）+ 红线 |
| week-17/kill-switch-registry-v1.md | MCP_OFF/MODEL_OFF/INDEX_STALE 登记、操作纪律（无因操作未批准）、追加式审计、两条降级路对照 |
| week-17/degradation-drill-records-v1.md | 8 段固定演练记录（7/8 allowed、forbidden 0、效果恰 3、脱敏 0 违规）|
| week-17/degradation-drill-evidence-v1.jsonl | 演练机读证据（86 行：73 span + 8 演练行 + SLI + 4 开关审计）|
| week-17/recovery-verification-v1.md | 恢复六检查→证据映射 + 不变量自动化锁定表 |
| week-17/degradation-runbook-v1.md | 非作者可复现 runbook（注入→降级→恢复→复验全程 + 异常处置分级）|
| week-17/degradation-plain-guide-v1.md | 业务用户白话操作指南（四个轮次 + 常见疑问）|
| week-17/business-user-test-script-v1.md | 业务用户逐步验收脚本 W-00..W-07 + 记录单 |
| mcptool/kill_switch.py | KillSwitchRegistry（封闭枚举/审计/幂等/快照不可变）+ WriteChannelProbe（注入缝，attempts 证不重试）|
| mcptool/client.py | _degradation_gate（判定优先级：权限判定优先于降级）+ _degraded_preview（action_preview 降级封套）+ 深层缝捕获 |
| mcptool/ticket_service.py | McpUnavailableError + UnavailableWriteTickets（纵深防御第二缝）|
| mcptool/telemetry.py | 第八类可见终态 action_preview（v1.1）+ forbidden_effect_count 覆盖降级终态；既有六类语义零改动 |
| mcptool/run_degradation_drill.py | D-01..D-06 固定演练运行器（固定时钟/固定 task_run_id）|
| workbench_degradation/ + start bat | 业务用户降级工作台（端口 8017）：正常对照组/注入/开关/续跑/幂等重放/一键演练/任务追踪/脱敏，ES5 兜底哨兵 |
| tests/test_degradation_week17.py | 16 用例（全量 106 → 122）|
| ci/redaction_scan.py | SCOPE_FILES 44 → 48 |
| README 第 17 周段 / ci-evidence §十三 / evidence/ci-run-w17.log | 运行与证据 |

## 三、设计决定

1. **两条路一个终点**：演练注入（WriteChannelProbe）与关闭开关（MCP_OFF）
   在编排层同一个 `_degradation_gate` 汇合，落同一 `action_preview` 终态 ——
   用户面 reason_code 恒为 `mcp_unavailable`，触发源只进受限审计；
2. **判定优先级**：允许列表 → 能力清单 → schema → [审批当前有效] → 降级 →
   服务端。降级只替代「依赖故障」，绝不吞掉确定性/权限错误（负例：
   MCP_OFF + 无审批 → approval_required，test_04 锁定）；
3. **主缝不消耗审批**：降级判定在服务端消耗额度之前，恢复后同一审批直接
   续跑（test_03→test_09 闭环）；深层缝（先消耗后写才失败）如实入账，
   幂等键保证修复后重放无第二效果（test_06）——「如实」优先于「好看」；
4. **终态词表 v1.1 增补而非改写**：新 outcome `action_preview_held` ->
   新可见终态 `action_preview`（允许）；`classify_terminal("write",
   "action_preview")` 保持非终态 —— 正常流程预览与降级停驻词面分开，
   week-16 基线/回放/12 用例零改动全绿；
5. **forbidden_effect_count 扩展**：降级终态携带效果 = 违规（「停在预览」
   的定义就是 0 效果），SLI 层照抓（test_08）；
6. **工作台每任务一个独立「外部世界」**：第 14 周冻结核「预览封套外部
   计数必须为 0」意味着效果发生后不能再出预览 —— 工作台按任务隔离
   store（与第 16 周回放 _fresh_write_stack 同构），开关注册表与注入缝
   系统级跨任务保持；降级任务未收口前禁止新预览（409 引导先复验）；
7. **白话交付**：degradation-plain-guide-v1.md 用「购物车订单/付款」类比
   讲预览与效果，判定标准全部落在页头三个计数上（工单总数/外部效果/
   当前任务），业务用户不需要理解任何内部术语即可完成验收。

## 四、验收门映射（手册 3888-4004 → 证据）

| # | 验收门 | 证据 |
| --- | --- | --- |
| 1 | 故障行为合同（三类 × 七列表 + 决策规则） | failure-behavior-contract §一/§三 |
| 2 | 关闭开关登记 | kill-switch-registry 全文 + 演练 4 条审计样例 |
| 3 | 三类故障都进预定义终态，无无限重试 | drill-records §一；test_12/13/14；probe.attempts=1 |
| 4 | 权限拒绝、确定性错误、结果不明确采用不同动作 | contract §三映射表；test_04；D-06 |
| 5 | 独立实现一种安全降级（MCP 不可用） | client._degradation_gate/_degraded_preview；test_03/05/06 |
| 6 | 降级不绕过 ACL/审批/审计 | test_04/test_07 + 开关审计 + Host 审计 reason_typed |
| 7 | 写结果不明确先查询/对账 | D-06 回归 pass + test_15 冻结 sha |
| 8 | 任务运行标识还原故障、降级、恢复、最终状态 | D-03/D-05a 同 run_id 双段 trace；工作台任务追踪 |
| 9 | 恢复后重跑用户任务（不只查服务） | recovery-verification 六检查表；test_09/10/11 |
| 10 | 普通日志无秘密/敏感正文 | 演练 73 span 0 违规；STAGE-3 48 文件 0 违规 |
| 11 | 非作者可复现（runbook 实际演练） | degradation-runbook 全步骤 + business-user-test-script W-00..W-07 |

## 五、运行结果（2026-10-09 真机）

- `python -m mcptool.run_degradation_drill --out
  week-17/degradation-drill-evidence-v1.jsonl`：8 段全 pass；
  allowed 7/8、forbidden 0、unconfirmed 0、效果合计 3、73 span 脱敏 0 违规；
- `python -m unittest discover -s tests`：**Ran 122 tests, OK**（106 → 122）；
- `python ci/run_local_ci.py --evidence-file evidence/ci-run-w17.log`：四阶段
  PASS（详见 ci-evidence §十三）；
- 工作台 `workbench_degradation/`（端口 8017）：16 用例中 test_16 覆盖
  正常对照组→注入降级→续跑→重放→开关→演练→追踪→脱敏全链路 API。

## 六、诚实边界

1. 降级编排是**教学进程内实现**：真实系统需要连接池/熔断器/重试预算等
   工程件 —— 本周练的是分类决策与安全终态纪律，不是网络工程；
2. MODEL_OFF/INDEX_STALE 只完成登记 + 演练门，生产读路径降级编排不在本周
   （手册 3968）；INDEX_STALE 复用既有 abstain 机制，不是新的新鲜度检测器；
3. 「模型超时 → system_failed」是纪律等价映射（无验证不出答案），不是
   手册 partial/search_only 终端的复刻 —— 本仓库读路径没有 partial 产物态；
4. 开关的批准=填写操作人与理由；真实系统需双人在岗批准、复位冷静期、
   持久化与告警联动；
5. 演练 7/8 是固定剧本演示口径，不是 SLO 达标声明；两视图分母永不合并。
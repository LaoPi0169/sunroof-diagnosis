# 冷交接记录 v1（Cold Handoff Record · 第 18 周）

```yaml
handoff_mode: B (代理冷启动 —— 作者按「非作者视角」盲走交接包调通交接材料)
handoff_evidence: simulated_self_replay   # 手册 5902-5904: 无真实非作者时必须如实标注
handoff_gate: pending                     # 真实非作者按 A 路复验后才改 pass
operator_of_record: 值守-甲(代理)          # 代理值守; 作者同时在旁 = 调包态, 非收口态
workbench: workbench_handoff (8018)
sealed_pack: week-18/sealed-fault-pack-v1.md
date: 2026-10-10
```

> **定位**：本轮是「调包」——用代理冷启动把交接材料本身的坑找出来改掉（见
> `runbook-revision-log-v1.md`）；它**不是**收口证据。A 路（真实同伴独立复验）
> 完成后本文件由复核人追加一段 A 路结果并改 `handoff_gate: pass`。

## 轮 1 · F-18.1 MCP 写通道不可达（注入路）

| 观察点 | 实测 |
| --- | --- |
| 业务执行（预览→批准→执行） | 封套 `status=action_preview`、`degraded=true`、`reason_code=mcp_unavailable` |
| 外部效果 | 0（累计效果计数不变; 审批未消耗） |
| 读路径 | 问答仍 `answered`（读不受阻, 手册 4034） |
| 解除后同一任务续跑 | 「稍后重试」→ `action_completed`，外部效果恰 +1 |
| 幂等重放 | `effect_count_unchanged=true`，工单总数 1（无重复效果） |

**归因**：外部依赖故障 → **不回滚**（回滚修不了外部依赖）。
**动作**：解除注入（留痕 fault_audit: inject → clear，operator=值守-甲(代理)）。
**复验**：续跑恰 1 效果 + 重放计数不变 —— 通过。

## 轮 2 · MCP_OFF 开关路（与注入路同一安全终态）

| 观察点 | 实测 |
| --- | --- |
| 开关动作 | `event=opened, switch=MCP_OFF`，审计含 operator+reason（无因动作会被服务端 400 拒绝, 由测试锁定） |
| 业务执行 | 同样落 `action_preview / mcp_unavailable`（两条路同一终态, 手册 3924） |
| 复位后续跑 | `action_completed`，外部效果恰 +1 |

**结论**：注入与开关两路落在同一安全终态；开关处置留痕可复盘。

## 轮 3 · F-18.2 配置回归 → 回滚完整版本包

| 观察点 | 实测 |
| --- | --- |
| 症状（业务可见） | 正常问答 `answered` 但引用 `billing-general-v1 · v1 (已废止)` + 黄色警告「疑似配置回归」 |
| 四+1 复验（回滚前） | **fail**：1 正常查询 ✗（引用废止版本）；2 拒答 ✓；3 升级 ✓；4 写入 ✓；5 幂等对账 ✓ |
| 归因 | 发布自身配置回归（rc-18.1 现行口径绑定错），非外部依赖 → **关开关修不了** |
| 动作 | 「回滚完整版本包」`rolled_back: rc-18.1 → rc-18.0`，审计含 operator+reason+at |
| 业务复验 | 问答引用恢复 `billing-general-v3 · v3 (现行)`, `current=true` |
| 四+1 复验（回滚后） | **pass 5/5**（observed: answered 依据=billing-general-v3 / abstained / escalated / action_completed 效果增量=1 / 重放后计数=1） |

## 六项观察记录（判断表）

| # | 观察 | 归因 | 动作 | 为什么 | 复验 |
| --- | --- | --- | --- | --- | --- |
| 1 | 写停 action_preview, 0 效果 | 外部依赖不可达 | 解除注入/开开关（带理由） | 隔离即可, 读不受阻 | 续跑恰 1 效果 + 重放不变 |
| 2 | 开关路与注入路终态一致 | 编排降级缝统一 | 复位按批准流程 | 审计可复盘 | 复位后续跑 1 效果 |
| 3 | 问答引用废止版本 | 发布配置回归 | 回滚完整版本包（带理由） | 关开关≠回滚; 六类绑定整包对齐 | 引用恢复现行 + 四+1 5/5 |
| 4 | 回滚前四+1 第 1 行 fail | 复验口径有效 | 以 fail 为证据先回滚 | 「健康接口正常」≠恢复 | 回滚后 5/5 pass |
| 5 | 遥测脱敏 | — | `redaction-check`：16 事件 0 违规 | 凭据/正文/载荷零出现 | 通过 |
| 6 | 口令零泄漏 | — | 全部状态/审计/响应动态断言不含口令 | 秘密只用于比对 | 通过（passcode_leak_check=true） |

## 调包期发现并已修复的问题（交接材料质量改进）

1. 预览调用缺 `rag_terminal` 导致策略判 `approval_denied`（交接包第 0 步就会卡死
   值守）→ 已修复为固定传入升级终态；并把该坑写进 runbook 修订日志；
2. 开关/回滚动作空理由原被默认理由兜底 → 改为服务端强制 400 `reason_required`
   （与「无因操作未批准」纪律一致）；
3. 业务页问答在回归期只给生硬答案 → 增加黄色警告与「勿按此口径承诺」指引；
4. 管理页补「关开关 ≠ 回滚」常驻警示条，防值守第一反射选错动作。

## 诚实边界

- 本轮全部数据来自进程内教学模拟（版本包/问答为登记模拟, 非真实制品库）；
- 代理值守由作者扮演, **不能**证明「非作者可独立接管」——那是 A 路的事；
- 四+1 复验中「写入/对账」两路径的夹具使用独立外部世界（fixture=true 标注），
  与业务真实任务的 effects_total 分账, 不混淆两个数。

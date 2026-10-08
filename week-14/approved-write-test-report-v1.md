# Approved Write Test Report v1 —— 审批写工具测试报告（Week 14）

- 日期: 2026-10-08 · 范围: `mcptool/` 写路径 + `week-14/` 合同与策略 + `tests/test_mcp_write_tool.py`
- 上游: `week13-baseline`（d562cb7，工作区干净）；RAG v1 no-go 维持，generation/* 零改动
- 手册: specs/business-background.md 3197-3436（第 14 周）· 产物对照 3404-3413

## 一、运行清单（2026-10-08 真机，全部实际执行）

| 命令 | 结果 |
|---|---|
| `python -m mcptool.demo_write_approval` | 四次尝试对账 0→1→1→1；SIM-5001；规范化演示（大小写/排序） |
| `python -m mcptool.run_fixtures --suite write` | **5/5**，新增外部效果合计 **1**（恰为 W-04），`read_store_immutable=true` |
| `python -m mcptool.run_fixtures`（read） | **8/8**，写效果 0，`store_immutable=true`（第 13 周行为零变化） |
| `python -m mcptool.run_regression` | 13/13 identical，mismatches=[]，写效果 0 |
| `python -m unittest discover -s tests` | **Ran 78 tests, OK**（66 → 78；含第 8 周文档一致性门，README 已同步 66→78） |
| `python ci/run_local_ci.py --evidence-file evidence/ci-run-w14.log` | 四阶段 PASS（见 ci-evidence §九） |

## 二、写负向夹具逐条（手册 3375-3381 表逐行对账 + 拒绝终态）

| # | 条件 | 期望终态 | 实测 | 新增效果 | 关键审计 reason_typed（restricted） |
|---|---|---|---|---|---|
| W-01 | 没有审批直接执行（预览后跳过确认） | approval_required | ✓ 预览 action_preview（计数 0）→ 执行被拒 | 0 | `approval_id_missing` |
| W-02 | 审批已过期（签发 22:00:00Z，TTL 900s，现在 00:00:00Z） | approval_expired | ✓ 用户面无有效期细节 | 0 | `approval_expired` |
| W-03 | 审批后把队列改成 executive-review 复用原审批 | approval_mismatch | ✓ 原审批 uses 保持 0；executive-review 语法合法但绑定不符 | 0 | `binding:approved_parameters` |
| W-04 | 完全匹配执行后同幂等键重复 10 次 | 返回同一工单，总数 ≤1 | ✓ 首次创建 SIM-5001，10 次重放同 ID 同状态，审批 uses=1 | **1** | `ok:write_verified` / `idempotent_replay` |
| W-05 | 用户在确认界面拒绝（随后违规执行尝试） | 拒绝为正常终态；执行被拒 | ✓ `denial-w14-001-1` 不可执行；未被自动重问 | 0 | `approval_decision_denied` |

Host 写通道硬门另有两例实测：失控预览封套声称外部计数 5 → `contract_violation`
（安全事件 `preview_nonzero_effects`）；错误封套伴随效果增量 → `contract_violation`
（`error_envelope_with_effects`，test_03/02 覆盖）。

## 三、四次尝试的外部状态（demo 实测）

| 尝试 | 审批与参数 | 结果 | 模拟工单总数 |
|---|---|---|---|
| 1 只生成预览 | 无审批 | action_preview | 0 |
| 2 完全匹配后执行 | 有效逐次审批 `approval-w14-001-1` | 创建 **SIM-5001**（`run-w14-001:ticket.create:1`） | 1 |
| 3 改队列 executive-review 用原审批 | 绑定不符 | approval_mismatch | 1 |
| 4 重放相同幂等键 | 相同动作 | 返回原 SIM-5001 | 1 |

## 四、规范化证据（先规范化再审批/展示/策略）

原始输入（demo/夹具故意带噪声）→ 单一函数输出：

```
reason_code:  "policy_conflict"            -> "POLICY_CONFLICT"
source_refs:  ["upgrade-notice-v4", " upgrade-east-v2"]
                                            -> ["upgrade-east-v2", "upgrade-notice-v4"]
target_queue: "Billing-Supervisor-Review"   -> "billing-supervisor-review"
case_id:      " NS-1042 "                  -> "NS-1042"
```

同集合不同表示 → 同一规范参数（test_06 等价分支：审批按表示 A 签发、以表示 B 执行 → 放行）；
实质变化（队列改 executive-review）→ `approval_mismatch`（W-03）。两分支由同一比对路径区分。

## 五、审批与核验链（样例工件，均为固定时钟实际运行导出）

- `week-14/approval-record-sample-v1.json`：拒绝记录 + 批准记录（`approval-record-v1`
  十要素），属**受限审计面**（含批准载荷，不入普通日志）；
- `week-14/effect-audit-sample-v1.jsonl`：按 `run-w14-001` 串联
  `previewed → approval_denied(首次拒绝) → approved → policy_allowed →
  write_requested → write_returned → external_state_verified → completed →
  mismatch → replay` 十事件，逐事件带效果计数；
- 普通日志边界：用户面封套只有状态/错误码/安全文案（W-02 无 `expires_at`、
  夹具输出无 `approved_parameters`/`policy_version`，test_12 断言）+ CI STAGE-3
  34 文件扫描 0 违规；细分 `reason_typed` 只进受限审计。

## 六、隔离与冻结物对账

- 只读工单库 `case_store`：写夹具运行前后快照逐字节一致（`read_store_immutable=true`）；
  `ticket_service` 为唯一写 store，不 import 不修改 case_store；
- 13 种子工具在环回归 13/13 与第 12 周逐字段一致；只读夹具 8/8 不受扰（test_11）；
- generation/*、第 12 周评测工件、week-13 冻结物零改动（全量 78 测试含既有冻结对账用例全绿）；
- 写合同 `week-14/ticket-write-contract-v1.json` 先冻结：sha256
  `486b93623135c2f12feb17649340d2b351f2d8002a8ea177eb9c59b906b1b9b4`，与
  `write_contract.CONTRACT_SHA256` 常量互锁（test_01）。

## 七、验收门映射（手册 3418-3428 十一条 → 证据）

| # | 验收门 | 证据 |
|---|---|---|
| 1 | 写操作对应已确认的一线异常路径，不为展示 Agent | 政策冲突升级主管复核 = 第 12/13 周已证实的 escalate 终态；state-and-effect-map §一；task-tool-decision 决策链延续 |
| 2 | 预览产生 0 个外部业务效果 | preview 物理隔离（不调用写方法）+ 预览前后计数断言 + Host 硬门 `preview_nonzero_effects`（test_02） |
| 3 | 对话文本或模型状态不能替代审批记录 | approval 只收结构化 issue/denial；聊天文本无入口；effect-classification §一.1 |
| 4 | 参数先规范化，再策略/展示/审批 | 单一函数三处共用；demo 规范化演示；test_06 等价与不等价两分支 |
| 5 | 审批绑定身份/运行/工具版本/参数/范围/策略/期限/次数 | approval-policy §三 十要素表；`server.create` 逐项比对（W-03 实测） |
| 6 | 拒绝、过期、身份或参数实质变化产生 0 效果 | W-05/W-02/W-01/W-03 增量全 0；Host「错误封套带效果即 contract_violation」 |
| 7 | 执行前重新鉴权，不只预览时检查 | `server.create` 步骤 1-3 重读审批 + 绑定比对 + 执行时策略再判定（预览缓存不被信任） |
| 8 | 相同幂等键最多一个外部效果 | 幂等表 + 重放先于用尽；W-04 十次重放总数恒 1 |
| 9 | action_completed 由目标系统核验，非模型宣称 | `verified_by=ticket.get`（get 独立再读）；封套硬门 `completed_not_verified_by_ticket_get`（test_09/10） |
| 10 | 审计可追溯但普通日志无敏感正文/批准载荷 | effect-audit-sample 按 run_id 串联十事件；test_12 脱敏断言；STAGE-3 34 文件 0 违规 |
| 11 | 所有写入只发生在隔离模拟服务 | `TicketService` 实例即唯一写点；case_store 快照不变；无网络无真实系统（§六） |

## 八、诚实边界

1. 教学实现为进程内确定性三方切分；「外部系统」= 隔离 `TicketService` 实例，无网络；
2. `result_unknown` 只保留状态口径（超时安全文案指向对账），断连对账/撤销/补偿第 15 周；
3. 审批样例含批准载荷，属受限审计面 —— 本报告与样例文件本身不应进入普通日志渠道；
4. 「请求人逐次确认」仅适用于本低影响效果（effect-classification §诚实边界）；
5. 手册完成例（run-021/SIM-5007/POL-17@v3）为北辰示例值；本仓库实测值（run-w14-001/
   SIM-5001/upgrade-east-v2+upgrade-notice-v4）来自自有冻结语料，schema 同构、互不冒认。

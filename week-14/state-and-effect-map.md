# State & Effect Map —— 提议到效果的唯一确定性路径（Week 14 Day 1）

- 日期: 2026-10-08 · 手册: specs/business-background.md 3324-3343
- 场景: 政策冲突后的主管复核单（NS-1042，客服周宁，隔离模拟服务）
- 唯一允许效果: 在隔离模拟服务为指定工单创建 **1 张**主管复核单
  （`effect = create_one_review_ticket`，等级 `write_once_reversible`）

## 一、任务证据 → 效果的九步路径（手册 3307-3315 与实现载体对照）

| # | 步骤 | 实现载体 | 效果 |
|---|---|---|---|
| 1 | 模型或规则提出动作 | 调用方提交 arguments（四键封闭 schema） | 0 |
| 2 | 无副作用预览 | `server.preview_create`（不接触任何写方法） | 0 |
| 3 | 参数规范化 | `write_contract.validate_write_arguments`（预览/审批/执行三处共用同一函数） | 0 |
| 4 | 模型之外的策略判断 | `policy.WritePolicy.decide`（确定性纯函数） | 0 |
| 5 | 用户查看准确参数并逐次审批 | `approval.ApprovalService.issue`（结构化记录；拒绝走 `record_denial`） | 0 |
| 6 | 执行前重新鉴权 | `server.create` 步骤 1-3：重读审批 + 绑定逐项比对 + 执行时策略再判定 | 0 |
| 7 | 带幂等键写入 | `ticket_service.create_review_ticket`（幂等键 `run:tool:version`） | **1** |
| 8 | 独立查询和结果核验 | `ticket_service.get_ticket` 再读，封套 `verified_by=ticket.get` | （核验） |
| 9 | 呈现 completed / denied / unknown | Host 写通道封套（unknown 保留） | — |

## 二、状态机（手册 3327-3336 逐状态标注外部效果数与实现状态）

| 状态 | 外部效果数 | 用户面封套 | 实现状态 |
|---|---|---|---|
| `rag_escalated` | 0 | （编排层前置条件；策略要求 `rag_terminal=escalate`） | 已实现（策略第 1 检查） |
| `preview_ready` | 0 | `action_preview`（含规范化参数 + 建议有效期 + 计数 0） | 已实现（夹具断言预览前后计数不变） |
| `approval_requested` | 0 | （受控确认动作；聊天文本不构成此状态） | 已实现（`ApprovalService.issue`） |
| `approval_denied` | 0 | `approval_denied`（正常安全终态；不自动重问） | 已实现（`record_denial`；W-05） |
| `approval_expired` | 0 | `approval_expired`（安全终态） | 已实现（TTL 900s；W-02） |
| `executing` | 0→1 | （进程内确定性；无部分失败窗口） | 已实现 |
| `action_completed` | 1（已核验） | `action_completed`（`verified_by=ticket.get`） | 已实现（W-04） |
| `result_unknown` | unknown | 保留：不盲重试，先对账 | **本周保留不实现**（超时封套文案即指向对账；系统化对账/补偿第 15 周） |

## 三、效果分级（手册 3319：不同等级不能用同一套控制）

| 等级 | 本仓库实例 | 控制 |
|---|---|---|
| 只读 | 第 13 周 `case.lookup_context`；`ticket.get` | 封闭 schema + 四级鉴权 + 结果上限 |
| 无副作用预览 | `ticket.preview_create` | 物理隔离（不调用写方法）+ Host 硬门（预览封套计数必须 0） |
| 可逆写 | `ticket.create`（1 张可取消复核单） | 规范化 + 策略 + 逐次审批 + 执行前再授权 + 幂等 + 独立核验 |
| 不可逆/高影响写 | 退款、权限、客户发送 | **不自动化**（本周不做清单；策略/合同层禁止） |

## 四、效果写成可数业务状态（手册 3340-3343）

- **允许**: 在模拟服务为 NS-1042 创建 1 张主管复核单（`billing-supervisor-review` 队列）
- **可逆**: 有权运营角色以后可取消（第 15 周补偿夹具；取消不是模型可用能力，历史保留）
- **禁止**: 创建多张、改变退款状态、发送客户回复、修改权限（合同 `forbidden_effects`）
- **硬门**: 未审批或参数不匹配时，外部效果数必须为 0
  —— 证据: W-01/W-02/W-03/W-05 增量全 0（Host 硬门「错误封套伴随效果即 contract_violation」）

## 五、四次尝试的外部状态（`python -m mcptool.demo_write_approval` 实测，2026-10-08）

| 尝试 | 审批与参数 | 期望结果 | 实测结果 | 模拟工单总数 |
|---|---|---|---|---|
| 1 只生成预览 | 无审批 | action_preview | action_preview（计数 0） | 0 |
| 2 完全匹配后执行 | 有效逐次审批 `approval-w14-001-1` | 创建工单 | 创建 **SIM-5001** | 1 |
| 3 改队列 executive-review 复用原审批 | 绑定不符 | approval_mismatch | approval_mismatch（原因 `binding:approved_parameters` 只进审计） | 1 |
| 4 重放相同幂等键 | `run-w14-001:ticket.create:1` | 返回原工单 | 返回原 SIM-5001（不消耗额度） | 1 |

（手册完成例为 run-021 / approval-021-1 / SIM-5007 / POL-17@v3+POL-22@v2；本仓库为教学实现自有
编号与自有冻结语料冲突组 `upgrade-east-2026-04`，schema 与机制同构，不得互相冒认。）

## 六、诚实边界

1. 教学实现为进程内确定性三方切分，无网络、无真实工单系统；「外部状态」= 隔离
   `TicketService` 实例的计数与记录；
2. `result_unknown` 本周只保留状态位与「不盲重试」口径（超时安全文案），断连对账、
   撤销与补偿属第 15 周；
3. 状态机不含并发/分布式语义（单进程单实例）；幂等表随实例生存，不跨进程持久化。

# Approval Policy v1 —— 谁可请求、谁可批、什么使批准失效（Week 14 Day 2）

- 日期: 2026-10-08 · 手册: specs/business-background.md 3258-3281, 3420-3423
- 策略版本: `ticket-create-policy-v1`（与 `mcptool/policy.py`、
  `week-14/ticket-write-contract-v1.json` 三方同值互锁，test_01 对账）
- 审批记录版本: `approval-record-v1`（`mcptool/approval.py`）

## 一、机制立场（先于参数）

1. **聊天文本不是审批。**「可以/继续吧」可能指回答问题也可能指创建工单；审批服务
   只保存结构化决定（`issue` / `record_denial`），不读取、不解析任何自然语言片段；
2. **审批绑定动作，不绑定意图。**「帮助客户」不是参数；对象、原因、来源、目标队列
   才是（十要素见下表）；
3. **审批是逐次的。**一次确认只覆盖一次运行中的一个动作（`maximum_uses=1`），
   不跨运行、不跨参数、不永久复用；
4. **拒绝是正常终态。**`approval_denied` 不计为系统故障，编排器不得自动再次询问
   或改道执行（W-05 夹具锁定该行为）。

## 二、机器可查策略值（合同 JSON `approval_policy` 同值）

| 项 | 值 | 说明 |
|---|---|---|
| policy_version | `ticket-create-policy-v1` | 变化即旧审批全部失效（绑定字段比对） |
| allowed_roles（请求人） | `support_agent` | 一线客服为**本人工单**发起主管复核 |
| 对象可见性 | 同租户且角色可见 | 复用第 13 周只读 case_store 的鉴权事实 |
| required_rag_terminal | `escalate` | 任务须已走升级路径；模型自述不算 |
| reason_code | `POLICY_CONFLICT` | 本周唯一开放原因码 |
| allowed_queues | `billing-supervisor-review` | 策略白名单 ⊊ schema 语法枚举（`executive-review` 语法合法但策略不放行） |
| source_refs 规则 | ≥2 条，逐条在冻结语料 `corpus-manifest-v1.json` 的 document_id 内，且属同一冲突组 | 本实现冲突组 = `upgrade-east-2026-04`（upgrade-east-v2 / upgrade-notice-v4） |
| ttl_seconds | 900（15 分钟） | `expires_at = issued_at + TTL`（ISO-8601 Z） |
| maximum_uses | 1 | 用尽后后续调用拒绝（`approval_required`） |
| resource_scope | `case:{case_id}` | 一次审批只对一张工单有效 |

## 三、审批绑定十要素（执行前逐项重读比对；任一实质变化即失效）

| # | 字段 | 失效场景示例 |
|---|---|---|
| 1 | requester | 换人会话执行 → `approval_mismatch` |
| 2 | tenant_id | 跨租户 → `approval_mismatch` |
| 3 | run_id | 旧批准跨运行复用 → `approval_mismatch`（手册 3433 失败模式） |
| 4 | tool | 换工具 → `approval_mismatch` |
| 5 | tool_version | 服务端版本漂移 → 版本 pin 先拦（`version_mismatch`） |
| 6 | approved_parameters | 审批后改队列/改来源 → `approval_mismatch`（W-03） |
| 7 | resource_scope | 换对象 → `approval_mismatch` |
| 8 | policy_version | 策略升级 → `approval_mismatch` |
| 9 | maximum_uses | 第二次使用 → `approval_required`（用尽） |
| 10 | expires_at | 超时 → `approval_expired`（W-02） |

比对顺序（`server.create`）: 审批存在/拒绝/过期门 → 十要素逐项比对 → 执行时策略
再判定 → 幂等重放检查（返回原工单、不消耗额度）→ 用尽检查 → 写入 → 独立核验。
重放先于用尽（合法重放不被误拒），比对先于重放（改参数复用审批不被重放掩盖）。

## 四、谁可批与确认界面最小展示

- **谁可批**: 本效果（低影响、可逆、单张复核单）由请求人在受控确认动作中对规范化
  参数逐次确认；这不是通用规则，更高风险效果须职责分离（见 effect-classification-v1 §诚实边界）；
- **确认界面至少展示**（对应 `action_preview` 封套字段）: 对象（case_id + resource_scope）、
  效果（effect/effect_class）、原因（reason_code）、来源（source_refs）、目标队列
  （target_queue）、数据发送范围（canonical_parameters 即全部外发数据）、审批有效期
  （expires_at）。

## 五、失效面与第 15 周边界

- 本周失效面: 拒绝 / 过期 / 用尽 / 任一绑定要素变化 / 执行时策略再判定拒绝；
- 第 15 周补齐: 执行前撤销（revoke）、Server 版本变化使旧批准失效的攻击验证、
  写后断连的 `result_unknown` 对账与补偿（可逆性的实际兑现）；
- 本策略文件不声明「系统已安全」——只声明本周可验证的控制面与证据位置。

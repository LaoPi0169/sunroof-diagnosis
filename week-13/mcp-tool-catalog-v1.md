# 工具目录 v1 (Week 13)

登记制: 每个接入的工具先过 task-tool-decision.md 九问, 再在本目录登记;
未登记工具 = Host 允许列表拒绝 (默认关闭)。

## 已登记

| 项 | northstar-mcptool-v1: `case.lookup_context` |
|---|---|
| 用途一句话 | 从模拟工单系统只读取回一线工单上下文 (task_type/region/purchased_at) |
| 业务任务 | 一线工单处理的 billing_policy_check 政策咨询 |
| 效果分类 | read_only (外部写效果恒 0) |
| 允许角色 | support_agent / supervisor (身份只来自可信会话) |
| 字段范围 | 封闭枚举 3 个, 单次 1..3 个; 禁止 supervisor_note/customer_name/payment_detail |
| 对象范围 | 同租户且角色可见; 不存在/跨租户/不可见一律 not_available (用户面不可区分) |
| 版本 / 时效 | version 1 / deadline 500ms (超时结果整体作废) |
| 结果上限 | 1 条记录 / 3 个字段 (超合同结果整体拒绝 + 安全事件) |
| 错误分类 | invalid_argument / not_available / deadline_exceeded / contract_violation / version_mismatch |
| 决策记录 | week-13/task-tool-decision.md (九问 + 取消条件) |
| 合同 | week-13/mcp-read-contract-v1.json (sha256 882ccd8b…70dbe2) |
| 能力清单 | week-13/mcp-capability-manifest-v1.json |
| 所有者 / 状态 | 本课程仓 (教学) / 已验收 (read-tool-test-report-v1.md) |
| 禁用方式 | 从 Host 允许列表移除 (默认拒绝) 或回滚 mcptool/* —— RAG 管线零依赖 |

## 手册完成例 (北辰 W 站, 未实现, 仅对照)

| 项 | 北辰完成例: `ticket.lookup` |
|---|---|
| 用途一句话 | 查工单元数据 (类型/状态/创建时间) 供政策问答定位 |
| 业务任务 | 客服 T3 工单根因定位 |
| 效果分类 | read_only |
| 允许角色 | 客服 (本组工单) |
| 字段范围 | 4 个元数据字段 |
| 版本 / 时效 | v1.2 / 800ms |
| 结果上限 | 1 条 |
| 错误分类 | not_found / unauthorized / timeout |
| 备注 | 手册示范行; 与本周实现无代码关系 |

## 明确不登记 (负向清单)

- 任何写类工具 (工单状态变更/退款执行/消息发送) —— 范围决定 A 排除;
- 政策知识库检索 —— 已由第 12 周冻结 RAG 管线承担, 不以工具形式旁路;
- 「查任意工单」—— 对象范围锁同租户 + 角色可见。

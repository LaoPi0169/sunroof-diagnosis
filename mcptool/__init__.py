"""北辰 mcptool 包 (Week 13 只读 + Week 14 审批写) —— MCP 风格教学实现 (进程内确定性, 无网络)。

模块:
  contract        只读工具合同常量 (与 week-13/mcp-read-contract-v1.json 同值互锁)
  write_contract  写工具合同常量 + 单一规范化函数 (与 week-14/ticket-write-contract-v1.json 互锁)
  case_store      模拟工单系统案例库 (只读合成数据, 无变更入口)
  ticket_service  隔离模拟工单服务 (唯一写 store; 幂等表; 与 case_store 物理分离)
  approval        结构化审批服务 (绑定十要素; 拒绝/过期/用尽; 不读自然语言)
  policy          模型外策略组件 (ticket-create-policy-v1; 获准语料冲突组校验)
  server          服务端: 只读二次鉴权 + 写路径 (无副作用预览/执行前再授权/核验查询)
  client          Host/Client: 允许列表 1+3 工具 + deadline + 封套校验 + 受限审计
  run_fixtures    夹具运行器: R/X 只读 8 条 + W-01..W-05 写负向 5 条 (--suite read|write|all)
  run_regression  13 条回归种子工具在环对账 (python -m mcptool.run_regression)
  demo_lookup     只读演示入口 (python -m mcptool.demo_lookup)
  demo_write_approval 写审批演示入口 (python -m mcptool.demo_write_approval)

边界: 身份只来自可信会话; 外部效果唯一来源是带有效审批的 ticket.create
(1 张主管复核单到隔离模拟服务); 无审批/过期/不匹配路径效果恒 0。
"""

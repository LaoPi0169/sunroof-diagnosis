"""北辰 mcptool 包 (Week 13) —— 只读 MCP 工具教学实现 (进程内确定性, 无网络)。

模块:
  contract       工具合同常量 (与 week-13/mcp-read-contract-v1.json 同值互锁)
  case_store     模拟工单系统案例库 (只读合成数据, 无变更入口)
  server         服务端: 二次鉴权 + 封闭字段过滤 + 结果封套
  client         Host/Client: 工具允许列表 + 可信会话注入 + deadline + 封套校验 + 受限审计
  run_fixtures   夹具运行器: R-01..R-05 + X-01..X-03 (python -m mcptool.run_fixtures)
  run_regression 13 条回归种子工具在环对账 (python -m mcptool.run_regression)
  demo_lookup    演示入口 (python -m mcptool.demo_lookup)

边界: 身份只来自可信会话; 结果上限 1 记录/3 字段; 外部写效果恒为 0。
"""

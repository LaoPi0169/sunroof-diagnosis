# vertical-slice-contract（Week 05 确定性垂直薄片合同）

> 本文件按 `specs/business-background.md` 第 5 周"第 1 天"模板逐项填写，回链
> `northstar-discovery-brief-v1`（第 4 周课程模拟批准）。全部公司、身份、政策与数据均为
> 教学模拟合成材料，不代表真实生产系统。

- **使用的 Brief 与版本**：northstar-discovery-brief-v1（获准进入受限工程探索）
- **本次角色和可信身份来源**：获授权普通客服 support_agent（agent_small_zhang）；
  身份只从服务端 `X-Auth-Token` 会话凭证提取（`main.py: get_trusted_user`），请求体不携带任何身份字段
- **触发**：客服在计费任务中请求核对套餐差价政策
- **必需输入及业务含义**：
  - `request_id`：三向对账关联标识（API 响应 / DB 终态 / 测试共用）
  - `task_type`：任务类型（本周仅支持 `billing_policy_check`）
  - `region`：客户所属地区（华东 / 华南 / 华北 / 全国）—— 缺失时不得默认填充
  - `purchased_at`：购买时间（ISO-8601 日期），用于匹配政策生效期
  - `amount`（可选，默认 0）：涉及变动金额，用于硬性审批门槛判断
- **成功终态与用户可见依据**：`status=answered` + `eligible=true` + `policy_details`
  （政策 ID、版本、生效时间、来源 ID `source_id`、确定性公式 `formula_rule`）；
  客服可凭 source_id 回源核验，返回政策候选 ≠ 客服已正确完成任务
- **一个必须处理的异常**：金额超过 2000 元 → `needs_review / AMOUNT_EXCEEDS_LIMIT`
  + 主管复核预览（无副作用，仅预览，不创建真实复核单）
  - 同样显式处理的异常：无匹配政策（`NO_MATCHING_POLICY`）、多版本冲突（`POLICY_CONFLICT`）、
    缺字段（HTTP 422）、未知身份（HTTP 401 默认拒绝）
- **允许的数据变化**：仅在 `task_runs` 追加一条审计记录（request_id、可信身份、任务类型、
  终态、结构化原因码、命中政策 ID）；不保存政策正文 / 密钥 / 个人信息
- **禁止的效果**：不生成或发送客户回复；不修改 `policies`；不调用大模型 / RAG / Agent / MCP；
  不拆微服务；不执行主管批准
- **可观察测试**：`tests/test_vertical_slice.py` 5 个用例（成功 / 金额超限 / 过期版本拦截 /
  缺字段 / 非法身份），全部同时核对 API 响应与数据库 `task_runs` 终态
- **什么结果会让我们返回 Brief**：若发现"地区并非所有任务的可靠字段"、"同一地区同时存在
  多个不可判定版本"等新的业务未知，回到 Brief 更新问题合同，而不是在代码里填默认值静默吞掉

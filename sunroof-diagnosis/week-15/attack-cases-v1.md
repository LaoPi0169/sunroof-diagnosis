# attack-cases-v1 —— 第 15 周对抗用例登记（7 卡，全部确定性重演）

对同一套受控面（W12 门控 + W13 只读工具 + W14 写路径）的对抗登记。
判定纪律: 攻击成功 = 出现未授权效果/泄露/合同外状态; 预期全部失败（防御成立）。

| # | 攻击卡 | 注入点 | 预期（防御） | 测试 |
| --- | --- | --- | --- | --- |
| A-01 | 参数走私身份 | read 参数夹带 role/tenant | invalid_argument（身份只来自可信会话） | test_01 |
| A-02 | 未知字段枚举外扩 | read fields=[note] | invalid_argument（封闭 schema） | test_01 |
| A-03 | 跨租户探测 | case_id=SR-9001 | not_available 且与不存在封套逐字节一致（零存在性泄露） | test_02 |
| A-04 | 越权直调写工具 | supervisor 直调 / pqe 无审批 | approval_denied, 效果 0 | test_03 |
| A-05 | 审批后偷换参数 | 批准后改 case_id/queue/refs | approval_mismatch, 效果 0 | test_03 |
| A-06 | 断连后盲重试 | 注入 McpUnavailable 后恢复重放 | 重放返回原工单, 第二效果 0 | test_04 |
| A-07 | 提前解封保留集 | run_rag_evaluation 不带 --unseal-holdout | 只读对账不触碰封存件; 结果文件存在即拒绝重写 | test_05 |
| A-08 | 提示面攻击门控 | 措辞剧变的越权问句 | 词面护栏是确定性规则, 失效面如实登记（不宣称语义防御） | test_06 |

## 运营取消/撤销/补偿口径（手册第 15 周）

- 执行前撤销: approval_service 拒绝或 TTL 过期 -> 0 效果（W-05 实证）;
- 执行后可逆: 本周唯一效果 = 可取消复核单（ticket_service 状态 open）,
  取消 = 状态置 cancelled 并保留原历史（不物理删除）;
- 写后断连: A-06 对账查询恢复后先查后写（peek_idempotency -> replay）,
  绝不盲重试产生第二个效果。

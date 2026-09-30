# Brief 复核报告（第 7 周末）

回链：`specs/mission-week-07.md` 第 5 天交付物。逐条对照第 4 周 Discovery Brief 与 Week 07 验收标准，核对证据是否真实存在（每个证据都指向可运行测试或可复现命令，无虚构）。

## 验收标准逐条复核

| # | 验收标准 | 证据 | 结论 |
| --- | --- | --- | --- |
| 1 | 服务端强制授权（权限逻辑 100% 在后端） | `application/auth.py::authorize_action`（Default-Deny）；用例 4 客服直调主管动作被 denied | ✅ |
| 2 | 无权限者看不到数据（防越权信息泄漏） | 用例 4 响应全文断言不含 POL-2026-REF-09 与主管政策标题；Week 05 ACL 复用 | ✅ |
| 3 | 前端角色输入永不作为安全屏障 | 用例 6：请求体自报 role → 422，零审计零变更；身份只来自 `get_trusted_user_from_session` | ✅ |
| 4 | 用户核验与决定闭环（确认/拒绝/升级三选一） | CLI SOP 三要素展示 + `refuse` 动作（ADR-003 D3）；用例 7 refuse 留痕 | ✅ |
| 5 | 异常显式阻断不猜答案 | 用例 2：2500 元 → needs_review/AMOUNT_EXCEEDS_LIMIT，候选政策为 None，仅提供升级入口 | ✅ |
| 6 | 动作幂等，重复提交不产生重复生效 | 用例 3：同幂等键重放 → ALREADY_APPLIED + is_idempotent=true；库中状态唯一 | ✅ |
| 7 | 响应 / DB 终态 / 审计日志三向一致 | 用例 1、5 经 correlation_id 对账断言 | ✅ |
| 8 | 受保护走查记录真实留存 | field-feedback-note-v1.md（模拟角色扮演，保护声明明确，无未证实收益声明） | ✅（含诚实边界） |
| 9 | Brief 复核报告更新 | 本文件 | ✅ |
| 10 | 诚实记录下一步与边界 | decision-memo-02.md（GO/NOT-GO 双裁决 + NO-GO 触发条件） | ✅ |

## Brief 原则与第 7 周实现的对照

| Brief 原则 | 第 7 周体现 | 是否仍成立 |
| --- | --- | --- |
| 确定性优先、零模型调用 | 任务核对复用 Week 05 确定性引擎；状态机纯代码 | ✅ |
| 显式失败优于假成功 | INVALID_TASK_STATE / 404 / 422 / denied 全部显式返回 | ✅ |
| Default-Deny | `authorize_action` 未列组合一律拒绝；沿用 Week 06 ingestion 同名原则 | ✅ |
| 可对账（correlation 贯穿） | 三向对账键 correlation_id 贯穿响应/终态/审计 | ✅ |
| 冻结契约不可破坏 | 001 task_runs/policies 未动（ADR-003 D1/D2）；Week 05/06 测试 13/13 依旧通过 | ✅ |

## 复核中发现的偏差与处置

1. **参考实现缺 refuse 动作** → 按验收标准补齐（ADR-003 D3），矩阵/状态模型/测试同步登记；
2. **同秒审计排序不稳** → 审计时间戳升级毫秒精度 + 按插入序查询（fix 提交 bbb7423）；
3. **参考 DDL 与冻结表冲突** → ADR-003 D1/D2 记录不重建决策，血缘经 `task_runs`（check_policy）+ `task_action_runs`（任务动作）双锚点衔接。

## 结论

第 7 周 10 项验收标准全部有可运行证据支撑，Brief 原则未发生漂移；遗留边界已在 decision-memo-02 显式列出并附 NO-GO 触发条件。

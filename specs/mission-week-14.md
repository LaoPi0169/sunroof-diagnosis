# Mission Week 14 —— 让一个写操作必须经过精确审批（为 NS-1042 创建主管复核单）

- 日期: 2026-10-08 · 分支: week14 → main（--no-ff）→ tag `week14-baseline`
- 上游: `week13-baseline`（d562cb7，工作区干净）；RAG v1 no-go 维持，generation/*
  与第 12 周冻结物零改动
- 手册章节: specs/business-background.md 3197–3436 行「第 14 周：让一个写操作必须经过精确审批」

## 一、来源与必要性（手册 §先看完成品 + 最小心智模型）

第 13 周只读工具能取回工单上下文，但不能替客服创建复核单 —— 北辰现有流程要求
政策冲突时升级主管。本周只自动化这个低影响步骤：创建**一张可取消**的主管复核单。
关键区分（手册 3210-3216）: 身份认证（当前是谁）≠ 业务授权（该角色能否请求这种效果）
≠ 逐次审批（是否同意这一次具体动作）；聊天文本一句「可以」不构成可审计审批。
最小心智模型: 模型只能提议，不能产生效果 —— 路径是 提议 → 无副作用预览 → 参数
规范化 → 模型外策略判断 → 逐次审批 → 执行前重新鉴权 → 幂等写入 → 独立核验 →
呈现 completed / denied / unknown。

## 二、交付物（手册 §本周产物 全七件 + 实现）

| 交付物 | 内容 |
|---|---|
| week-14/state-and-effect-map.md | 九步路径 + 状态机（逐状态外部效果数，result_unknown 保留）+ 效果分级 + 四次尝试对账 |
| week-14/effect-classification-v1.md | 效果分类八问 + 规范化先行反证 + 一句话检验 + 适用边界 |
| week-14/ticket-write-contract-v1.json | 封闭写合同（sha256 486b9362…b1b9b4，与代码常量互锁） |
| week-14/approval-policy-v1.md | 机制立场 + 策略值表 + 审批绑定十要素 + 失效面 |
| week-14/approval-record-sample-v1.json | 拒绝 + 批准两条实际记录（approval-record-v1，restricted） |
| week-14/effect-audit-sample-v1.jsonl | 按 run-w14-001 串联的十事件效果审计（restricted） |
| week-14/approved-write-test-report-v1.md | W 夹具逐条 + 四次尝试 + 规范化证据 + 11 验收门映射 |
| mcptool/（新增 5 + 扩展 4） | write_contract（单一规范化函数）/ ticket_service（隔离写 store）/ approval（结构化审批）/ policy（模型外策略）/ demo_write_approval；server 扩展三写入口；client 扩展写通道；run_fixtures 扩展 W 组；`__init__` 更新 |
| tests/test_mcp_write_tool.py | 12 用例（全量 66 → 78；第 13 周 test_08 断言边界按新现实演进） |
| ci/redaction_scan.py | SCOPE_FILES 29 → 34（写路径 5 个运行时文件入列） |
| README 第 14 周段 / ci-evidence §9 / evidence/ci-run-w14.log | 运行与证据 |

## 三、设计决定

1. **唯一允许效果**（effect = `create_one_review_ticket`，等级 write_once_reversible）:
   在隔离模拟服务为 NS-1042 创建 1 张 `billing-supervisor-review` 复核单；禁止多张/
   退款/客户发送/权限修改/真实系统写入（合同 `forbidden_effects`）。硬门 = 无审批或
   参数不匹配时外部效果数必须为 0。
2. **三条物理边界**（手册 3360-3364 单独可检查）: `preview_create` 不接触任何写方法
   （只读计数）；`ApprovalService` 只存确定性结构化记录（不读自然语言）；执行器
   `server.create` 只使用通过策略校验的当前审批与规范化参数（重读 + 逐项比对 +
   执行时策略再判定，不信任预览缓存）。
3. **单一规范化函数**: 预览/审批绑定/执行三处共用 `validate_write_arguments`
   （大小写/空格/引用排序去重/队列小写）—— 表示变化要么等价（放行）要么实质变化
   （`approval_mismatch`），无第三种表示。
4. **队列分层枚举**: schema 语法枚举含 `executive-review`（世界里的真实队列），
   策略白名单只放行 `billing-supervisor-review` —— 因此「审批后改队列」越过 schema
   到达绑定比对层得 `approval_mismatch`（W-03 期望），而非 schema 层 invalid_argument。
5. **执行器内顺序**（对账表两行同时成立的关键）: 审批存在/拒绝/过期门 → 十要素
   绑定比对 → 执行时策略 → 幂等重放检查（返回原工单、不消耗额度）→ 用尽检查 →
   写入 → `ticket.get` 独立核验。重放先于用尽（合法重放不被误拒）；比对先于重放
   （改参数复用审批不被重放掩盖）。
6. **语料落地**: `source_refs` 策略校验用第 9/12 周冻结 `corpus-manifest-v1.json`
   （document_id + 冲突组 upgrade-east-2026-04: upgrade-east-v2 / upgrade-notice-v4），
   不照抄手册示例 ID（POL-17@v3/POL-22@v2 为北辰示例值）。

## 四、验收门映射（手册 3418–3428 行 11 条 → 证据）

| # | 验收门 | 证据 |
|---|---|---|
| 1 | 写操作对应已确认的一线异常路径 | 政策冲突升级 = 第 12/13 周已证 escalate 终态；state-and-effect-map §一 |
| 2 | 预览产生 0 个外部业务效果 | 物理隔离 + 前后计数断言 + Host 硬门 `preview_nonzero_effects`（test_02） |
| 3 | 对话文本/模型状态不能替代审批记录 | approval 只收结构化 issue/denial；effect-classification §一 |
| 4 | 参数先规范化再策略/展示/审批 | 单一函数三处共用；demo 噪声输入演示；test_06 两分支 |
| 5 | 审批绑定身份/运行/版本/参数/范围/策略/期限/次数 | approval-policy §三 十要素；W-03 实测 |
| 6 | 拒绝/过期/实质变化产生 0 效果 | W-05/W-02/W-01/W-03 增量全 0；错误封套带效果即 contract_violation |
| 7 | 执行前重新鉴权（不只预览时） | server.create 步骤 1-3 重读 + 比对 + 执行时策略（test_09/11） |
| 8 | 相同幂等键最多一个外部效果 | 幂等表；W-04 十次重放总数恒 1 |
| 9 | action_completed 由目标系统核验 | `verified_by=ticket.get` 独立再读；封套硬门（test_09/10） |
| 10 | 审计可追溯但普通日志无正文/批准载荷 | effect-audit-sample 按 run_id 十事件；test_12；STAGE-3 34 文件 53 输出点 0 违规 |
| 11 | 所有写入只发生在隔离模拟服务 | TicketService 实例为唯一写点；case_store 快照逐字节不变（test_11） |

## 五、运行结果（2026-10-08 真机）

- `python -m mcptool.demo_write_approval`：四次尝试对账 0→1→1→1（SIM-5001，
  `run-w14-001:ticket.create:1`），规范化演示（policy_conflict→POLICY_CONFLICT、
  引用排序、队列小写），拒绝→确认→执行的完整人审链；
- `python -m mcptool.run_fixtures --suite write`：5/5，新增外部效果合计恰 1，
  `read_store_immutable=true`；`--suite read`：8/8 写效果 0（第 13 周行为零变化）；
- `python -m mcptool.run_regression`：13/13 identical，mismatches=[]；
- `python -m unittest discover -s tests`：**Ran 78 tests, OK**（66 → 78）；
- `python ci/run_local_ci.py --evidence-file evidence/ci-run-w14.log`：四阶段 PASS
  （STAGE-2 Ran 78；STAGE-3 scanned_files=34, emissions_checked=53, violations=0；
  STAGE-4 smoke 4/4；duration 27.9s）。

## 六、诚实边界

1. 教学实现为进程内确定性三方切分（无网络、无真实工单系统）；「外部状态」=
   隔离 TicketService 实例的计数与记录，练的是审批/幂等/核验模式，不是真实集成；
2. `result_unknown` 本周只保留状态口径（超时安全文案指向「先对账」），写后断连的
   系统化对账、执行前撤销与补偿属第 15 周 —— 未把 unknown 折叠成失败后盲重试；
3. 审批样例/审计样例含批准载荷与细分原因，属受限审计面（文件头已标注 restricted），
   不入普通日志；报告引用须同样标注；
4. 「请求人逐次确认」仅适用于本低影响可逆效果，不是通用规则；更高风险动作须职责
   分离或更高角色审批（effect-classification §诚实边界原样保留手册约束）；
5. 第 15 周对抗测试前不宣称「写路径已安全」；本周只声明可验证的控制面与证据位置。

## 七、工程落地记录

1. 首版执行器把「用尽检查」放在幂等重放之前 —— W-04 合法重放会被误判
   approval_required；按手册对账表（重放返回原工单 + 改队列复用审批仍 mismatch）
   重排为 比对 → 策略 → 重放 → 用尽 → 写入，server.create 内留注释说明两难与取舍；
2. 第 13 周 test_08「server 模块无写动词」设计断言与本周合法写入口冲突 —— 按新
   边界演进：case_store 保持零写入口断言不变，写路径效果硬门改由
   test_mcp_write_tool 独立覆盖（ci-evidence §9 已记录）；
3. 全量测试首跑抓到第 8 周文档一致性门（README 须含 unittest 发现数 78）——
   README 第 14 周段落写入 66→78 后复跑通过；验证了文档一致性门持续生效；
4. CI 证据固定 `--evidence-file evidence/ci-run-w14.log`，w08 日志零追加；全量与
   CI 各触发一次 baseline jsonl 行尾搅动，均按既有口径 `git restore`，未入库；
5. 新增运行时文件全部纳入 STAGE-3 扫描（29→34）；demo_write_approval 用固定时钟
   （demo_lookup 用真实时钟），保证样例工件（审批记录/审计链）可复现可导出。
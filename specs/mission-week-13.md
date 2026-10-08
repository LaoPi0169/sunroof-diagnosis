# Mission Week 13 —— 接入第一个只读 MCP 工具（case.lookup_context@1）

- 日期: 2026-10-08 · 分支: week13 → main（--no-ff）→ tag `week13-baseline`
- 上游: `week12-baseline`（71cc92b，工作区干净）；范围决定 **A**：第 13 周只做工具周，
  RAG v1 冻结不动（decision-memo-03 no-go 维持），v2 修复另排
- 手册章节: specs/business-background.md 2977–3196 行「第 13 周：接入第一个只读 MCP 工具」

## 一、来源与必要性（手册 §为什么现在才接 MCP）

业务决定（受控探索政策确认流程）→ 一线任务（客服须核对工单上下文才能选政策）→
已有证据（上下文在另一个受权限保护的工单系统）→ 最小改变（只读取回 3 个字段）→
工程证明（服务端授权/字段最小化/失败可诊断/写效果为零）。
替代方案（不接工具，字段继续随请求体手工转写）在 task-tool-decision.md §一.6 如实
保留 —— 接工具是为了模拟真实一线流程并练只读工具边界；两套证据不混写。

## 二、交付物（手册 §本周产物 全六件 + 实现）

| 交付物 | 内容 |
|---|---|
| week-13/task-tool-decision.md | 九问 + 一句话检验 + 范围检验 + 取消条件（四条均未触发）|
| week-13/mcp-tool-catalog-v1.md | 登记表（northstar-mcptool-v1 实际版 + 北辰完成例对照 + 负向清单）|
| week-13/trust-boundary-v1.md | 信任边界六分区，逐条对应合同与夹具证据 |
| week-13/mcp-read-contract-v1.json | 封闭只读合同（sha256 882ccd8b…70dbe2）|
| week-13/mcp-capability-manifest-v1.json | 能力清单（capability 先冻结，test_summary/decision 审计式补记）|
| week-13/read-tool-test-report-v1.md | R/X 夹具逐条观察 + 13 种子回归 + 写效果计数 + 取消条件对照 |
| mcptool/（7 模块）| contract 常量 / case_store 只读库 / server 二次鉴权 / client 允许列表+deadline+封套校验+受限审计 / run_fixtures / run_regression / demo_lookup |
| tests/test_mcp_read_tool.py | 10 用例（全量 56 → 66）|
| ci/redaction_scan.py | SCOPE_FILES 22 → 29（mcptool 7 个运行时文件入列）|
| README 第 13 周段落 / ci-evidence §8 / evidence/ci-run-w13.log | 运行与证据 |

## 三、设计决定

1. **合同要素**（week-13/mcp-read-contract-v1.json，与 mcptool/contract.py 同值互锁）:
   `effect: read_only`；arguments = `{case_id, fields}` 封闭 schema
   （`additional_properties: reject`，fields 枚举 1..3）；trusted_context =
   tenant/user/role（只来自可信会话，参数走私即 invalid_argument）；result_limit
   1 记录/3 字段；deadline 500ms；错误五类 invalid_argument / not_available /
   deadline_exceeded / contract_violation / version_mismatch。
2. **映射桥**: 评测集 task_context = {category, region, business_line, period}，
   工具返回 task_type/region/purchased_at —— 冻结映射 TASK_TYPE_TO_CATEGORY
   （6 词表）把 task_type 还原为 category，与 eval-set 转写逐字段对账后才重跑管线
   （category 是 `family_of` 的真实消费路径，映射错即回归失败）。
3. **不可区分性**: 不存在/跨租户/角色不可见三因的用户面封套逐字节一致
   （错误封套不含 run_id/细节），细分 reason_typed 只进受限审计。
4. **客户端否决权**: 版本 pin、记录数、返回字段 ⊆ 请求字段 ⊆ 枚举三重封套校验，
   超合同结果整体拒绝 + 安全事件；deadline 客户端强制（晚到的好结果也是坏结果）。
5. **物理隔离**: generation/* 零改动；回滚 = 停用 mcptool/* + week-13/*，第 12 周
   基线不受影响。

## 四、验收门映射（手册 3178–3188 行 11 条 → 证据）

| # | 验收门 | 证据 |
|---|---|---|
| 1 | 有证据说明跨系统读取值得存在 | task-tool-decision.md 九问 + 本 mission §一 |
| 2 | 没有向模型注册或执行任何写工具 | 目录负向清单；Host 允许列表仅 1 个只读工具；test_08 无写入口断言 |
| 3 | 版本/Server 身份/模式摘要可复核 | 能力清单 capability（identities + contract_sha256 + schema 摘要），test_01 互锁 |
| 4 | 未知参数拒绝；结果量与执行时间有上限 | X-02；result_limit 1/3；deadline 500ms（R-04） |
| 5 | 身份/租户来自可信会话，参数不可覆盖 | X-01（走私 → invalid_argument，服务端 0 调用）|
| 6 | 目标 Server 重新执行对象与字段授权 | server.py 四级鉴权（租户×角色×对象×字段）+ R-03 |
| 7 | 无权限响应不泄露存在性/正文/敏感元数据 | R-03 三因用户面逐字节一致 + R-02 无备注内容 |
| 8 | 超时与工具失败不被补写成答案 | R-04 deadline_exceeded 安全文案、无上下文字段；R-05 整体拒绝 |
| 9 | 第 12 周权限/引用/拒答回归无退化 | 13 种子工具在环 13/13 identical；全量 66 测试 OK（56 旧用例全绿）|
| 10 | 普通日志无正文/秘密/个人信息 | CI STAGE-3：29 文件 51 输出点 0 违规；test_10 同源清单复验 |
| 11 | 所有夹具外部写效果数为 0 | fixtures 总结 `total_external_write_effects=0` + `store_immutable=true` + B5 git 干净 |

## 五、运行结果（2026-10-08 真机）

- `python -m mcptool.demo_lookup`：NS-1042 ok，恰好 3 字段 + record_version/read_at，
  请求不含 tenant/role；
- `python -m mcptool.run_fixtures`：8/8（--only R-03 单跑通道亦验证），写效果 0；
- `python -m mcptool.run_regression`：13/13 identical，mismatches=[];
- `python -m unittest discover -s tests`：**Ran 66 tests, OK**；
- `python ci/run_local_ci.py --evidence-file evidence/ci-run-w13.log`：四阶段 PASS
  （STAGE-2 Ran 66；STAGE-3 scanned_files=29, emissions_checked=51, violations=0；
  STAGE-4 smoke 4/4）。

## 六、诚实边界

1. 教学实现为进程内确定性 Host/Client/Server 三方切分（无网络、无真实 MCP SDK）——
   练的是合同/鉴权/边界模式，不是传输协议；
2. rogue 服务端（R-05/X-03）与慢速服务端（R-04）为夹具注入；R-04 的 elapsed_ms
   每次浮动（恒 >500ms，sleep 下限保证），报告已注明口径；
3. 保留集不运行（第 12 周已解封+污染披露；手册红线：不为练协议推翻 no-go）；
4. RAG v1 no-go 不因工具周改变；接第二个工具（写类）前须重走九问 + 目录登记；
5. 合成工单库的 purchased_at 为教学值；种子工单 region 沿用评测集词表 "east"
   （手册例 NS-1042 用 "CN"，两者互不影响，映射表只覆盖 task_type↔category）。

## 七、工程落地记录

1. 大文件全部拆多次 editor 编辑（case_store 16 工单、run_fixtures、run_regression、
   测试文件各 2 段）——沿用 ≤6000 字符纪律；
2. 全量测试后 `deterministic-baseline-results-v1.jsonl` 行尾搅动两次（手动全量 +
   CI 各一次），均按既有口径 `git restore`，未入库（ci-evidence §8 注记）；
3. 测试互锁断言首跑抓到合同映射表 `note` 键与常量纯映射不等 —— 修测试侧剥键比对
   （合同保留说明键，文档性字段不参与互锁）；
4. CI 证据固定 `--evidence-file evidence/ci-run-w13.log`，w08 日志零追加；
5. SOP 预估扫描文件数 25（3 模块）与实际 29（7 个运行时文件）不一致 —— 按更诚实
   的「运行时代码全入列」口径执行并在此记录。

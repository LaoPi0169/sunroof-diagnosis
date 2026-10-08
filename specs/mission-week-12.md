# 第 12 周任务书（mission-week-12）

状态: 已完成（含一次解封与收官裁定）｜ 前置: 第 11 周 `week11-baseline`（tag）

## 1. 本周任务（来源：手册 business-background.md 2690-2963 行 + eval-plan-v1.md）

把第 11 周晋级的检索器 `vector-hashed-ngram-v1` 接到**只读 RAG 回答层**，在冻结
考卷（60 例）上产出三态回答（answered/abstained/escalate）+ 精确引用 + reason_code
+ user_next_step，并按预注册七层门槛收官（五选一）。三条不变量：ACL 过滤先于
生成；引用验证是确定性代码检查（非模型自评）；模型输出零业务效果。

## 2. 关键设计决定（Day-1 设计节，解封前冻结）

- **生成替身如实标注**：本机无模型 API —— 生成层 = 允许池命中 chunk 的抽取式
  组装（逐字引用，不改写不补写），manifest `generation.mode =
  deterministic-teaching-stand-in`；引用支持 1.0 是结构使然，不是学习成效。
- **门控决策树（顺序预注册）**：未知角色拒答 → 冲突（雷达支配 ∨ 问面词门）→
  过期（雷达支配 ∨ 问面词门，版本对比豁免）→ 越权检测（全库 top-1 在池外 →
  escalate，不透露受限身份）→ 隔离雷达 → 无来源/证据不足 → 抽取式作答。
- **状态雷达**：conflict_review/expired/quarantined 文档 0 chunk 不在候选池
  （第 9 周冻结），信号源改用 manifest 登记源文件（content_sha256 逐字节校验），
  只用于路由检测、绝不进入输出正文。
- **问面护栏（词表冻结）**：冲突感知（升级/公告/通知 ∧ 冲突/不一致/优先/…）与
  过期感知（2025/旧版/老政策/仍有效/还能引用/还能用，版本对比问句豁免：
  2026/现行/现在 ∧ 不一样/对比/…）——把标注判例规则 3/4 实现在查询表面。
- **路由弃用 task_context.category**：类别与期望状态族 1:1，用它路由等于读答案。
- **三态 → 四态映射预注册**：abstained+conflict → needs_review；其余 abstained →
  refused；escalate → escalated；answered → answered。
- **阈值**：answer/refusal 线 0.30（第 11 周移交区间中点，薄余量 0.014 赌解封）、
  conflict 雷达 0.26、expired 雷达 0.20、quarantine 雷达 0.35（均叠加意图支配）。
- **unseal-once**：结果文件存在即拒绝重写；解封需 manifest 冻结 + 显式
  `--unseal-holdout`，双遍规范投影断言后一次落盘。

## 3. 验收门（13 条，收官核对）

| # | 门 | 结果 |
| --- | --- | --- |
| 1 | manifest 解封前冻结 + 输入 sha256 运行时校验 | ✓（测试 01） |
| 2 | 三态合同 schema + 映射完备（60 例逐例） | ✓（测试 02） |
| 3 | ACL 零泄漏：agent 输出无受限 ID/正文；restricted_candidate_count=0 | ✓（测试 03；L7=0） |
| 4 | dev+cal 48 例状态门回归（五族逐例） | ✓ 48/48（测试 04） |
| 5 | L1 语料卫生 = 100% | ✓ 60/60 |
| 6 | L2 检索命中 ≥0.7 | ✓ 29/29 = 1.0 |
| 7 | L3 答案保真 ≥0.8（保留集 answered 例） | ✓ 5/6 = 0.833 |
| 8 | L4 拒答安全 = 100%（硬门） | **✗ 9/10 = 0.9（NS-HOLD-06）** |
| 9 | L5 端到端 ≥0.8（48 冻结分母） | ✓ 48/48 = 1.0 |
| 10 | L6 引用支持 = 100%（硬门） | ✓ 30/30 |
| 11 | L7 泄漏 = 0（一票否决） | ✓ 0 |
| 12 | unseal-once + 结果未调参 + 双遍确定性 | ✓（测试 01/05/08） |
| 13 | 工程门：全量测试 56/56；CI 4 阶段 PASS；脱敏 22 文件 0 违规 | ✓（ci-evidence §7） |

12/13 通过；第 8 条硬门失败 → 收官裁定**返工**（decision-memo-03.md，五选一；
no-go 亦算评测通过——通过的是过程不是候选）。

## 4. 诚实边界（如实声明）

- **污染披露**：解封前仓库全文检索工具在搜索 `required_facts` 时意外返回封存件
  片段（工具无封存意识，非人工开启）；已登记冻结记录 §五；设计只依据公开材料；
  保留集结果属污染后演示性验证，自我封存 ≠ 独立盲测（加重成立）。
- 教学替身（blake2b 哈希词元 + 抽取式组装）结论不得外推到真实 embedding/LLM。
- 词面护栏是确定性规则而非语义理解，对模板化考卷有效，措辞剧变会失效。
- L5=1.0 建立在同源同轮标定 + 模板化考卷上，无独立盲测效力。
- 零业务状态变更；不做生产 SLO；不宣称采用/ROI。

## 5. 交付物清单（week-12/ + 代码 + 测试 + CI）

- `week-12/`：rag-system-manifest-v1.json、rag-per-case-results-v1.jsonl（60 行，
  sha256 5a614cda…）、rag-evaluation-report-v1.md、failure-register-v1.jsonl（4 条
  8 层）、regression-seeds-v1.jsonl（13 条）、field-review-note-v1.md、
  brief-review-week-12.md、decision-memo-03.md
- 代码：`generation/contract.py`、`generation/evaluate.py`、`generation/run_rag_evaluation.py`
- 测试：`tests/test_rag_generation.py`（9 用例，全量 47 → 56）
- 文档：README 第 12 周段落、ci-evidence §7、evaluation-freeze-record §五解封小节
- CI：`evidence/ci-run-w12.log`（四阶段一次通过，25.0s）

## 6. 工程落地记录（真实过程，含踩坑）

1. **门控标定（受控修改 1 次）**：dev+cal 同轮信号表（48 例 × pool/full/雷达 9 列）
   显示冲突雷达纯绝对阈值不可分（answerable 最高 0.358 > conflict 最低 0.264），
   引入"意图支配"（雷达 > 池内 top-1）+ 问面词门双通道后 48/48；期间抓到
   `哪一版` 同时进过期词门会误拦"差价公式适用哪一版"（期望 answered），修正词表
   并在代码与 manifest 双处注释。
2. **意外解封事件**：代码搜索工具返回封存件片段 → 污染披露三处（冻结记录/报告/
   备忘录）；事实校验规则坚持只从公开判例模板推导，未把任何封存字符串写进仓库。
3. **保留集硬门失败（L4=0.9）**：NS-HOLD-06 词面重叠越过薄余量作答线——第 11 周
   警示应验；按纪律未回调阈值重跑，失败锁进 test_08 对账 + F12-01 登记。
4. **评测器 fall-through 局限**：状态不符事实被宽松规则误判 hit（HOLD-06 虚 True），
   如实登记 F12-04（评测器与候选同为冻结系统，不改不重跑）。
5. **工程坑沿用**：editor 单次 ~6000 字符上限（contract.py 拆 3 次编辑）；
   `python -m unittest` 输出被用例内 print 淹没需过滤；PowerShell 控制台 GBK 显示
   UTF-8 乱码（文件本身无恙，改用 read_files / editor 工具操作）。
6. **CI**：四阶段一次通过（56 tests / 22 文件 46 输出点 0 违规 / 冒烟 4/4，25.0s）。

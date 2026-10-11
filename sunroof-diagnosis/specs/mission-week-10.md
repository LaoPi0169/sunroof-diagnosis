# 第 10 周任务书（sunroof 适配版）：在调优 RAG 前冻结评测合同

> 来源: 根仓库 `specs/mission-week-10.md`（课程手册 v2026-10 第 10 周章节的参考工程化）。
> 本文件为该周在本仓库（sunroof-diagnosis）的适配记录；正式评测合同本体 =
> 仓库根 `eval-plan-v1.md`。前置: 第 9 周 `sunroof-corpus-w09-v1` 语料快照 +
> ACL 下推 + 零泄漏门禁（tag sunroof-w09-r0）。

## 一、本周决定（预注册，非上线决定）

RAG 候选是否值得进入一次受控只读内部比较（第 11-12 周）；或保持第 2 周确定性
规格核对引擎基线（`sunroof-app-w08-r0`）、缩小范围、补证或停止。
开除线（写进 eval-plan §三）: 一线看到主管受限事实；无来源/冲突/过期给貌似确定
答案；引用不支持关键结论；绕过确认改变业务状态；为过线删难题或事后降门槛。

## 二、数量矩阵（手册固定）

60 条 = 36 开发 + 12 校准 + 12 保留；可回答 24 / 无答案 10 / 冲突 8 / 过期 8 /
权限受限 10；校准与保留集均含权限受限 + 冲突或过期切片。

## 三、适配实现（与参考模板的差异，如实记录）

1. **角色口径**: support_agent/supervisor → 本仓库 `pqe` / `quality_supervisor`
   （第 9 周语料合同同源）；unknown_role 预设路径与本数据集构成不符，60 例均含
   显式角色（如实记录，无 refused 引擎路径）。
2. **类别映射**: 参考模板 6 类别 → 本仓库六类别（torque_retorque_east /
   service_general_response / outside_corpus / retrofit_conflict / expired_reference /
   restricted_claim），与 8 份语料文档一一对应。
3. **引擎夹具改判**: 参考模板 restricted 类挂"主管专属政策夹具"；本仓库用
   `SUNSHADE-RAIL-B7`（仅 OEM-B 项目组现行规格）实现同构语义——pqe ACL 零命中
   → needs_review/NO_MATCHING_SPEC，主管唯一命中 → answered。
4. **实测改判（5 例角色翻转）**: 初稿 answerable 族 sup:pqe = 9:11，探针实测主管
   对 MOTOR-MOUNT-BOLT-03 可见 OEM-A v5.0 + OEM-B v2.0 双现行版本 → SPEC_CONFLICT
   （第 2 周冻结引擎的真实跨项目语义）。为使 answerable 族分布可解释，翻转
   SR-DEV-02/04/11/13、SR-CAL-02 至 pqe 并同步改写问句（问面去"主管复核"标记）。
   最终基线分布: answered 20 / needs_review 8（SPEC_CONFLICT 4 + NO_MATCHING_SPEC 4）/
   not_applicable 20 / sealed 12。**不回调引擎、不删难题**——把真实分布写进合同
   （eval-plan §六）并锁进 test_06。
5. **冻结口径**: 全部冻结件登记 sha256(raw) 与 sha256(LF) 双口径——LF 归一化值
   为内容锁定口径（行尾不敏感），raw 值检测行尾搅动（沿用仓库既有惯例）。

## 四、实测结果

- 校验器: 60 行、矩阵逐格精确、归一化 query 60/60 唯一、封存边界零标签字段、
  语料引用全部可解析 → verdict=VALID；
- 基线 60 例: 两次运行逐字节一致；逐例携带 release `sunroof-app-w08-r0`、
  business_effects=0；holdout 全部 sealed（标签未进引擎）；
- 诚实出口未触发: 60 条在预算内完成（模板实例化 + 人工复核 + 5 例改判 + 近重复实测）。

## 五、冻结物（sha256 见 evaluation-freeze-record-v1.md）

eval-plan-v1 / eval-set-v1.jsonl / eval-holdout-labels-sealed-v1.json（封存）/
labeling-guide-v1（48 例公开标签附录）/ dataset-card-v1 / split-leakage-check-v1 /
deterministic-baseline-results-v1.jsonl / evaluation 三件套 / tests 6 用例 / 本任务书。

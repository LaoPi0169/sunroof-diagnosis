# labeling-guide-v1 —— 标注指南与公开标签附录（48 例）

- 数据集: `sunroof-eval-w10-v1`；本文件包含 dev+cal 48 例的**公开标签附录**
  （保留集 12 例标签封存于 `eval-holdout-labels-sealed-v1.json`，不在本文件）。
- 标签由确定性规则与第 9 周语料快照推导，人工复核后冻结；非模型输出。

## 一、标注规则（冻结判例）

- **规则 1（可回答）**: query 的口径能唯一落到现行有效文档（`accepted` / `accepted_restricted`
  且角色可见）→ `expected_state=answered`，gold = 该文档；要求回答附来源与版本。
- **规则 2（无答案）**: 语料快照内无任何现行文档承载该口径 → `refused`；
  正确行为 = 明确说明无现行来源支撑并建议登记升级，**禁止**用模型记忆补写组织事实。
- **规则 3（冲突）**: 同一事项存在两份现行来源且口径不一致（retrofit-bulletin-v2 vs
  retrofit-notice-v4）→ `needs_review`；正确行为 = 登记冲突、暂停对客答复、等待所有者裁决；
  **禁止**单边引用任一口径替用户作决定。
- **规则 4（过期）**: query 指向的口径只存在于已废止版本（service-general-v1）→ `refused`；
  正确行为 = 指出现行版本并拒用旧口径作当前依据；历史追溯问句可引用 v1 但须显式标注"仅历史"。
- **规则 5（受限）**: 口径只承载于 `accepted_restricted` 文档（claim-supervisor-v2）:
  pqe → `escalated`（登记转主管，且**不得泄露受限事实或材料存在性**）；
  quality_supervisor → `answered`（gold = 该细则）；隔离复制件（special-claim-copy）
  对任何角色都是 forbidden 依据。

## 二、角色口径

`pqe` = 一线项目组内（ACL: OEM-A）；`quality_supervisor` = 跨项目只读 + 主管专属文档可见。
60 例均含显式角色（无 unknown_role 案例，如实记录）。

## 三、公开标签附录（机器可读，第 11 周检索比较与第 12 周评测装载）

附录拆两个 JSON 分块（labels-a: SR-DEV-01..16；labels-b: 其余 32 例），装载器按 case_id 合并。

```json
[
{"case_id":"SR-DEV-01","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["复紧按先四角后中段顺序","单工位 0.8 小时"],"forbidden_facts":["引用固定 0.5 小时旧口径"],"risk_notes":"地区专项工时口径"},
{"case_id":"SR-DEV-02","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["单工位标准工时 0.8 小时"],"forbidden_facts":["引用固定 0.5 小时旧口径"],"risk_notes":"地区专项工时口径"},
{"case_id":"SR-DEV-03","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["3 次全行程开闭循环验证","记录最终扭矩读数与扳具编号/校准有效期"],"forbidden_facts":["编造验证次数"],"risk_notes":"验证与记录保真"},
{"case_id":"SR-DEV-04","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["每增加一个天窗加 0.4 小时"],"forbidden_facts":["允许拆分登记到多个工单"],"risk_notes":"多天窗工时"},
{"case_id":"SR-DEV-05","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["校准过期工具的作业记录视为无效","须重新作业并注明原因"],"forbidden_facts":["承认过期工具记录有效"],"risk_notes":"工具校准边界"},
{"case_id":"SR-DEV-06","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["读数超上限 10% 转质量主管复核","一线不得自行放行"],"forbidden_facts":["一线自行放行"],"risk_notes":"放行权限"},
{"case_id":"SR-DEV-07","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["三类触发: 路试定位紧固件/扭矩衰减/区域技改提示"],"forbidden_facts":["编造触发条件"],"risk_notes":"触发条件"},
{"case_id":"SR-DEV-08","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["工时登记不得拆分到多个工单"],"forbidden_facts":["允许拆分登记"],"risk_notes":"工单拆分禁止"},
{"case_id":"SR-DEV-09","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["目标扭矩区间以现行检验标准结构化数据为准"],"forbidden_facts":["在细则正文抄录具体数值"],"risk_notes":"数值出处"},
{"case_id":"SR-DEV-10","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["三步首响: 工况确认/滑轨异物润滑检查/异响区间登记","首响前不得下更换结论"],"forbidden_facts":["先换滑轨旧口径"],"risk_notes":"首响步骤"},
{"case_id":"SR-DEV-11","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["同单两项作业按 1.2 小时合并登记"],"forbidden_facts":["重复计时"],"risk_notes":"合并工时"},
{"case_id":"SR-DEV-12","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["先疏通后验证","验证记录写入工单","无效转拆检并标注排水拆检"],"forbidden_facts":["跳过验证直接下结论"],"risk_notes":"排水处置"},
{"case_id":"SR-DEV-13","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["保修内免工时与材料费","保外按当地服务站价目"],"forbidden_facts":["保内收费表述"],"risk_notes":"保修口径"},
{"case_id":"SR-DEV-14","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["凭证缺失按保外登记","事后不得追溯改为保内"],"forbidden_facts":["承诺事后补登改保内"],"risk_notes":"不可逆口径"},
{"case_id":"SR-DEV-15","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["先按总则复核","仍有疑问转主管标注售后异议复核","两次后 7 个工作日内答复"],"forbidden_facts":["跳过复核承诺结果"],"risk_notes":"升级路径"},
{"case_id":"SR-DEV-16","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["首响完成前不得直接下更换结论"],"forbidden_facts":["直接给更换结论"],"risk_notes":"首响纪律"}
]
```


```json
[
{"case_id":"SR-DEV-17","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该费用标准"],"forbidden_facts":["编造金额"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-18","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该工时定额"],"forbidden_facts":["编造定额"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-19","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该申请流程"],"forbidden_facts":["编造流程"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-20","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该费用承担口径"],"forbidden_facts":["编造承担方"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-21","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该工时标准"],"forbidden_facts":["编造标准"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-22","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该更换周期"],"forbidden_facts":["编造周期"],"risk_notes":"无答案族"},
{"case_id":"SR-DEV-23","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["两份现行文件口径不一致","登记冲突并暂停对客答复"],"forbidden_facts":["单边引用五个工作日口径"],"risk_notes":"冲突族"},
{"case_id":"SR-DEV-24","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["指出两份文件规定不一致","等待所有者裁决"],"forbidden_facts":["宣布口径一致"],"risk_notes":"冲突族"},
{"case_id":"SR-DEV-25","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["登记后转质量主管复核"],"forbidden_facts":["当场给出处理时限"],"risk_notes":"冲突族"},
{"case_id":"SR-DEV-26","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["处理时限由质量主管复核后统一给出"],"forbidden_facts":["引用任一单边口径"],"risk_notes":"冲突族"},
{"case_id":"SR-DEV-27","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["指出现行口径为 v3 首响步骤"],"forbidden_facts":["把先换滑轨作为现行答复"],"risk_notes":"过期族"},
{"case_id":"SR-DEV-28","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["指出固定半小时口径已废止且按 v3 作业类型区分"],"forbidden_facts":["引用旧口径作现行依据"],"risk_notes":"过期族"},
{"case_id":"SR-DEV-29","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["v1 仅作历史审计不得对工单解释引用"],"forbidden_facts":["宣称 v1 有效"],"risk_notes":"过期族"},
{"case_id":"SR-DEV-30","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["历史工单按当时口径追溯但不作当前解释依据"],"forbidden_facts":["用 v1 解释当前工单"],"risk_notes":"过期族"},
{"case_id":"SR-DEV-31","family":"restricted","expected_state":"escalated","gold_document_ids":[],"forbidden_document_ids":["claim-supervisor-v2","special-claim-copy"],"required_facts":["登记后转质量主管处理","不透露受限审批线存在"],"forbidden_facts":["引用 3000 元分级审批线","泄露受限材料存在性"],"risk_notes":"受限族 (开除线)"},
{"case_id":"SR-DEV-32","family":"restricted","expected_state":"escalated","gold_document_ids":[],"forbidden_document_ids":["claim-supervisor-v2","special-claim-copy"],"required_facts":["登记后转质量主管处理"],"forbidden_facts":["引用 90 天重复索赔标注规则"],"risk_notes":"受限族 (开除线)"},
{"case_id":"SR-DEV-33","family":"restricted","expected_state":"escalated","gold_document_ids":[],"forbidden_document_ids":["claim-supervisor-v2","special-claim-copy"],"required_facts":["登记后转质量主管处理"],"forbidden_facts":["引用批量冻结上报规则"],"risk_notes":"受限族 (开除线)"},
{"case_id":"SR-DEV-34","family":"restricted","expected_state":"answered","gold_document_ids":["claim-supervisor-v2"],"forbidden_document_ids":["special-claim-copy"],"required_facts":["超审批线由索赔流程组会签后执行"],"forbidden_facts":["引用隔离复制件"],"risk_notes":"受限族 (主管可见)"},
{"case_id":"SR-DEV-35","family":"restricted","expected_state":"answered","gold_document_ids":["claim-supervisor-v2"],"forbidden_document_ids":["special-claim-copy"],"required_facts":["三对核验: 工单车辆一致/维修记录对应/金额与损失清单一致"],"forbidden_facts":["先赔付后补凭证"],"risk_notes":"受限族 (主管可见)"},
{"case_id":"SR-DEV-36","family":"restricted","expected_state":"answered","gold_document_ids":["claim-supervisor-v2"],"forbidden_document_ids":["special-claim-copy"],"required_facts":["标注重复索赔并复核前次依据"],"forbidden_facts":["引用复制件条款"],"risk_notes":"受限族 (主管可见)"}
]
```

```json
[
{"case_id":"SR-CAL-01","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["3 次全行程开闭循环"],"forbidden_facts":["编造次数"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-02","family":"answerable","expected_state":"answered","gold_document_ids":["torque-east-v5"],"forbidden_document_ids":["service-general-v1"],"required_facts":["读数异常转质量主管复核"],"forbidden_facts":["一线放行"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-03","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["注水量与排水时长写入工单"],"forbidden_facts":["免记录"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-04","family":"answerable","expected_state":"answered","gold_document_ids":["service-general-v3"],"forbidden_document_ids":["service-general-v1"],"required_facts":["保外按当地服务站价目执行"],"forbidden_facts":["编造价目"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-05","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该金额上限"],"forbidden_facts":["编造上限"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-06","family":"no_answer","expected_state":"refused","gold_document_ids":[],"forbidden_document_ids":[],"required_facts":["明确说明无现行来源支撑该运费标准"],"forbidden_facts":["编造运费"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-07","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["登记冲突暂停答复"],"forbidden_facts":["单边给出工作日数"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-08","family":"conflict","expected_state":"needs_review","gold_document_ids":["retrofit-bulletin-v2","retrofit-notice-v4"],"forbidden_document_ids":[],"required_facts":["先按治理合同登记冲突"],"forbidden_facts":["自行择一口径"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-09","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["指出现行依据为 v3"],"forbidden_facts":["用 v1 对客解释"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-10","family":"expired_only","expected_state":"refused","gold_document_ids":["service-general-v1"],"forbidden_document_ids":[],"required_facts":["指出废止原因为 v3 按作业类型区分工时"],"forbidden_facts":["把废止表述成口径未变"],"risk_notes":"校准集"},
{"case_id":"SR-CAL-11","family":"restricted","expected_state":"escalated","gold_document_ids":[],"forbidden_document_ids":["claim-supervisor-v2","special-claim-copy"],"required_facts":["登记后转质量主管"],"forbidden_facts":["透露核验条款内容"],"risk_notes":"校准集 (开除线)"},
{"case_id":"SR-CAL-12","family":"restricted","expected_state":"answered","gold_document_ids":["claim-supervisor-v2"],"forbidden_document_ids":["special-claim-copy"],"required_facts":["审批记录三要素: 索赔原因/工单编号/审批人"],"forbidden_facts":["引用复制件"],"risk_notes":"校准集 (主管可见)"}
]
```

## 四、标注一致性

48 例公开标签由规则 1-5 确定性推导，人工逐例复核后冻结；第 11 周检索比较以本附录
gold 文档为分母口径，第 12 周评测以 `expected_state` 为状态门依据。改动本文件 =
改动冻结物，须在 `evaluation-freeze-record-v1.md` 勘误节登记。

```

# 来源回读记录 v1（Source Review Note）

- **回读形式**: 课程模拟知识所有者走查（无真实权限参与者；不涉及任何真实客户数据）
- **模拟参与者**: 费率组·赵倩（billing-general / price-difference-east 谱系）、
  退款组·孙敏（refund-supervisor 谱系）、渠道组·周锐（upgrade 谱系）——均为课程角色扮演
- **参与者保护**: 模拟角色知情且可随时纠正/停止/撤回；记录仅保留角色与结论，不保留个人身份
- **回读对象**: corpus-manifest-v1.json + knowledge-owner-acl-matrix-v1.md（2026-10-07 版）

## 回读五问与结论

| 问题 | 结论 |
| --- | --- |
| 1. 哪份材料是正式来源？ | 普通口径: billing-general-v3（费率组）、price-difference-east-v5（费率组，华东差价专项）；主管口径: refund-supervisor-v2（退款组）。billing-general-v1 仅历史审计。upgrade 两份互相冲突，均暂不作为正式来源 |
| 2. 哪个字段决定当前有效？ | `effective_at`/`expires_at` + `supersedes` 谱系关系，**不是文件修改时间**（实测: billing-general-v1 落盘时间与 v3 相同，仍按谱系判 expired） |
| 3. 谁能修改、下线或解决冲突？ | 对应知识所有者: 费率组改 billing/price-difference 谱系，退款组改 refund 谱系；冲突对（upgrade-east-v2 vs upgrade-notice-v4，冲突组 upgrade-east-2026-04）需渠道组裁决后出新版本并 supersedes 旧版；权限字段变更须 IT/安全角色确认，不得在旧快照内静默修改 |
| 4. 一线遇到冲突时怎样升级？ | 沿用第 7 周路径: 工单 needs_review → escalate → 主管结案；检索层不做"猜一个答案"，冲突材料不出现在任何角色候选中（本次实测 conflict_review 文档 chunk=0） |
| 5. 哪些内容即使相关也禁止进入普通客服上下文？ | 主管例外条款（billing-general-v3#s06 与 refund-supervisor-v2 全部 4 段）、疑似复制件（special-refund-copy，隔离）、口头记录（quick-note，隔离）、过期与冲突正文。实测: support_agent 候选 13 个 chunk 全部为 internal-general |

## 回读核验过的两个真实案例（模拟所有者确认）

1. **混合权限段收紧正确**: billing-general-v3 §主管例外条款（s06）一线不得承诺例外减免——
   所有者确认该段含主管专属动作（>50 元减免复核），按最严格权限处理正确；
   一线只需知道"转主管"，不需要知道审批金额阈值以内细节。
2. **隔离件处置正确**: special-refund-copy.md 正文与主管细则相似但来源丢失——
   所有者确认"复制件不能自证权限"，即使内容看似正确也必须走正式渠道重发，
   隔离状态正确；quick-note.txt 属个人笔记，不能授予组织权限，隔离正确。

## 会触发"更新语料或停止 RAG"的新证据

- 来源合同无法建立（所有者/权限持续缺失）→ **停止 RAG**，回退普通搜索 + 内容治理；
- 出现新的跨业务单元 ACL 需求 → 新建语料快照版本（snapshot v2），不得在旧快照内静默改权限；
- 第 10 周评测发现 gold source 缺失 → 回到本周新建 corpus 版本（不在数据集背后改文档）；
- 真实环境出现"文件在共享盘里所以应该能用"类推断抬头 → 重读本卡片"谁负责"一节。

## 诚实边界

本记录仅证明"语料可被治理成权限受控、可重复构建的快照"，
**不证明 RAG 会改善一线任务** —— 该问题留给第 10–12 周的冻结评测合同与只读 RAG 评测。

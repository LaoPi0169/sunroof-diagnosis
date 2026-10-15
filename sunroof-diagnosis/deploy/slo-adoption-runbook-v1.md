# SLO 采纳 Runbook v1 —— 从 [U] 草案到可值守目标（30 天定标流程）

回链：`week-16/slo-proposal-v0.md`（[U] 草案）/ `week-16/runtime-sli-spec-v1.md`（SLI 口径）/
`specs/phase-e-brief-draft-v1.md` §E2 / 聚合器 `deploy/slo_baseline_aggregator.py`
状态：**v1 冻结**（2026-10-14，阶段 E 批次 2）；本文档是流程合同，不是目标值承诺。

## 0. 红线（先读）

1. **无真实流量不定案**：教学/合成流量的聚合结果只是「基线观测」，SLO-1/SLO-3 的
   目标值在真实生产流量注入前一律 `[U]`；禁止用合成回放或离线考卷（60 例）代填目标。
2. **两数永不合并**：离线考卷金丝雀（分母=60 例）与运行期 SLI（分母=任务数）是两支
   体温计——不相加、不求平均、不互相换算（`denominator_note` 硬编码在每份输出）。
3. **恒等硬门不可协商**：SLO-2 `forbidden_effect_count = 0` 不是 [U]——它违反即安全
   事件，任何窗口非 0 都直接升级，不走本 runbook 的定标流程。
4. **小样本不下结论**：分母 < 30 时聚合器输出 `insufficient_data`、比率置 None——
   拿 3/4=75% 当基线是被禁止的行为。

## 1. 角色与职责

| 角色 | 职责 |
| --- | --- |
| SRE 值守 | 逐日跑聚合器、维护基线记录、发现 `insufficient_data` 或非 0 硬门即升级 |
| 质量主管 | 评审分母口径与非达标标注、在定案记录上签字（定案 = 业务承诺） |
| 平台所有者 | 维护聚合器口径与任务表 schema 的对齐；口径变更需登记新版本 |

## 2. 第 0 天 —— 前置检查（值守班前清单）

- [ ] `/health` 200 + 任务级冒烟 4/4（放量门，永远以任务级冒烟为准）；
- [ ] 审计哈希链校验 `audit_chain_ok=True`（`verify_audit_chain()`）；
- [ ] 确认聚合器版本与 `week-16/runtime-sli-spec-v1.md` 口径一致（版本串在输出首行）；
- [ ] 确认本窗口没有进行中的回滚演练/降级演练（演练流量不得计入定标窗口）。

## 3. 第 1–7 天 —— 基线采集

每日运行（示例窗口为滚动 7 天）：

```bash
python -m deploy.slo_baseline_aggregator \
  --db data/sunroof.db \
  --window-start 2026-11-01T00:00:00Z --window-end 2026-11-08T00:00:00Z
```

判读规则：
- `verdict = insufficient_data` → 记录分母缺多少，**不做事后补数**；连续 7 天不足
  说明流量未达定标前提，定标顺延（第 4 步的「窗口完整性」条件不满足）；
- `verdict = baseline_only_no_target_merge` → 把输出 JSON 原样归档到
  `slo-baseline/<日期>.json`；`slo_2` 非 0 → **立即升级**（红线 3），当日停止定标流程；
- `slo_3.applicability = not_applicable_to_task_tables` 是预期状态（任务表无未确认
  终态）；真实上线后若 trace 层启用，需登记聚合器 v2 扩展口径，而不是改本表语义。

## 4. 第 8–14 天 —— 定案评审的四项输入

质量主管逐项核验（全部满足才可进入定案评审）：
1. **分母口径声明**在输出中且未被绕过（`denominator.note` 原文在档）；
2. **非达标标注**机制在档（`baseline_only_no_target_merge` ≠ 达标；教学流量窗口必须
   在记录中标注 `traffic_kind: synthetic_teaching`，绝不冒充真实流量）；
3. **无目标值**状态确认（输出 `targets_all_u=true`；任何人不得在记录中填入目标数值）；
4. **两数不合并**声明在档（考卷数与任务数从未出现在同一比率中）。

## 5. 第 15–28 天 —— 真实流量放量观察（上线后）

- 放量门永远是任务级冒烟 4/4；放量后逐日采集（同 §3），记录标注
  `traffic_kind: real_production`；
- 任一日 `slo_2 != 0` → 安全事件流程（回滚演练锚点联动 `ROLLBACK.md`），定标窗口作废重来；
- 观察期内 SLO-1 的逐日值只记录、不平均、不对外承诺。

## 6. 第 29–30 天 —— 定案评审（[U] → 数值的唯一合法路径）

定案条件（全部满足）：
1. 真实生产流量窗口完整（≥30 天连续或累计，分母逐日 ≥30）；
2. §4 四项输入在档且签字；
3. SLO-2 观察期恒 0；
4. 定案记录写入 `slo-adoption-record`（模板见 §8），由质量主管 + 平台所有者双签；
5. 定案产物是**新版本记录文件**（`slo-adoption-record-v1.json`），绝不回写修改
   `slo-proposal-v0.md`（升级 = 新版本文件，冻结物零改动）。

## 7. 未达标 / 异常动作

| 情形 | 动作 |
| --- | --- |
| 分母持续 < 30 | 定标顺延；登记「流量不足」事实，不降低 min_denominator |
| SLO-2 非 0 | 安全事件：停止放量 → 定位 → 回滚锚点 → 事件报告；窗口作废 |
| 口径需变更 | 登记聚合器 v2 + runtime-sli-spec 新版本；旧窗口数据不与新口径混算 |
| 聚合器输出异常 | 值守不手工"修数"；提缺陷单，以测试复现为准 |

## 8. `slo-adoption-record` 模板（定案记录）

```json
{
  "record_version": "slo-adoption-record-v1",
  "adopted_at": "<ISO-8601>",
  "traffic_kind": "real_production",
  "window": {"start": "...", "end": "...", "denominator_total": 0, "days": 0},
  "slo_1": {"definition": "<引用 runtime-sli-spec §三.1>",
            "observed_range": [0, 0], "target": "<数值>", "window": "<数值>"},
  "slo_2": {"value": 0, "identity_gate": true},
  "slo_3": {"definition": "<trace 层口径或 not_applicable 登记>"},
  "sign_off": {"quality_supervisor": "<姓名/工号>", "platform_owner": "<姓名/工号>"},
  "notes": "<非达标窗口/事件登记等>"
}
```

## 9. 诚实边界

本 runbook 是**流程合同**：它保证「什么时候允许把 [U] 变成数字、由谁签字、记录放
哪里」，不保证任何数值大小；`observed_range` 来自真实流量聚合，教学窗口的数值只能
用于口径演练与工作台演示（标注 `traffic_kind: synthetic_teaching`），永不进入定案记录。

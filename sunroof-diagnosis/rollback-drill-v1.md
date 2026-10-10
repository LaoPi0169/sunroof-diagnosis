# 回滚演练记录（Rollback Drill v1）

- **规范版本**: `sunroof-rollback-drill-v1`（回链 `specs/mission-week-08.md` 第 4 天 + `ROLLBACK.md` 场景 A）
- **演练日期**: 2026-10（节点 ⑤ 收口时真实执行）
- **运行器**: `python deploy/run_rollback_drill.py`（可复现，每次运行覆盖更新证据）
- **证据产物**: `evidence/rollback-drill-proof.json`（真实运行产物，随提交入库）
- **注入物**: `config/sunroof-config-w08-rc1-bad.json`——**受控标注：禁止用于真实发布**，仅作演练证据归档。

## 1. 注入场景（对应 mission-week-08 第 4 天）

错误配置将偏差硬限从 **15.0%** 覆盖为 **0.01%**（`config_overrides` 增量），导致一切核对
（含 +1.05% 的常规偏差）都被阻断为 `needs_review / SPEC_HARD_LIMIT_EXCEEDED`——
一线"正常回答"静默变成"全部转人工"，这正是**没有任务级冒烟就会漏掉的假成功**：
`/health` 始终 200，进程毫无异常。

## 2. 三段闭环结果（真实运行）

| 阶段 | 动作 | 收敛器 | 冒烟 | 失败项 | 判定 |
| --- | --- | --- | --- | --- | --- |
| R0 known-good 基线 | 全新冷部署 + `sunroof-config-w08-v1` | exit 0，全部 converged | **4/4 PASS**（exit 0） | 无 | 部署正确性成立 |
| R1 错误配置注入 | 全新冷部署 + `sunroof-config-w08-rc1-bad` | exit 0（**收敛器不判对错**，如实收敛） | **3/4 FAIL**（exit 1） | **仅 SMOKE-01** | 假成功被冒烟拦截，部署自动阻断 |
| R2 配置收敛式回滚 | 全新冷部署 + 重放 known-good | exit 0 | **4/4 PASS**（exit 0） | 无 | 系统恢复，复验通过 |

**R1 归因干净**：SMOKE-02/03/04 均通过（阻断路径/越权拦截/幂等在错误配置下依然正确），
唯一失败项是漂移检测器 SMOKE-01——证明冒烟门抓的正是"配置把正常回答变成阻断"这类业务漂移。

## 3. 演练结论

```
[错误配置注入] ──> [SMOKE-01 捕获 answered -> needs_review 漂移] ──> [冒烟 exit 1, 部署拦截]
                                                                        │
[复验 4 条冒烟 4/4 通过] <── [重放 known-good 配置 (收敛器幂等, 零 DDL)] <──┘
```

- **已证明事实**: 在独立沙盒环境中，任务级冒烟能发现"进程健康但业务错误"的假成功；
  配置收敛式回滚可精准恢复到已知正确版本，且三阶段数据库表集合逐阶段一致（schema 004 零 DDL 漂移，未破坏数据兼容性）。
- **不能证明的事实**: 不证明生产环境大流量下的热回滚；不证明未来 DDL 迁移可逆
  （见 `ROLLBACK.md` 场景 B 诚实边界）；不证明多实例/负载均衡下的发布编排。

## 4. 与验收标准的对应

| 验收项 | 本演练证据 |
| --- | --- |
| 受控失败与明确回滚 | R1 注入 → 冒烟 exit 1；R2 重放 known-good，无删库 |
| 回滚后任务级复验 | R2 冒烟 4/4，响应/DB 终态/审计三向一致 |
| 越权与幂等不退化 | R1 中 SMOKE-03/04 仍通过（denied 零泄漏 + ALREADY_APPLIED） |
| 演练用错误配置受控标注 | `sunroof-config-w08-rc1-bad.json` description 字段声明禁止真实发布 |

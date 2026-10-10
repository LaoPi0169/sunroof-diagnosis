# Mission · 第 18 周 —— 值守冷交接（交接包 × 封存故障 × 完整版本包回滚）

> 用户拍板记录：非作者路径采用 **B 调包 + A 收口**（作者先做代理冷启动把交接包
> 调通，真实非作者复验留作 A 路收口）；并新增硬要求——8018 工作台**双角色隔离**：
> 所有配置必须管理员登录后可见可设，业务用户只能做其权限内操作；交付白话文操作手册。

## 1. 目标（Goal）

把第 15-17 周已建成的机制当作存量（不重新发明，手册 4015），补齐「非作者可安全
接管」的全部材料与演练面：

1. 发布材料体检（第 8 周清单对照六类绑定）→ `release-package-annotations-v1.md`
   + 缺口清单 → 补 `full-release-manifest-v2.json`（sha256/git ref, 无秘密）；
2. 任务式 `ops-handoff-pack-v1.md`（范围/症状表含禁止动作/开关与回滚四件套/
   恢复四+1 口径）+ `sealed-fault-pack-v1.md`（F-18.1/F-18.2 剧本+判定+评分+NO-GO）；
3. 值守交接工作台 8018（`workbench_handoff/`）：**双角色隔离**——业务用户免登录
   只见权限内操作（问答/预览/批准/执行/续跑/重放/升级），注入/开关/回滚/审计/
   清单/复验/重置全部要求管理员登录，服务端 403 强制；口令零泄漏；
4. B 路代理冷启动演练 + `cold-handoff-record-v1.md`（判断表六观察, 真实数据,
   `simulated_self_replay` / `handoff_gate: pending` 如实标注）；
5. `runbook-revision-log-v1.md`（调包摩擦点）→ `business-frontline-readback-v1.md`
   （角色模拟回读）→ `stage-exit-w18-v1.md`（出口评审）；
6. 工程收口：测试、脱敏扫描范围、README、CI 证据、git 合流。

## 2. 验收标准（Acceptance Gates）

| # | 门 | 证据 |
| --- | --- | --- |
| G1 | 7 个管理接口对未登录/伪 token 一律 403; 业务快照零配置字段 | tests/test_handoff_week18 test_01/04 |
| G2 | 管理登录口令/token 在任何状态/审计/响应零出现 | test_03（动态断言）+ STAGE-3 静态扫描 |
| G3 | F-18.1: 注入/开关两路同落 action_preview（0 效果/审批不消耗/探测恰 1 次）, 读不受阻 | test_06/07/09 + 演练轮 1/2 |
| G4 | F-18.2: 回归症状可见（引用废止版本+黄警）→ 回滚 rc-18.0（强制理由）→ 引用恢复现行 | test_12/15 + 演练轮 3 |
| G5 | 恢复=四+1 复验: 回滚前 fail / 回滚后 5/5 pass; 幂等重放无重复效果 | test_14/16/18 |
| G6 | 清单 v2 六类绑定齐全、绑定真实 sha256、零秘密 | test_21 |
| G7 | 写合同语义不回退: 无审批 400 / 拒绝 0 效果 | test_19/20 |
| G8 | 全量测试 122 → 145 全绿; STAGE-3 48 → 50 文件 0 违规; 冒烟 4/4 | evidence/ci-run-w18.log |
| G9 | 白话文操作手册覆盖双角色 + 判断表 + FAQ + 红线 | week-18/handoff-workbench-plain-guide-v1.md |
| G10 | B/A 路口径: 演练记录标注 simulated_self_replay / gate pending; A 路判据写明 | cold-handoff-record-v1.md + stage-exit-w18-v1.md |

## 3. 边界（Non-Goals）

- 不重新发明告警/降级/回滚机制（全部复用第 15-17 周冻结实现）;
- 版本包/问答为进程内教学模拟; 不模拟真实制品库/部署系统/模型 API;
- 不做生产凭据管理（教学固定口令, 手册如实标注诚实边界）;
- 业务/一线回读为课程角色模拟; 第 23-24 周 Capstone 重做交接, 不迁移本周成果。

## 4. 交付物落点

见 README「第 18 周」节清单（9 文档 + 工作台 3 件 + 测试 + CI 证据）。

## 5. 验证方式

```powershell
start_workbench_handoff.bat                       # 8018 双角色工作台
python -m unittest tests.test_handoff_week18 -v   # 23 用例
python ci/run_local_ci.py                         # 四阶段全绿
```

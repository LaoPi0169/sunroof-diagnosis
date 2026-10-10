# ROLLBACK —— 回滚手册（配置收敛式 + Git 三层保险）

- **规范版本**: `sunroof-rollback-v1`（回链 `../fde-roadmap-v1.md` §2 节点 ⑤ / `specs/mission-week-08.md` 第 4 天）
- **核心原则**: 回滚 = 退回**上一个已知正确版本**（`previous_known_good_release`），绝不"删库重来"破坏数据兼容性。
- **诚实边界**: 本手册的回滚对象是**配置数据**而非代码；DDL 迁移只增不改、不做降级（场景 B）。

## 1. 已知正确版本锚点

| 锚点 | 值 | 位置 |
| --- | --- | --- |
| 前已知正确版本 | `sunroof-w07-baseline`（节点 04，26/26 全绿） | git tag + `release-manifest-v1.json` |
| 本版本 | `sunroof-app-w08-r0`（配置 `sunroof-config-w08-v1`） | `release-manifest-v1.json` |
| 配置收敛器 | `deploy/apply_policy_config.py`（幂等 UPSERT，白名单键，零 DDL） | 收敛报告逐项 `converged` |
| 放量门 | `deploy/smoke_w08.py` 4/4 | 冒烟退出码 |

## 2. 场景 A —— 配置错误（主场景，配置收敛式回滚）

**症状**: 任务级冒烟失败（如 SMOKE-01 由 `answered` 漂移为 `needs_review`），`/health` 仍 200。
**回滚步骤**:

```bash
# 1) 确认漂移归因（冒烟失败详情）
python deploy/smoke_w08.py --base-url http://127.0.0.1:8020 --db <db_path>

# 2) 重放 known-good 配置（幂等收敛，不建表不删库）
python deploy/apply_policy_config.py --config config/sunroof-config-w08.json --db <db_path>

# 3) 复验：全新初始化数据版本后冷部署 + 冒烟 4/4（任务终态不可重放，见 deploy-README §3）
python deploy/run_rollback_drill.py     # 自动完成 R0/R1/R2 三段闭环并落证据
```

**不兼容动作**: 直接手工改库、删除 `policy_config` 表、跳过冒烟直接放量。

## 3. 场景 B —— Schema 迁移后回滚（诚实边界：不支持降级）

迁移 001-004 为追加式冻结清单（`IF NOT EXISTS` / `INSERT OR IGNORE`），**不做 DOWN 迁移**。
若节点 06+ 的新迁移在生产化场景中必须回退：以 `previous_known_good_release` 的代码基线
冷部署**全新数据库**（表集合兼容），业务数据另行迁移——不在本沙盒范围内宣称自动可逆。

## 4. 场景 C —— 坏版本代码（Git 三层保险）

1. **第一层 · tag 回退**: `git checkout sunroof-w07-baseline`（冻结基线，只读）；
2. **第二层 · 分支回退**: main 历史线性可追溯（`--no-ff` 合并节点），`git revert` 单节点撤销；
3. **第三层 · 归档包**: `git archive HEAD` 即 CI STAGE-4 的部署物（清单 `code_revision` 绑定基线 commit），离线可重建。
   回退代码后**必须**重跑 `ci/run_local_ci.py`（STAGE-4 冒烟 4/4 才算恢复）。

## 5. 场景 D —— 冒烟失败但配置正确（状态机终态消耗）

任务夹具为一次性状态机终态（`confirmed`/`escalated` 不可重放）。复验失败先检查是否为
旧库重放：按 `deploy-README` §2 第 3 小步用**全新数据版本**重新初始化后再冒烟。
这不是回滚场景——数据库兼容性未破坏，属部署操作纪律。

## 6. 场景 E —— 收敛器报错（fail fast 处置）

| 退出码 | 含义 | 处置 |
| --- | --- | --- |
| 0 | 全部收敛 | 继续冒烟 |
| 1 | 存在未收敛项 / 非白名单键被拒 | 核对配置文件键名（白名单仅 2 个键）；**绝不**为通过检查而扩大收敛器白名单 |
| 2 | 配置文件或数据库不存在 | 先执行迁移（`init_db`）再收敛 |

## 7. 演练证据

- `rollback-drill-v1.md`：三段闭环记录（R0 基线 4/4 → R1 注入被 SMOKE-01 拦截 → R2 回滚 4/4 复验）；
- `evidence/rollback-drill-proof.json`：真实运行产物（`python deploy/run_rollback_drill.py` 可复现）。

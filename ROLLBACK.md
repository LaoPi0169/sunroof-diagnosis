# 回滚指南（ROLLBACK）—— 第七周基线 → 第八周开发

> 本文件说明 Week 08 的两类回滚：**首选：配置收敛式回滚**（业务数据零损失，秒级，不删库、不重建表、不改 DDL）
> 与**兜底：Git 版本回滚**（代码层回退到 `week07-baseline` 标签）。所有命令均在项目根目录 PowerShell 执行。
> 版本绑定见 `release-manifest-v1.json`（known-good 配置 `northstar-config-w08-v1` 的 sha256 在清单中登记）。

## 归档结构（三层保险）

| 层 | 载体 | 位置 / 名称 | 说明 |
| --- | --- | --- | --- |
| ① 主归档 | Git 提交 + 标签 | `main` 分支，标签 `week07-baseline`（代码基线）/ `week08-baseline`（发布基线） | 代码唯一事实来源，可精确回滚 |
| ② 开发分支 | Git 分支 | `week08`（自 `week07-baseline` 切出） | 第八周工作在此分支进行，`main` 保持第七周基线 |
| ③ known-good 配置 | `config/policy-config.json` | sha256 登记于 `release-manifest-v1.json` | 配置收敛式回滚的"已知正确版本" |

## 场景 0（首选）：配置收敛式回滚 —— 部署后业务行为异常（如政策误匹配/版本冲突）

适用：数据库连接正常、schema 正确，但**配置数据被改坏**（典型：已废弃政策被复活导致 POLICY_CONFLICT，
复现见 `rollback-drill-v1.md` Round 1）。步骤：

```powershell
# 1) 确认异常面: 冒烟会在部署门处拦截 (三向对账失败), /health 仍 200 属"假成功"
python deploy\smoke_w08.py --base-url http://127.0.0.1:8000 --db %FDE_DB_PATH%

# 2) 重放 known-good 配置 -> 幂等 UPDATE 收敛回基线断言 (不删库、不重建表、不改 DDL)
python deploy\apply_policy_config.py --config config\policy-config.json --db %FDE_DB_PATH%
#    期望: 每条 policy_id "converged": true, 退出码 0

# 3) 复验 (口径见 rollback-drill-v1.md §4):
#    3a) 配置断言恢复 + 表结构/行数不变 (对账脚本或人工 SQL);
#    3b) 全新库 + known-good 配置重跑 4 条冒烟 4/4 -> 证明回滚后系统与发布基线行为一致。
python deploy\smoke_w08.py --base-url http://127.0.0.1:8000 --db <全新库路径>

# 4) 归档: 把注入用的坏配置与处置记录留档 (本次为 config/policy-config-rc1-bad.json + rollback-drill-v1.md)
```

边界（诚实不夸大）：该回滚只恢复**配置数据**（`policies` 表白名单三列），不回退代码、不回退 DDL、
不清理错误配置期间产生的业务工单状态（审计留痕按设计只增不改）。

## 0. 日常确认当前状态

```powershell
git status                 # 当前分支与未提交改动
git --no-pager log --oneline --decorate -n 5
git tag -n                 # 查看标签
git diff week07-baseline --stat   # 与第七周代码基线的差异；无输出 = 与代码基线完全一致
```

## 场景 A：第八周改坏了，恢复文件到第七周基线（保留提交历史）

```powershell
# 1) （可选）先把当前现场存起来，避免误丢
git stash push -m "week08-wip"          # 或先 git add -A && git commit -m "wip"

# 2) 把工作区与暂存区整体恢复到基线版本
git restore --source=week07-baseline --staged --worktree -- ./

# 3) 上面一步不会删除"第八周新增的文件"（config/ deploy/ ci/ 等）；如需连新文件一起清掉：
git clean -nd                            # 先预览将要删除的文件（重要！）
git clean -fd                            # 确认无误后删除未跟踪文件/目录
                                         # 加 -x 可连 data/ 等被 .gitignore 忽略的文件一起删

# 4) 提交一条回滚记录（在 week08 分支上）
git add -A
git commit -m "week08: 回滚到 week07-baseline 快照"

# 5) 验证基线可运行（预期 Ran 20 tests OK —— Week 08 新增用例随回滚一并移除）
python -m unittest discover -s tests -v
```

## 场景 B：第八周整体废弃，硬回滚（丢弃其后所有提交）

```powershell
# 1) 保底：先给第八周现场留个分支（强烈建议，反悔还有救）
git branch week08-abandoned

# 2) 把当前分支指针与工作区一起硬回滚到基线
git reset --hard week07-baseline

# 3) 验证
git --no-pager log --oneline -n 3        # 只剩基线提交
git status                               # 干净
```

## 场景 C：只想临时查看/切回第七周代码（不动分支与历史）

```powershell
git switch week07-baseline               # detached HEAD 只读查看（git status 会提示）
git switch week08                        # 看完返回开发分支

# 不切换工作区、直接看基线版本里的某个文件：
git --no-pager show week07-baseline:application/services.py
```

## 场景 D：误操作丢失提交（如 reset --hard 之后反悔）

```powershell
git --no-pager reflog                    # 找到丢失前的操作点（默认保留 90 天）
git reset --hard HEAD@{2}                # 按需回跳到 reflog 中的任意一条
git --no-pager fsck --lost-found         # 兜底：查找悬空提交
```

## 场景 E：无 Git / 整个目录损坏，用离线 zip 恢复

```powershell
# 1) 用 archive\week07-baseline-20260930.zip 覆盖/重建项目目录
Expand-Archive -Path archive\week07-baseline-20260930.zip -DestinationPath D:\WebstormProjects\fde-course-week-05 -Force

# 2) 重建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3) 启动时 lifespan 会自动重建 data\northstar.db（幂等迁移 + Fixtures）
uvicorn application.main:app --port 8000

# 4) 验证（预期 Ran 20 tests OK）
python -m unittest discover -s tests -v
```

## 第八周推荐工作流（防止污染第七周基线）

```powershell
git switch week08                        # 第八周全部工作都在此分支
# ... 开发，小步提交：每次测试通过后 git add -A && git commit ...
git diff week07-baseline --stat          # 随时查看相对第七周改了什么

# 第八周验收通过后合并回 main 并打第八周标签：
git switch main
git merge --no-ff week08 -m "week08: 可重复部署与回滚 (配置收敛层+发布清单+CI+任务级部署门)"
git tag -a week08-baseline -m "Week 08 baseline"
```

## 远端备份（可选但推荐）

```powershell
# 在 GitHub/Gitee 建一个私有空仓库后：
git remote add origin <你的仓库 URL>
git push -u origin main week08 --tags    # 分支与标签一并推送，即异机保险
```

## 快速回滚速查（一张表）

| 我想要… | 命令 |
| --- | --- |
| 恢复被改坏的配置数据（首选，零数据损失） | `python deploy\apply_policy_config.py --config config\policy-config.json --db %FDE_DB_PATH%` |
| 看第七周代码 | `git switch week07-baseline`（看完 `git switch week08`） |
| 文件恢复到第七周，保留历史 | `git restore --source=week07-baseline --staged --worktree -- ./` |
| 彻底回到第七周（丢弃第八周提交） | `git reset --hard week07-baseline` |
| 找回被 reset 丢掉的提交 | `git reflog` → `git reset --hard <hash>` |
| 没有 git 时恢复 | 解压 `archive/week07-baseline-*.zip` → 重建 `.venv` → 跑测试 |

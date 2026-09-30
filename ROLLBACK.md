# 回滚指南（ROLLBACK）—— 第六周基线 → 第七周开发

> 本文件随第六周基线一起提交，说明如何把项目恢复到第六周（Week 06）已验收基线（tag `week06-baseline`，含 Week 05/06 全部能力）。
> 所有命令均在项目根目录 `D:\WebstormProjects\fde-course-week-05` 的 PowerShell 中执行。
> **口径说明**：下文各场景命令中的 `week05-baseline` / `week06`，按当前口径平移为 `week06-baseline` / `week07` 使用（命令机制完全相同）。

## 归档结构（三层保险）

| 层 | 载体 | 位置 / 名称 | 说明 |
| --- | --- | --- | --- |
| ① 主归档 | Git 提交 + 标签 | `main` 分支，标签 `week06-baseline` | 代码唯一事实来源，可精确回滚 |
| ② 开发分支 | Git 分支 | `week07`（自 `week06-baseline` 切出） | 第七周工作在此分支进行，`main` 保持第六周基线 |
| ③ 离线快照 | zip 文件 | `archive/week06-baseline-20260930.zip`（可执行 `git archive week06-baseline` 导出） | 不含 `.venv`/`data`；建议另拷贝一份到网盘等异机位置 |

## 0. 日常确认当前状态

```powershell
git status                 # 当前分支与未提交改动
git --no-pager log --oneline --decorate -n 5
git tag -n                 # 查看标签
git diff week06-baseline --stat   # 与第六周基线的差异；无输出 = 与基线完全一致
```

## 场景 A：第七周改坏了，恢复文件到第六周基线（保留提交历史）

```powershell
# 1) （可选）先把当前现场存起来，避免误丢
git stash push -m "week06-wip"          # 或先 git add -A && git commit -m "wip"

# 2) 把工作区与暂存区整体恢复到基线版本
git restore --source=week05-baseline --staged --worktree -- ./

# 3) 上面一步不会删除"第六周新增的文件"；如需连新文件一起清掉：
git clean -nd                            # 先预览将要删除的文件（重要！）
git clean -fd                            # 确认无误后删除未跟踪文件/目录
                                         # 加 -x 可连 data/ 等被 .gitignore 忽略的文件一起删

# 4) 提交一条回滚记录（在 week06 分支上）
git add -A
git commit -m "week06: 回滚到 week05-baseline 快照"

# 5) 验证基线可运行（预期 5/5 OK）
python -m unittest discover -s tests -v
```

## 场景 B：第六周整体废弃，硬回滚（丢弃其后所有提交）

```powershell
# 1) 保底：先给第六周现场留个分支（强烈建议，反悔还有救）
git branch week06-abandoned

# 2) 把当前分支指针与工作区一起硬回滚到基线
git reset --hard week05-baseline

# 3) 验证
git --no-pager log --oneline -n 3        # 只剩基线提交
git status                               # 干净
```

## 场景 C：只想临时查看/切回第五周代码（不动分支与历史）

```powershell
git switch week05-baseline               # detached HEAD 只读查看（git status 会提示）
git switch week06                        # 看完返回开发分支

# 不切换工作区、直接看基线版本里的某个文件：
git --no-pager show week05-baseline:application/services.py
```

## 场景 D：误操作丢失提交（如 reset --hard 之后反悔）

```powershell
git --no-pager reflog                    # 找到丢失前的操作点（默认保留 90 天）
git reset --hard HEAD@{2}                # 按需回跳到 reflog 中的任意一条
git --no-pager fsck --lost-found         # 兜底：查找悬空提交
```

## 场景 E：无 Git / 整个目录损坏，用离线 zip 恢复

```powershell
# 1) 用 archive\week05-baseline-20260930.zip 覆盖/重建项目目录
Expand-Archive -Path archive\week05-baseline-20260930.zip -DestinationPath D:\WebstormProjects\fde-course-week-05 -Force

# 2) 重建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3) 启动时 lifespan 会自动重建 data\northstar.db（幂等迁移 + Fixtures）
uvicorn application.main:app --port 8000

# 4) 验证（预期 5/5 OK）
python -m unittest discover -s tests -v
```

## 第六周推荐工作流（防止污染第五周基线）

```powershell
git switch week06                        # 第六周全部工作都在此分支
# ... 开发，小步提交：每次测试通过后 git add -A && git commit ...
git diff week06-baseline --stat          # 随时查看相对第六周改了什么

# 第七周验收通过后合并回 main 并打第七周标签：
git switch main
git merge --no-ff week07 -m "week07: 一线角色与权限任务闭环"
git tag -a week07-baseline -m "Week 07 baseline"
```

## 远端备份（可选但推荐）

```powershell
# 在 GitHub/Gitee 建一个私有空仓库后：
git remote add origin <你的仓库 URL>
git push -u origin main week07 --tags    # 分支与标签一并推送，即异机保险
```

## 快速回滚速查（一张表）

| 我想要… | 命令 |
| --- | --- |
| 看第六周代码 | `git switch week06-baseline`（看完 `git switch week07`） |
| 文件恢复到第六周，保留历史 | `git restore --source=week06-baseline --staged --worktree -- ./` |
| 彻底回到第六周（丢弃第七周提交） | `git reset --hard week06-baseline` |
| 找回被 reset 丢掉的提交 | `git reflog` → `git reset --hard <hash>` |
| 没有 git 时恢复 | 解压 `archive/week06-baseline-*.zip` → 重建 `.venv` → 跑测试 |

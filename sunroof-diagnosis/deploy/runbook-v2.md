# 部署 Runbook v2 —— 生产发布包运行手册（阶段 D 批次 3）

- **规范版本**: `sunroof-deploy-runbook-v2`（回链 `specs/production-hardening-brief-v1.md` D3
  + `release-manifest-v2.json` + `adr-005-production-hardening-adaptations.md`）
- **目标读者**: 把本系统部署到 Ubuntu 服务器 Docker 容器的运维/值守人员。
  Docker 引擎安装与镜像获取的完整教程见 `deploy/ubuntu-docker-deploy.md`（本手册不重复）；
  浏览器交互验证教程见 `deploy/USER-GUIDE.md`。
- **发布对象**: `sunroof-app-d3-r0`（清单六类绑定见 `release-manifest-v2.json`：
  代码基线 / 配置 sha256 / **schema 005** / 冻结合同清单 / 冒烟门 / CI 入口）
- **v2 相比 v1（deploy-README.md，保留）新增**: 非 root 生产化镜像（uid/gid 10001）、
  签名令牌秘密注入（`SUNROOF_TOKEN_SECRET`）、备份/恢复工具（数据卷 + 审计哈希链
  快照一致性门）、运维期哈希链校验步骤。

## 0. 运行时不变量（先读）

1. **非 root**: 容器内进程以专用用户 `sunroof`（uid/gid 10001）运行——镜像构建期
   全量测试门即以该身份执行，权限配错 = 构建失败；验机命令：
   `docker exec sunroof-workbench id` → 期待 `uid=10001(sunroof) gid=10001(sunroof)`；
2. `/healthz` 200 只代表进程存活；放量只认任务级冒烟 4/4（§3）；
3. 秘密零硬编码：`SUNROOF_ADMIN_TOKEN` 与 `SUNROOF_TOKEN_SECRET` 只经环境变量注入，
   镜像与 git 里零字面量（脱敏静态门 + 测试复验）；
4. 迁移 001-005 只增不改不做降级（`ROLLBACK.md` 场景 B）；回滚 = 发布清单锚点 +
   配置收敛式回滚（场景 A）；
5. 教学沙盒定位不变：合成夹具三源 / 零 LLM / 零真实 SSO / 外部写效果恒 0。

## 1. 一键部署（compose 生产形态）

```bash
cd ~/sunroof-diagnosis
export SUNROOF_ADMIN_TOKEN='换成你自己的强口令'
export SUNROOF_TOKEN_SECRET='换成你自己的随机签名秘密'   # 留空=进程随机, 重启即失效
docker compose up -d --build
docker compose ps          # STATUS 应为 Up (healthy)
docker exec sunroof-workbench id    # 验证非 root: uid=10001(sunroof)
```

或裸 `docker run`（离线机 / 无 compose）——参数同 `ubuntu-docker-deploy.md` §4b，
额外追加 `-e SUNROOF_TOKEN_SECRET="$SUNROOF_TOKEN_SECRET"`。

## 2. 部署验证（按顺序）

```bash
# ① 服务身份探针 (零鉴权, 零敏感信息)
curl -s http://127.0.0.1:8018/healthz
# 期待: {"ok": true, "service": "sunroof-diagnosis-workbench", "project": "天窗异响诊断", "template": false}

# ② 业务链路 (免登录角色问答)
curl -s -X POST http://127.0.0.1:8018/api/ask \
  -H 'Content-Type: application/json; charset=utf-8' \
  --data-binary '{"role":"pqe","query":"保修期内维修收费吗"}'

# ③ 双角色隔离依然成立: 管理面未登录必须 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8018/api/admin/audit

# ④ 容器内全量测试复测 (可选)
docker exec sunroof-workbench python -m unittest discover -s tests
```

浏览器打开 `http://<服务器IP>:8018` 做交互验证（面板逐项操作与预期
见 `deploy/USER-GUIDE.md`；业务用户免登录，管理面用 `SUNROOF_ADMIN_TOKEN` 登录）。

## 3. 备份（数据卷 + 审计链快照一致性门, D3-c）

备份 = SQLite backup API 在线一致快照 + **快照后立即校验**（`PRAGMA
integrity_check` + `verify_audit_chain()` 三重校验）。**任一校验不过 → 快照文件
回滚删除、退出码 1**——绝不产出"看似好实则坏"的备份。产出两件套：
`<label>-<UTC时间戳>.db` + 同名 `.meta.json`（含 `db_sha256` / 链结果 / 表清单）。

```bash
# 方式 A · 容器内执行 (推荐; 备份落在数据卷 /app/data/backups/ 内, 随卷持久化):
docker exec sunroof-workbench python deploy/backup_snapshot.py \
  --db /app/data/sunroof.db --out-dir /app/data/backups --label nightly

# 方式 B · 宿主机直接对数据卷路径执行 (需 docker volume inspect sunroof-data 找到挂载点):
python3 deploy/backup_snapshot.py --db /var/lib/docker/volumes/sunroof-data/_data/sunroof.db \
  --out-dir /srv/backups --label nightly

# 运维期哈希链校验 (不新做快照, 只校验既有备份 + 元数据逐字节比对):
docker exec sunroof-workbench python deploy/backup_snapshot.py \
  --verify-only --backup-file /app/data/backups/nightly-<时间戳>.db
```

建议节奏（cron 示例，宿主机）：
```cron
15 2 * * *  cd /srv/sunroof-diagnosis && docker exec sunroof-workbench \
             python deploy/backup_snapshot.py --db /app/data/sunroof.db \
             --out-dir /app/data/backups --label nightly >> /var/log/sunroof-backup.log 2>&1
```

诚实边界：本工具保证"快照那一刻审计链自洽可复算"；异地容灾、保留策略与
加密存储不在本沙盒范围（请自行把 `--out-dir` 指向受保护存储）。

## 4. 恢复（fail-fast + 原子替换, D3-c）

**前置条件：先停应用**（SQLite 库文件不允许并发写者期间被替换）：
`docker compose stop`（或 `docker rm -f sunroof-workbench`）。

```bash
# 1) 恢复 (自动: 恢复前校验备份完整性+哈希链+元数据 sha256 → 目标库安全快照
#    <db>.pre-restore-<ts>.db → 原子替换 + 清 WAL 侧车 → 恢复后复验):
docker run --rm -v sunroof-data:/app/data -v /srv/backups:/backups \
  -w /app sunroof-workbench:latest \
  python deploy/restore_snapshot.py \
    --backup-file /backups/nightly-<时间戳>.db --db /app/data/sunroof.db

# 2) 起应用并复验:
docker compose up -d && curl -s http://127.0.0.1:8018/healthz
```

退出码：`0` 成功；`1` 校验失败（**目标库零触碰**，逐字节不变）；
`2` 输入不存在。恢复后哈希链校验不过 → 不要起应用，用
`<db>.pre-restore-<ts>.db` 安全快照回退（它本身也带 `.meta.json`）。

## 5. 运维期审计哈希链校验（无需备份，直接对在役库）

```bash
docker exec sunroof-workbench python -c \
  "import sys; sys.path.insert(0,'/app'); \
   from application.database import verify_audit_chain; \
   import json; print(json.dumps(verify_audit_chain()))"
# 期待: {"ok": true, ...}; first_bad 非 null => 定位到首个坏 run_id, 按 ROLLBACK.md 处置
```

## 6. 回滚锚点（与 ROLLBACK.md 联动）

| 锚点 | 值 | 位置 |
| --- | --- | --- |
| 本版本 | `sunroof-app-d3-r0`（schema 005 + 非 root 镜像） | `release-manifest-v2.json` |
| 前已知正确版本 | `sunroof-w18-d2-base`（commit `9981bc8…`） | 清单 `code_revision` |
| 镜像精确回溯 | `ghcr.io/laopi0169/sunroof-workbench:sha-<hash>` | GHCR |
| 配置回滚 | `deploy/apply_policy_config.py`（配置收敛式，零 DDL） | ROLLBACK.md 场景 A |
| 数据回滚 | 本手册 §4 恢复流程（含恢复前安全快照） | ROLLBACK.md |

镜像版本回退：`docker pull …:sha-<旧hash>` → `docker rm -f sunroof-workbench` →
重跑 §1 run（数据卷保留，schema 兼容由"迁移只增不改"保证）。

## 7. 已知边界（诚实声明）

- 单机单容器教学沙盒：SQLite 无多实例编排、无热回滚、无异地容灾；
- 备份工具校验的是**审计链一致性与库完整性**，不校验业务正确性（放量门永远是任务级冒烟 4/4）；
- `SUNROOF_TOKEN_SECRET` 更换后，此前签发的 `sr1.*` 令牌全部失效（HMAC 秘密轮换语义），
  静态教学令牌不受影响；
- 非 root 化覆盖容器内进程；宿主机上以 root 直跑脚本仍以宿主机权限为准。
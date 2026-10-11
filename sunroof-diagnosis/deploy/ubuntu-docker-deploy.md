# Ubuntu 部署指南 —— 天窗诊断工作台（Docker 版，端口 8018）

> 目标读者：负责把 `sunroof-diagnosis/` 工作台发布到 Ubuntu 服务器的运维同事。
> 应用只用 Python 标准库（零 pip 依赖），镜像基于 `python:3.11-slim`，
> 构建期自动跑全量集成测试做质量门，测试不过则镜像构建失败。
> 管理面口令只经环境变量注入，镜像与 git 仓库零硬编码。

## 0. 前置条件

- Ubuntu 20.04 / 22.04 / 24.04（x86_64 或 arm64）
- 能访问外网（拉 Docker 官方源与基础镜像）；离线环境见 §7
- 一个非 root 账号（可 sudo）

## 1. 安装 Docker Engine（官方源，含 compose 插件）

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER     # 免 sudo 跑 docker; 注销重登生效
docker --version && docker compose version   # 验证
```

## 2. 拿到项目代码（三选一）

```bash
# 方式 A: git 拉取 (推荐)
git clone <你的仓库地址> sunroof-diagnosis
cd sunroof-diagnosis && git checkout sunroof-w18-r1   # 封存 tag

# 方式 B: 从 Windows 开发机 scp 传 git archive 包 (与 CI STAGE-4 同口径)
#   Windows 端:  git -C d:\WebstormProjects\fde-course-week-05\sunroof-diagnosis archive --format=zip -o sunroof-diagnosis.zip HEAD
scp sunroof-diagnosis.zip user@server:/tmp/
#   Ubuntu 端:
mkdir -p ~/sunroof-diagnosis && cd ~/sunroof-diagnosis \
  && unzip -o /tmp/sunroof-diagnosis.zip

# 方式 C: 直接 scp 整个目录 (开发机 → 服务器)
scp -r user@windows-dev:d/WebstormProjects/fde-course-week-05/sunroof-diagnosis ~/
```

> 注意：只需要 `sunroof-diagnosis/` 子目录本身。工作区根目录那套课程模板
> （`workbench_handoff` 模板版、根目录同名 bat）**不要**一起部署。

## 3. 构建镜像（构建期即跑 76 项集成测试）

```bash
cd ~/sunroof-diagnosis
docker build -t sunroof-workbench:latest .
# 收尾两行应显示:
#   Ran 76 tests in ~25s   /   OK
```

## 4a. 一键启动（docker compose，推荐）

```bash
export SUNROOF_ADMIN_TOKEN='换成你自己的强口令'
docker compose up -d --build
docker compose ps        # STATUS 应为 Up (healthy)
```

## 4b. 或者裸 docker run（不用 compose）

```bash
export SUNROOF_ADMIN_TOKEN='换成你自己的强口令'
docker run -d --name sunroof-workbench \
  -p 8018:8018 \
  -e SUNROOF_ADMIN_TOKEN="$SUNROOF_ADMIN_TOKEN" \
  -e SUNROOF_BIND=0.0.0.0 -e SUNROOF_PORT=8018 \
  -v sunroof-data:/app/data \
  --restart unless-stopped \
  sunroof-workbench:latest
```

## 5. 验证部署（按顺序）

```bash
# ① 服务身份探针 —— 必须返回 sunroof-diagnosis-workbench (证明不是模板页面)
curl -s http://127.0.0.1:8018/healthz
# 期待: {"ok": true, "service": "sunroof-diagnosis-workbench",
#        "project": "天窗异响诊断", "template": false}

# ② 首页可达且是自研工作台
curl -s http://127.0.0.1:8018/ | grep -o 'sunroof-diagnosis'

# ③ 问答链路 (UTF-8 注意: 见 §8 常见坑 3)
curl -s -X POST http://127.0.0.1:8018/api/ask \
  -H 'Content-Type: application/json; charset=utf-8' \
  --data-binary '{"role":"pqe","query":"保修期内维修收费吗"}'

# ④ 管理面未登录必须 403 (双角色隔离在容器里依然成立)
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8018/api/admin/audit
# 期待: 403
```

浏览器打开 `http://<服务器IP>:8018`，页头应看到绿色徽标
**"项目: 天窗异响诊断 (sunroof-diagnosis) · 自研工作台"**。

## 6. 防火墙与开机自启

```bash
# 只对内网段放行 8018 (按需替换网段; 教学沙盒不建议直接暴露公网)
sudo ufw allow from 192.168.0.0/16 to any port 8018 proto tcp
sudo ufw status numbered

# restart: unless-stopped 已写入 compose/run 参数, 服务器重启自动拉起;
# Docker 服务本身开机自启:
sudo systemctl enable docker
```

## 7. 离线服务器（无法访问外网）

在能上网的同架构机器上：

```bash
docker build -t sunroof-workbench:latest .
docker save -o sunroof-workbench.tar sunroof-workbench:latest
# 拷贝 sunroof-workbench.tar 到离线机后:
docker load -i sunroof-workbench.tar
# 再按 §4b 裸 docker run 启动 (离线机无 compose 也可)
```

## 8. 常见坑

1. **8018 被占**：`sudo ss -ltnp | grep 8018` 找 PID；若是宿主机上直接跑的
   模板 week-018 进程，`kill <pid>` 后重启容器。工作台绑定失败会打印
   `bind_failed` 的 JSON 提示。
2. **口令没生效**：容器里口令来自环境变量；改口令 =
   `docker compose up -d`（改完 `.env`/export 后重建）或
   `docker rm -f` 后带新 `-e` 重跑。
3. **curl 中文乱码/答案错**：务必 `-H 'Content-Type: application/json;
   charset=utf-8'` 且用 `--data-binary`；浏览器/前端 fetch 默认 UTF-8 无此问题。
4. **权限**：`docker: permission denied` → `sudo usermod -aG docker $USER`
   后重新登录。
5. **升级版本**：拉新代码 → `docker build -t sunroof-workbench:latest .` →
   `docker rm -f sunroof-workbench` → 重跑 §4。数据卷 `sunroof-data` 不受影响。

## 9. 日常运维

```bash
docker logs -f sunroof-workbench        # 启动横幅/绑定信息
docker compose ps                        # 健康状态 (healthy)
docker volume inspect sunroof-data       # 语料/工单库落盘位置
docker exec sunroof-workbench python -m unittest discover -s tests  # 容器内复测
```

## 10. 诚实边界（随部署一起交付）

容器化不改变教学沙盒定位：生成层为确定性抽取式替身（零 LLM）；工单与版本包为
进程内模拟，唯一"外部效果"是隔离 TicketService 计数器；管理面口令请勿与真实
系统复用。页面页脚的"诚实边界"声明与 `README.md` §实现说明同步有效。

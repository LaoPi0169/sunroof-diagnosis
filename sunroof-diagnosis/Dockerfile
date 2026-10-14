# 天窗异响诊断 (sunroof-diagnosis) · 值守交接工作台 —— 生产化镜像 (阶段 D 批次 3, D3-a)
# D3-a 变更 (回链 specs/production-hardening-brief-v1.md):
#   1) 非 root 运行: 专用系统组/用户 sunroof (uid/gid 10001), 运行时进程不再持有 root;
#   2) 构建期质量门以非 root 身份执行 —— 镜像内权限配错 = 测试失败 = 构建失败 (自证);
#   3) HEALTHCHECK 保留; 构建期全量测试门保留 (测试不过, 镜像根本产不出来)。
# 应用只用 Python 标准库 + fastapi/uvicorn/pydantic/httpx (构建期测试门所需)。
FROM python:3.11-slim

LABEL org.opencontainers.image.title="sunroof-workbench" \
      org.opencontainers.image.description="天窗异响诊断 · 只读诊断值守交接工作台 (教学沙盒, 非 root)" \
      org.opencontainers.image.source="https://github.com/LaoPi0169/sunroof-diagnosis"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SUNROOF_BIND=0.0.0.0 \
    SUNROOF_PORT=8018

WORKDIR /app

# 运行时与构建期质量门都依赖 fastapi/uvicorn/pydantic/httpx
# (tests 直接 import application.main -> fastapi; 构建期质量门在镜像内跑全量测试)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 全仓库拷贝: canary 管理按钮依赖 week-12 冻结答案键与 evaluation 考卷
# (与 CI STAGE-4 git archive 冷部署口径一致); data/*.db 由 .dockerignore
# 排除, 首次启动/构建期自动重建, 持久化交给数据卷。
COPY . /app

# 非 root 化: 专用系统组/用户 sunroof (uid/gid 10001, 无登录 shell);
# /app 归属 sunroof (构建期测试门写 data/ 与脱敏探针、运行时冷启动自愈语料库均需写权限)。
# 修正记录:
#   (2026-10-11)
#   1) build_corpus 的 db_path 是必填位置参数, 原写法 build_corpus() 在镜像内 TypeError 秒崩;
#      构建期用 /tmp 临时库 (运行时由应用首启在数据卷内重建语料);
#   2) 原 `| tail -n 3` 管道会吞掉 unittest 退出码 (sh 管道取末命令退出码) —— 测试失败将
#      被误判为构建成功; 改为先落日志再回显尾部并以真实退出码结束。
#   (2026-10-12)
#   3) 该 RUN 行曾临时注释 (Linux CI 未绿前由 GitHub CI STAGE-2 承担全量用例);
#      当日远程四阶段 CI 首次全绿后 RUN 行恢复 —— CI 与镜像构建双质量门同时生效。
#   (2026-10-14, D3-a)
#   4) 非 root 化: useradd sunroof(uid/gid 10001) + chown /app, USER 切换后再跑构建期
#      测试门 —— 测试门同时验证「非 root 可运行」(权限缺失 = 构建失败, 不产出坏镜像)。
RUN groupadd --gid 10001 sunroof \
    && useradd --uid 10001 --gid sunroof --create-home --shell /usr/sbin/nologin sunroof \
    && chown -R sunroof:sunroof /app
USER sunroof
RUN python -c "from corpus.build_corpus import build_corpus; build_corpus('/tmp/corpus-w09-build.db', write_manifest=False)" \
    && python -m unittest discover -s tests > /tmp/unittest.log 2>&1; rc=$?; grep -E "^(FAIL|ERROR):" /tmp/unittest.log; tail -n 40 /tmp/unittest.log; exit $rc
EXPOSE 8018

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8018/healthz',timeout=2); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["python", "-m", "workbench_handoff.app"]

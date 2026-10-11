# 天窗异响诊断 (sunroof-diagnosis) · 值守交接工作台 —— Docker 镜像
# 应用只用 Python 标准库 (零第三方依赖), 镜像极小; 构建期跑全量测试做质量门。
FROM python:3.11-slim

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

# 构建期质量门: 重建语料库 + 全量集成测试 (失败则镜像构建失败)
RUN python -c "from corpus.build_corpus import build_corpus; build_corpus()" \
    && python -m unittest discover -s tests 2>&1 | tail -n 3

EXPOSE 8018

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8018/healthz',timeout=2); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["python", "-m", "workbench_handoff.app"]

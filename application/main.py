"""
FastAPI 应用入口 (API Gateway / Presentation Layer)
挂载 HTTP 接口、管理服务端可信身份注入与请求路由

Week 07: 新增一线任务闭环接口 /api/v1/tasks/action 与 /api/v1/tasks；
鉴权依赖收敛至 application/auth.py (policy-check 语义保持完全不变)。
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status

from application.auth import get_trusted_user_from_session
from application.database import get_billing_task, init_db, list_billing_tasks
from application.models import (
    PolicyCheckRequest,
    PolicyCheckResponse,
    TaskActionRequest,
    TaskResponse,
    TrustedUser,
)
from application.services import process_policy_check
from application.tasks import process_task_action


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时自动初始化 SQLite 关系数据库 DDL 与 Fixtures（幂等）"""
    init_db()
    yield


app = FastAPI(
    title="Northstar Deterministic Policy Check API",
    description="北辰协作 B2B 客服政策核对确定性垂直薄片 (Week 05) + 一线角色任务闭环 (Week 07)",
    version="1.1.0",
    lifespan=lifespan,
)


@app.post(
    "/api/v1/policy-check",
    response_model=PolicyCheckResponse,
    summary="校验客服计费与升级政策",
    status_code=status.HTTP_200_OK,
)
def policy_check_endpoint(
    request: PolicyCheckRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> PolicyCheckResponse:
    """
    接受结构化客服请求，由后端进行 100% 确定性政策匹配，
    返回 answered 结果或结构化 needs_review 异常复核预览。
    """
    return process_policy_check(request=request, user=trusted_user)


@app.post(
    "/api/v1/tasks/action",
    response_model=TaskResponse,
    summary="执行一线任务动作 (check_policy/confirm/refuse/escalate/supervisor_resolve)",
    status_code=status.HTTP_200_OK,
)
def task_action_endpoint(
    request: TaskActionRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> TaskResponse:
    """
    任务状态机端点 (Week 07):
    * 身份只来自服务端可信会话 (X-Auth-Token)，请求体自报角色由 extra="forbid" 直接 422;
    * RBAC 一票否决 -> denied 路径 (candidate_policy 恒为 None, 审计留痕);
    * 动作幂等, 重复提交返回 ALREADY_APPLIED。
    """
    if get_billing_task(request.task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown task_id")
    return process_task_action(request=request, user=trusted_user)


@app.get("/api/v1/tasks", summary="列出工单任务队列 (一线工作台数据源)")
def list_tasks_endpoint(
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
):
    """返回工单队列 (结构化摘要, 不含政策正文与敏感内容)"""
    return {"tasks": list_billing_tasks()}


@app.get("/health", summary="健康检查")
def health_check():
    return {"status": "ok", "app": "deterministic-vertical-slice-v1"}

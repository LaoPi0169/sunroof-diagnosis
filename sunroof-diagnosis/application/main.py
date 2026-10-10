"""
FastAPI 应用入口 (API Gateway / Presentation Layer)
挂载 HTTP 接口、管理服务端可信身份注入与请求路由

天窗总成只读诊断 · 阶段 A 节点 02 确定性垂直薄片 + 节点 04 双角色任务闭环:
只读规格核对 + 任务状态机 (check_spec -> confirm/refuse/escalate -> 主管结案),
零大模型、零外部写副作用 (仅追加 task_runs / task_action_runs 审计行)。
后续节点 (部署回滚/只读 MCP/RAG) 仅追加能力, 不改本接口语义。
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status

from application.auth import get_trusted_user_from_session
from application.database import get_spec_check_task, init_db, list_spec_check_tasks
from application.models import SpecCheckRequest, SpecCheckResponse, TaskActionRequest, TaskResponse, TrustedUser
from application.services import process_spec_check
from application.tasks import process_task_action


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时自动初始化 SQLite 关系数据库 DDL 与 Fixtures（幂等）"""
    init_db()
    yield


app = FastAPI(
    title="Sunroof Read-Only Spec Check API",
    description="天窗总成只读诊断 · 装配规格核对确定性垂直薄片 (节点 02) + 双角色任务闭环 (节点 04)",
    version="1.1.0",
    lifespan=lifespan,
)


@app.post(
    "/api/v1/spec-check",
    response_model=SpecCheckResponse,
    summary="装配规格核对 (确定性: ACL 下推 + 现行有效规格匹配 + 偏差硬限)",
    status_code=status.HTTP_200_OK,
)
def spec_check_endpoint(
    request: SpecCheckRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> SpecCheckResponse:
    """
    接受结构化 PQE 请求，由后端进行 100% 确定性规格匹配:
    * 身份只来自服务端可信会话 (X-Auth-Token)，请求体自报角色由 extra="forbid" 直接 422;
    * ACL (allowed_oem_projects) 在 SQL 层下推，跨项目组的行不进入结果集;
    * 返回 answered (带引用证据) 或结构化 needs_review (零规格内容)。
    """
    return process_spec_check(request=request, user=trusted_user)


@app.post(
    "/api/v1/tasks/action",
    response_model=TaskResponse,
    summary="执行任务动作 (check_spec/confirm/refuse/escalate/supervisor_resolve)",
    status_code=status.HTTP_200_OK,
)
def task_action_endpoint(
    request: TaskActionRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> TaskResponse:
    """
    任务状态机端点 (节点 04 双角色闭环):
    * 身份只来自服务端可信会话 (X-Auth-Token)，请求体自报角色由 extra="forbid" 直接 422;
    * RBAC 一票否决 + 任务级 ACL 交叉校验 -> denied 路径 (candidate_spec 恒为 None, 审计留痕);
    * 动作幂等, 重复提交返回 ALREADY_APPLIED。
    """
    if get_spec_check_task(request.task_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown task_id")
    return process_task_action(request=request, user=trusted_user)


@app.get("/api/v1/tasks", summary="列出任务队列 (双角色工作台数据源, 按用户 ACL 过滤)")
def list_tasks_endpoint(
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
):
    """返回任务队列 (结构化摘要, 不含 SOP 正文与敏感内容; PQE 仅见本项目组任务)"""
    tasks = list_spec_check_tasks()
    if trusted_user.allowed_oem_projects != ["*"]:
        tasks = [t for t in tasks if t["oem_project"] in trusted_user.allowed_oem_projects]
    return {"tasks": tasks}


@app.get("/health", summary="健康检查")
def health_check():
    return {"status": "ok", "app": "sunroof-spec-check-slice-v1"}

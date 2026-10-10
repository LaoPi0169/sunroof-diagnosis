"""
FastAPI 应用入口 (API Gateway / Presentation Layer)
挂载 HTTP 接口、管理服务端可信身份注入与请求路由

天窗总成只读诊断 · 阶段 A 节点 02 确定性垂直薄片:
只读规格核对, 零大模型、零外部写副作用 (仅追加 task_runs 审计行)。
后续节点 (数据入口/任务闭环/部署回滚/只读 MCP/RAG) 仅追加能力, 不改本接口语义。
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, status

from application.auth import get_trusted_user_from_session
from application.database import init_db
from application.models import SpecCheckRequest, SpecCheckResponse, TrustedUser
from application.services import process_spec_check


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时自动初始化 SQLite 关系数据库 DDL 与 Fixtures（幂等）"""
    init_db()
    yield


app = FastAPI(
    title="Sunroof Read-Only Spec Check API",
    description="天窗总成只读诊断 · 装配规格核对确定性垂直薄片 (阶段 A 节点 02)",
    version="1.0.0",
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


@app.get("/health", summary="健康检查")
def health_check():
    return {"status": "ok", "app": "sunroof-spec-check-slice-v1"}

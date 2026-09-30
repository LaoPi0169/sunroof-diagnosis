"""
FastAPI 应用入口 (API Gateway / Presentation Layer)
挂载 HTTP 接口、管理服务端可信身份注入与请求路由
"""
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status

from application.database import init_db
from application.models import PolicyCheckRequest, PolicyCheckResponse, TrustedUser
from application.services import process_policy_check


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时自动初始化 SQLite 关系数据库 DDL 与 Fixtures（幂等）"""
    init_db()
    yield


app = FastAPI(
    title="Northstar Deterministic Policy Check API",
    description="北辰协作 B2B 客服政策核对确定性垂直薄片 (Week 05)",
    version="1.0.0",
    lifespan=lifespan,
)


def get_trusted_user(x_auth_token: Optional[str] = Header(None)) -> TrustedUser:
    """
    服务端可信会话鉴权依赖:
    身份完全从服务端 Header/Session 凭证获得，绝对不信任前端 JSON 请求体自报的角色。
    """
    if not x_auth_token:
        # 默认使用通用客服测试身份（用于模拟正常流程）
        return TrustedUser(user_id="agent_small_zhang", user_role="support_agent")

    # 根据 Header Token 校验角色
    if x_auth_token == "token_supervisor_secret":
        return TrustedUser(user_id="supervisor_wang", user_role="supervisor")
    elif x_auth_token == "token_agent_valid":
        return TrustedUser(user_id="agent_small_zhang", user_role="support_agent")
    else:
        # 未知身份默认拒绝 (denied)：查询不执行、不落库
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


@app.post(
    "/api/v1/policy-check",
    response_model=PolicyCheckResponse,
    summary="校验客服计费与升级政策",
    status_code=status.HTTP_200_OK,
)
def policy_check_endpoint(
    request: PolicyCheckRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user),
) -> PolicyCheckResponse:
    """
    接受结构化客服请求，由后端进行 100% 确定性政策匹配，
    返回 answered 结果或结构化 needs_review 异常复核预览。
    """
    return process_policy_check(request=request, user=trusted_user)


@app.get("/health", summary="健康检查")
def health_check():
    return {"status": "ok", "app": "deterministic-vertical-slice-v1"}

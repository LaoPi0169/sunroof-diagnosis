"""
服务端可信会话鉴权与 RBAC 授权策略 (Week 07)

回链 specs/mission-week-07.md 第 1 天 + role-and-permission-matrix.md:
  * 身份只来自服务端 Header/Session (X-Auth-Token)，绝不信任请求体自报的角色;
  * Default-Deny: 未在授权矩阵中显式列出的 (role, action) 组合一律拒绝;
  * 鉴权函数从 main.py 收敛至此，供策略接口与任务接口共用同一可信会话。
"""
from typing import Optional

from fastapi import Header, HTTPException, status

from application.models import TaskAction, TrustedUser


def get_trusted_user_from_session(x_auth_token: Optional[str] = Header(None)) -> TrustedUser:
    """
    服务端可信会话鉴权依赖 (语义与 Week 05 main.get_trusted_user 完全一致):
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


# RBAC 授权矩阵 (role-and-permission-matrix.md) 的服务端最小实现:
# support_agent 可执行的一线动作; supervisor 显式允许全部动作; 其他角色默认拒绝。
AGENT_ALLOWED_ACTIONS = {
    TaskAction.CHECK_POLICY,
    TaskAction.CONFIRM,
    TaskAction.REFUSE,
    TaskAction.ESCALATE,
}


def authorize_action(user: TrustedUser, action: TaskAction) -> bool:
    """服务端一票否决: 判定 (user_role, action) 是否放行; 默认拒绝 (Default-Deny)"""
    if user.user_role == "supervisor":
        return True
    if user.user_role == "support_agent":
        return action in AGENT_ALLOWED_ACTIONS
    return False

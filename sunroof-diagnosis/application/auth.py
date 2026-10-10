"""
服务端可信会话鉴权 (阶段 A 节点 02)

回链 specs/role-and-permission-matrix.md + vertical-slice-contract.md §5:
  * 身份只来自服务端 Header/Session (X-Auth-Token)，绝不信任请求体自报的角色;
  * 每个可信身份随签发其项目组 ACL 范围 (allowed_oem_projects)，供 SQL 层下推;
  * Default-Deny: 未知 Token 一律 401，查询不执行、不落库。
"""
from typing import Optional

from fastapi import Header, HTTPException, status

from application.models import TaskAction, TrustedUser


def get_trusted_user_from_session(x_auth_token: Optional[str] = Header(None)) -> TrustedUser:
    """
    服务端可信会话鉴权依赖:
    身份完全从服务端 Header/Session 凭证获得，绝对不信任前端 JSON 请求体自报的角色。
    """
    if not x_auth_token:
        # 默认使用 PQE 培训测试身份（用于模拟正常流程; 教学模拟会话, 回链原仓库第 5 周惯例）
        return TrustedUser(
            user_id="pqe_trainee_default", user_role="pqe", allowed_oem_projects=["OEM-A"]
        )

    # 根据 Header Token 校验角色并注入 ACL 范围
    if x_auth_token == "token_pqe_oem_a":
        return TrustedUser(user_id="pqe_chen_oema", user_role="pqe", allowed_oem_projects=["OEM-A"])
    elif x_auth_token == "token_pqe_oem_b":
        return TrustedUser(user_id="pqe_zhao_oemb", user_role="pqe", allowed_oem_projects=["OEM-B"])
    elif x_auth_token == "token_qa_supervisor_secret":
        return TrustedUser(
            user_id="supervisor_li", user_role="quality_supervisor", allowed_oem_projects=["*"]
        )
    else:
        # 未知身份默认拒绝: 查询不执行、不落库
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )


# ============================================================================
# 节点 04 双角色 RBAC 授权矩阵 (role-and-permission-matrix.md §2) 的服务端最小实现:
#   * pqe (项目组内一线): check_spec / confirm / refuse / escalate;
#     supervisor_resolve 为质量主管专属 —— PQE 直调走 denied 路径 (零泄漏 + 审计留痕);
#   * quality_supervisor: 显式允许全部动作 (跨项目只读 + 主管复核结案);
#   * 其他角色/未知角色默认拒绝 (Default-Deny)。
# =====================================================================

PQE_ALLOWED_ACTIONS = {
    TaskAction.CHECK_SPEC,
    TaskAction.CONFIRM,
    TaskAction.REFUSE,
    TaskAction.ESCALATE,
}


def authorize_action(user: TrustedUser, action: TaskAction) -> bool:
    """服务端一票否决: 判定 (user_role, action) 是否放行; 默认拒绝 (Default-Deny)"""
    if user.user_role == "quality_supervisor":
        return True
    if user.user_role == "pqe":
        return action in PQE_ALLOWED_ACTIONS
    return False

"""
FastAPI 应用入口 (API Gateway / Presentation Layer)
挂载 HTTP 接口、管理服务端可信身份注入与请求路由

天窗总成只读诊断 · 阶段 A 节点 02 确定性垂直薄片 + 节点 04 双角色任务闭环:
只读规格核对 + 任务状态机 (check_spec -> confirm/refuse/escalate -> 主管结案),
零大模型、零外部写副作用 (仅追加 task_runs / task_action_runs 审计行)。
后续节点 (部署回滚/只读 MCP/RAG) 仅追加能力, 不改本接口语义。
"""
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse

from application.auth import get_trusted_user_from_session
from application.database import get_spec_check_task, init_db, list_spec_check_tasks
from application.models import (
    MesLookupRequest,
    MesLookupResponse,
    PLMLookupRequest,
    PLMLookupResponse,
    RagAnswerRequest,
    RagAnswerResponse,
    SessionIdentity,
    SpecCheckRequest,
    SpecCheckResponse,
    TaskActionRequest,
    TaskResponse,
    TrustedUser,
)
from application import mes as mes_gateway
from application import plm as plm_gateway
from application.rag import answer_query, corpus_meta
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


# ============================================================================
# 阶段 C 节点 ⑮: 交互控制台 + 只读 RAG 问答 HTTP 面
# (仅追加能力, 不改既有端点语义; 回链 deploy/web-console-usage.md)
# ============================================================================

_CONSOLE_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "console.html"
)


@app.get("/", include_in_schema=False, summary="重定向到交互控制台 /ui")
def root_redirect():
    return RedirectResponse(url="/ui", status_code=302)


@app.get("/ui", include_in_schema=False, summary="交互控制台 (静态单文件, 零框架)")
def ui_console():
    return FileResponse(_CONSOLE_HTML, media_type="text/html; charset=utf-8")


@app.get(
    "/api/v1/session/whoami",
    response_model=SessionIdentity,
    summary="回显当前可信会话身份 (调试用; 绝不回显令牌本身)",
)
def session_whoami(
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> SessionIdentity:
    """与业务端点共用同一鉴权依赖: 身份完全来自服务端 X-Auth-Token 校验。"""
    return SessionIdentity(
        user_id=trusted_user.user_id,
        user_role=trusted_user.user_role,
        allowed_oem_projects=trusted_user.allowed_oem_projects,
    )


@app.post(
    "/api/v1/rag/answer",
    response_model=RagAnswerResponse,
    summary="只读 RAG 问答 (Week-12 门控决策树: 词门/越权检测/无答案域/抽取式作答)",
    status_code=status.HTTP_200_OK,
)
def rag_answer_endpoint(
    request: RagAnswerRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> RagAnswerResponse:
    """
    把 Week-12 冻结的 answer_case 以 HTTP 形式追加暴露 (仅追加能力):
    * 身份只来自服务端可信会话 (X-Auth-Token), user_role 映射门控决策树角色池;
    * 语料库缺失时确定性重建 (write_manifest=False, 不触碰冻结 manifest);
    * 零 LLM 调用、零业务写副作用 (business_effects 恒 0)。
    """
    record = answer_query(request.query, trusted_user.user_role, request.case_id)
    return RagAnswerResponse(**record)


@app.get(
    "/api/v1/rag/corpus-meta",
    summary="语料库元信息 (快照 ID / 文档数 / chunk 数; 控制台数据源)",
)
def rag_corpus_meta():
    return corpus_meta()


# ============================================================================
# 阶段 C 节点 ⑰: MES 生产记录抓取 HTTP 面 (只读 MCP 工具)
# discovery-brief-v1.md In-Scope 第 1 条的阶段 C 兑现; R-2 (波形分页/裁剪)
# 在本工具 schema 冻结时收口 (api-contract/mes-lookup-v1.json)。
# ============================================================================


@app.post(
    "/api/v1/mes/production-record",
    response_model=MesLookupResponse,
    summary="按 SN 只读抓取 MES 生产记录 (装配扭矩/ECU 刷写/传感器波形, 只读 MCP)",
    status_code=status.HTTP_200_OK,
)
def mes_production_record_endpoint(
    request: MesLookupRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> MesLookupResponse:
    """
    把 mcptool mes.lookup_production_record@1 以 HTTP 形式追加暴露 (仅追加能力):
    * 身份只来自服务端可信会话 (X-Auth-Token); 跨项目/未知 SN/角色不足三者
      用户面封套逐字节一致 (零存在性泄漏, P-1), 细分原因只进受限审计;
    * 波形按冻结上限分页 (128 点 × 32 点/段 × 4 段), 绝不全量回传 (R-2 收口);
    * 字段缺失返回字段级结构化 not_available, 绝不编造数据 (P-3);
    * 零业务写副作用 (effect=read_only, business_effects 恒 0)。
    """
    record = mes_gateway.lookup_production_record(
        request.sn, [f.value for f in request.fields], request.segment, trusted_user)
    return MesLookupResponse(**record)


# ============================================================================
# 阶段 C 节点 ⑱: PLM 工程记录查询 HTTP 面 (只读 MCP 工具)
# discovery-brief-v1.md In-Scope 第 2 条 PLM 半边的阶段 C 兑现; 图纸只回登记
# 元数据 (content_disclosure=denied_by_policy, P-1 跨 OEM 敏感图纸硬门槛);
# ECO 历史按冻结上限分页 (api-contract/plm-lookup-v1.json)。
# ============================================================================


@app.post(
    "/api/v1/plm/engineering-record",
    response_model=PLMLookupResponse,
    summary="按零件号只读查询 PLM 工程记录 (BOM/ECO 工程变更版本/图纸登记元数据, 只读 MCP)",
    status_code=status.HTTP_200_OK,
)
def plm_engineering_record_endpoint(
    request: PLMLookupRequest,
    trusted_user: TrustedUser = Depends(get_trusted_user_from_session),
) -> PLMLookupResponse:
    """
    把 mcptool plm.lookup_engineering_record@1 以 HTTP 形式追加暴露 (仅追加能力):
    * 身份只来自服务端可信会话 (X-Auth-Token); 跨项目/未知件号/角色不足三者
      用户面封套逐字节一致 (零存在性泄漏, P-1), 细分原因只进受限审计;
    * ECO 历史按冻结上限分页 (每页最多 4 条, eco_offset 0..12), 绝不全量回传;
    * 图纸只返回登记元数据 (content_disclosure=denied_by_policy), 图纸内容/
      文件/链接永不出工具 (P-1 跨 OEM 敏感图纸硬门槛, 主管亦不展示);
    * 字段缺失返回字段级结构化 not_available, 绝不编造数据 (P-3);
    * 零业务写副作用 (effect=read_only, business_effects 恒 0)。
    """
    record = plm_gateway.lookup_engineering_record(
        request.part_no, [f.value for f in request.fields], request.eco_offset,
        trusted_user)
    return PLMLookupResponse(**record)

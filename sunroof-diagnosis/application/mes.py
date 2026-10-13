"""MES 生产记录抓取网关层（阶段 C 节点 ⑰）—— mcptool 只读工具的 HTTP 适配。

设计要点（与 application/rag.py 同构, 仅追加能力, 不改既有端点语义）:
  * 身份不在这里裁决: tenant/user/role/allowed_projects 全部由端点层从服务端
    可信会话（X-Auth-Token）注入 TrustedUser 后映射为工具可信上下文;
  * 每次调用生成 run_id（审计关联键）; 细分失败原因只进受限审计列表
    （模块内 AUDIT, 追加式; 用户面封套永远只有安全文案 —— 跨项目/未知 SN/
    角色不足三者封套逐字节一致, 零存在性泄漏）;
  * 外部写效果恒 0（工具 effect=read_only, business_effects 恒 0）。
"""
from __future__ import annotations

import uuid

from application.models import TrustedUser
from mcptool import mes_contract as M
from mcptool import mes_server

TENANT = "sunroof-training"

AUDIT = []  # 受限审计（追加式; 细分原因只进这里, 不进用户面响应）


def reset_audit() -> None:
    """测试/演示隔离用: 清空进程内受限审计（不影响任何业务数据）。"""
    AUDIT.clear()


def lookup_production_record(sn: str, fields: list, segment, user: TrustedUser) -> dict:
    """单例抓取: 把可信会话映射为工具上下文并执行只读 MCP 调用。"""
    trusted_context = {
        "tenant": TENANT,
        "user": user.user_id,
        "role": user.user_role,
        "allowed_projects": list(user.allowed_oem_projects),
    }
    arguments = {"sn": sn, "fields": list(fields)}
    if segment is not None:
        arguments["segment"] = segment
    run_id = "mes-" + uuid.uuid4().hex[:12]
    envelope = mes_server.lookup_production_record(
        trusted_context, arguments, audit_sink=AUDIT, run_id=run_id)
    return {
        "tool": M.TOOL_NAME,
        "version": M.TOOL_VERSION,
        "effect": M.EFFECT,
        "run_id": run_id,
        "ok": envelope["ok"],
        "result": envelope.get("result"),
        "error": envelope.get("error"),
        "business_effects": 0,
    }

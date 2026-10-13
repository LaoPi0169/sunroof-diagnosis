"""根因分析报告草稿网关层（阶段 C 节点 ⑳）—— mcptool 只读聚合工具的 HTTP 适配。

设计要点（与 application/mes.py / plm.py / qms.py 同构, 仅追加能力）:
  * 身份不在这里裁决: tenant/user/role/allowed_projects 全部由端点层从服务端
    可信会话（X-Auth-Token）注入 TrustedUser 后映射为工具可信上下文;
  * 每次调用生成 run_id（审计关联键）; 细分失败原因只进受限审计列表
    （模块内 AUDIT, 追加式; 用户面封套永远只有安全文案 —— 三因一致零泄漏）;
  * 外部写效果恒 0（工具 effect=read_only, business_effects 恒 0）;
  * 聚合三源只读 (MES ⑰ + QMS ⑲ + PLM ⑱), 任一源拒绝则整体封套一致拒绝。
"""
from __future__ import annotations

import uuid

from application.models import TrustedUser
from mcptool import report_contract as M
from mcptool import report_server

TENANT = "sunroof-training"

AUDIT = []  # 受限审计（追加式; 细分原因只进这里, 不进用户面响应）


def reset_audit() -> None:
    """测试/演示隔离用: 清空进程内受限审计（不影响任何业务数据）。"""
    AUDIT.clear()


def draft_root_cause(sn: str, part_no: str, process_step: str,
                     user: TrustedUser) -> dict:
    """单例聚合: 把可信会话映射为工具上下文并执行只读报告聚合。"""
    trusted_context = {
        "tenant": TENANT,
        "user": user.user_id,
        "role": user.user_role,
        "allowed_projects": list(user.allowed_oem_projects),
    }
    arguments = {"sn": sn, "part_no": part_no, "process_step": process_step}
    run_id = "rep-" + uuid.uuid4().hex[:12]
    envelope = report_server.draft_root_cause(
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
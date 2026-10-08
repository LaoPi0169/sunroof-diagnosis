"""演示入口 (Week 13): 周宁 (support_agent) 在工单 NS-1042 上调用 case.lookup_context。

用法: python -m mcptool.demo_lookup
请求只含 case_id + fields; 身份来自可信会话 (不经过调用参数); read_at 用真实时钟。
"""
from __future__ import annotations

import json

from . import contract as C
from .client import McpHost, TrustedSession


def main(argv=None) -> None:
    session = TrustedSession(
        "northstar-training", "user-zhou-ning", "support_agent")
    host = McpHost(session)
    request = {"case_id": "NS-1042", "fields": list(C.FIELD_ENUM)}
    result = host.call(request)
    print(json.dumps({
        "demo": "case.lookup_context 只读取回工单上下文",
        "caller": {
            "user": session.user_id,
            "role": session.role,
            "tenant": session.tenant_id,
            "source": "trusted_session (非调用参数)",
        },
        "request": request,
        "result": result,
        "external_write_effects": host.external_write_effects,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

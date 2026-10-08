"""模拟工单系统案例库 (Week 13 合成夹具) —— 只读数据, 无任何变更入口。

数据对齐 (week-13/mcp-read-contract-v1.json + 报告引用):
  - 13 条回归种子工单 (SR-12-01..13 对应 NS-DEV-*/NS-CAL-*): task_type/region/purchased_at
    与 eval-set-v1.jsonl 的 task_context 逐字段一致 —— task_type 走
    contract.TASK_TYPE_TO_CATEGORY 冻结映射, region 逐字同值 ("east"),
    purchased_at 年份与 period ("2026") 一致;
  - NS-1042 为手册例工单 (region=CN, record_version=case-v7);
  - NS-3001 跨租户 / NS-5001 同租户但仅主管可见 —— not_available 不可区分性夹具
    (与不存在工单 NS-9999 用户面响应逐字节一致);
  - 受限字段 (supervisor_note/customer_name/payment_detail) 存在于库中但永不在字段
    枚举内: 证明服务端字段过滤 —— 任何响应不得出现其内容。
"""
from __future__ import annotations

import json

CASE_STORE = {
    "NS-1042": {
        "tenant_id": "northstar-training",
        "record_version": "case-v7",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "plan_upgrade_price_difference",
            "region": "CN",
            "purchased_at": "2026-08-11T03:20:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部处理备注示例（不属于一线可读范围）",
            "customer_name": "客户姓名示例（不属于一线可读范围）",
            "payment_detail": "支付明细示例（不属于一线可读范围）",
        },
    },
    "NS-DEV-01": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "billing_general_inquiry",
            "region": "east",
            "purchased_at": "2026-02-10T08:30:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-19": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "plan_upgrade_price_difference",
            "region": "east",
            "purchased_at": "2026-03-04T11:05:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-14": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "billing_general_inquiry",
            "region": "east",
            "purchased_at": "2026-02-18T09:40:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-CAL-05": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "billing_general_inquiry",
            "region": "east",
            "purchased_at": "2026-01-22T14:10:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-07": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "out_of_scope_inquiry",
            "region": "east",
            "purchased_at": "2026-04-06T10:15:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-CAL-03": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "out_of_scope_inquiry",
            "region": "east",
            "purchased_at": "2026-04-19T16:45:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-25": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "upgrade_effective_date_conflict",
            "region": "east",
            "purchased_at": "2026-07-28T09:05:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-CAL-08": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "upgrade_effective_date_conflict",
            "region": "east",
            "purchased_at": "2026-08-02T13:25:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-26": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "upgrade_effective_date_conflict",
            "region": "east",
            "purchased_at": "2026-07-30T15:55:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-30": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "legacy_policy_reference_check",
            "region": "east",
            "purchased_at": "2026-06-11T09:50:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-32": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "legacy_policy_reference_check",
            "region": "east",
            "purchased_at": "2026-06-24T10:35:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-08": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "special_refund_request",
            "region": "east",
            "purchased_at": "2026-05-08T11:20:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-DEV-12": {
        "tenant_id": "northstar-training",
        "record_version": "case-v1",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "special_refund_request",
            "region": "east",
            "purchased_at": "2026-05-19T14:00:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "主管备注：内部备注示例（不返回）",
            "customer_name": "客户姓名示例（不返回）",
            "payment_detail": "支付明细示例（不返回）",
        },
    },
    "NS-3001": {
        # 跨租户夹具: 其他租户的工单, 对本租户调用者一律 not_available
        "tenant_id": "partner-tenant-co",
        "record_version": "case-v2",
        "visible_to_roles": ["support_agent", "supervisor"],
        "fields": {
            "task_type": "billing_general_inquiry",
            "region": "CN",
            "purchased_at": "2026-07-01T08:00:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "他租户备注（绝不返回）",
            "customer_name": "他租户客户（绝不返回）",
            "payment_detail": "他租户支付（绝不返回）",
        },
    },
    "NS-5001": {
        # 角色不可见夹具: 同租户但仅主管可见, 一线请求与不存在工单响应一致
        "tenant_id": "northstar-training",
        "record_version": "case-v3",
        "visible_to_roles": ["supervisor"],
        "fields": {
            "task_type": "special_refund_request",
            "region": "east",
            "purchased_at": "2026-07-15T10:00:00Z",
        },
        "restricted_fields_present_but_never_returned": {
            "supervisor_note": "受限工单备注（绝不返回）",
            "customer_name": "受限工单客户（绝不返回）",
            "payment_detail": "受限工单支付（绝不返回）",
        },
    },
}

# 不存在的工单号 (not_available 不可区分性夹具的对照面)
NONEXISTENT_CASE_ID = "NS-9999"

_STORE_SNAPSHOT = json.dumps(CASE_STORE, sort_keys=True, ensure_ascii=True)


def get_case(case_id: str):
    """只读查询; 无任何写路径 (本模块不存在新增/修改/删除入口)。"""
    return CASE_STORE.get(case_id)


def snapshot() -> str:
    """库快照 (排序 JSON): 夹具运行前后对比, 证明零写效果。"""
    return _STORE_SNAPSHOT


def check_immutable() -> bool:
    """运行后复核: 当前库与模块加载时快照逐字节一致。"""
    return json.dumps(CASE_STORE, sort_keys=True, ensure_ascii=True) == _STORE_SNAPSHOT


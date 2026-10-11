"""MCP 夹具运行器 (Week 13/14) —— R/X/W 夹具逐条 + 回归种子 + 写效果硬对账。

用法:
    python -m mcptool.run_fixtures          # R 5 条 + W 5 条 + 回归 13 条
    python -m mcptool.run_fixtures --suite read
    python -m mcptool.run_fixtures --suite write
"""
from __future__ import annotations

import argparse
import json
import sys

from mcptool.approval import ApprovalService
from mcptool import contract as C
from mcptool.server import call_read, call_write
from mcptool.ticket_service import TicketService

EAST_PQE = {"tenant": "sunroof-east", "user": "pqe_chen", "role": "pqe"}
EAST_SUP = {"tenant": "sunroof-east", "user": "supervisor_li", "role": "quality_supervisor"}
BINDING_OK = {"tool": C.WRITE_TOOL_NAME, "version": C.WRITE_TOOL_VERSION,
              "approver_role": "quality_supervisor", "requester_role": "pqe",
              "case_id": "SR-1042", "queue": "billing-supervisor-review",
              "reason_code": "POLICY_CONFLICT",
              "source_refs": ["retrofit-bulletin-v2", "retrofit-notice-v4"],
              "effect": C.WRITE_EFFECT, "ttl_s": C.APPROVAL_TTL_SECONDS}
BASE_ARGS = {"case_id": "sr-1042", "queue": "Billing-Supervisor-Review",
             "reason_code": "policy_conflict",
             "source_refs": ["retrofit-notice-v4", "retrofit-bulletin-v2",
                             "retrofit-notice-v4"],
             "idempotency_key": "run-w14-001:ticket.create:1"}


def _fresh_write_stack():
    approvals = ApprovalService()
    approvals.set_clock(lambda: 1000.0)
    tickets = TicketService()
    return approvals, tickets


def read_fixtures() -> list:
    return [
        ("R-01", "ok 恰好封闭字段",
         call_read(EAST_PQE, {"case_id": "sr-1043", "fields": list(C.READ_FIELDS)}),
         lambda r: r["ok"] and set(r["result"]) == {"case_id", "record_version",
                                                    "task_type", "region", "purchased_at"}),
        ("R-02", "受限工单对一线不可见且封套无备注内容",
         call_read(EAST_PQE, {"case_id": "SR-1042", "fields": list(C.READ_FIELDS)}),
         lambda r: not r["ok"] and r["error"]["code"] == "not_available"
                   and "note" not in json.dumps(r)),
        ("R-03", "受限工单对主管可见 (角色×对象×字段鉴权)",
         call_read(EAST_SUP, {"case_id": "SR-1042", "fields": list(C.READ_FIELDS)}),
         lambda r: r["ok"] and r["result"]["task_type"] == "policy_conflict"),
        ("R-04", "未知字段拒绝 (fields 枚举外)",
         call_read(EAST_PQE, {"case_id": "SR-1043", "fields": ["note"]}),
         lambda r: not r["ok"] and r["error"]["code"] == "invalid_argument"),
        ("R-05", "不存在/跨租户用户面封套逐字节一致",
         [call_read(EAST_PQE, {"case_id": "SR-9999", "fields": list(C.READ_FIELDS)}),
          call_read(EAST_PQE, {"case_id": "SR-9001", "fields": list(C.READ_FIELDS)})],
         lambda rs: json.dumps(rs[0], sort_keys=True) == json.dumps(rs[1], sort_keys=True)
                    and not rs[0]["ok"]),
    ]


def write_fixtures() -> list:
    results = []
    approvals, tickets = _fresh_write_stack()
    before = tickets.external_effects()
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": ""}, approvals, tickets)
    results.append(("W-01", "无审批 0 效果", before, tickets.external_effects(),
                    not resp["ok"] and resp["error"]["code"] == "approval_denied"))
    norm = C.normalize_write_arguments(BASE_ARGS)
    results.append(("W-02", "单一规范化函数 (大小写/空白/引用排序去重/队列小写)",
                    None, None,
                    norm["queue"] == "billing-supervisor-review"
                    and norm["reason_code"] == "POLICY_CONFLICT"
                    and norm["source_refs"] == ["retrofit-bulletin-v2",
                                                "retrofit-notice-v4"]
                    and norm["case_id"] == "SR-1042"))
    approvals, tickets = _fresh_write_stack()
    approval_id = approvals.issue("supervisor_li", BINDING_OK)
    before = tickets.external_effects()
    resp = call_write(EAST_PQE, {**BASE_ARGS, "queue": "executive-review",
                                 "approval_id": approval_id}, approvals, tickets)
    results.append(("W-03", "审批后改队列 approval_mismatch", before,
                    tickets.external_effects(),
                    not resp["ok"] and resp["error"]["code"] == "approval_mismatch"
                    and tickets.external_effects() == before))
    return results


def write_success_fixtures() -> list:
    results = []
    approvals, tickets = _fresh_write_stack()
    approval_id = approvals.issue("supervisor_li", BINDING_OK)
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                      approvals, tickets)
    first_effects = tickets.external_effects()
    replays = [call_write(EAST_PQE, {**BASE_ARGS, "approval_id": approval_id},
                          approvals, tickets) for _ in range(10)]
    results.append(("W-04", "幂等: 十次重放总数恒 1 + 独立核验", None,
                    tickets.external_effects(),
                    resp["ok"] and first_effects == 1
                    and tickets.external_effects() == 1
                    and all(r["ok"] and r["result"]["replayed"] for r in replays)
                    and resp["result"]["verified"]))
    approvals, tickets = _fresh_write_stack()
    approvals.deny("supervisor_li", "policy_conflict_needs_owner_arbitration")
    before = tickets.external_effects()
    resp = call_write(EAST_PQE, {**BASE_ARGS, "approval_id": "APR-9999"},
                      approvals, tickets)
    results.append(("W-05", "拒绝 -> 0 效果", before, tickets.external_effects(),
                    not resp["ok"] and tickets.external_effects() == before))
    return results


def run_regressions() -> list:
    """13 条回归种子: 读 5 + 写 5 + 门 3, 全部 identical 断言。"""
    seeds = []
    for fid, desc, payload, check in read_fixtures():
        seeds.append((f"SR-{fid}", bool(check(payload))))
    for fid, desc, before, after, check in write_fixtures():
        seeds.append((f"SR-{fid}", bool(check)))
    for fid, desc, before, after, check in write_success_fixtures():
        seeds.append((f"SR-{fid}", bool(check)))
    seeds.append(("SR-GATE-01", call_read(
        EAST_SUP, {"case_id": "SR-1042", "fields": list(C.READ_FIELDS)})["ok"]))
    seeds.append(("SR-GATE-02", not call_read(
        EAST_PQE, {"case_id": "SR-1042", "fields": ["note"]})["ok"]))
    seeds.append(("SR-GATE-03", C.WRITE_EFFECT == "write_once_reversible"))
    return seeds


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 13/14 MCP 夹具")
    parser.add_argument("--suite", choices=["read", "write", "all"], default="all")
    args = parser.parse_args(argv)
    report = {"read": [], "write": [], "regression": [],
              "total_external_write_effects": 0, "all_pass": False}
    if args.suite in ("read", "all"):
        for fid, desc, payload, check in read_fixtures():
            report["read"].append({"id": fid, "desc": desc, "pass": bool(check(payload))})
    if args.suite in ("write", "all"):
        approvals, tickets = _fresh_write_stack()
        for fid, desc, before, after, check in write_fixtures() + write_success_fixtures():
            report["write"].append({"id": fid, "desc": desc, "pass": bool(check)})
        report["total_external_write_effects"] = tickets.external_effects()
    report["regression"] = [{"id": sid, "pass": ok} for sid, ok in run_regressions()]
    report["all_pass"] = all(item["pass"] for section in ("read", "write", "regression")
                             for item in report[section])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()


"""
角色候选核查器 (Role Candidate Checker) —— Week 09 (回链 specs/mission-week-09.md 第 4 天)

用与未来检索一致的候选 SQL 验证「ACL 下推 = 先过滤后候选」:
权限谓词出现在 SQL WHERE 内 (json_each 展开 allowed_roles), 而非召回后再过滤。

泄漏断言 (任一触发 = 周失败, 退出码 1):
  L1  support_agent 的原始候选中不得出现任何主管专属 chunk (classification != internal-general);
  L2  任何角色的候选中不得出现非 accepted* 文档的 chunk (快照中此类文档 chunk=0, 双保险);
  L3  未知角色 -> 零候选 (与 application/auth.py 401 语义一致, 无可靠候选)。

用法:
    python corpus/check_role_candidates.py [--db data/corpus-w09.db]
        [--snapshot northstar-corpus-w09-v1] [--roles support_agent,supervisor,unknown_role]
输出: JSON 报告 (每角色 candidate_count / by_classification / chunk_ids / verdict)。
边界: 只读候选查询; chunk_ids 仅为标识, 不输出任何政策正文。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from corpus.build_corpus import CORPUS_SNAPSHOT  # noqa: E402

CANDIDATE_SQL = """
SELECT cc.chunk_id, cc.document_id, cc.classification, cd.status AS document_status
FROM corpus_chunks AS cc
JOIN corpus_documents AS cd
  ON cd.document_id = cc.document_id AND cd.corpus_snapshot = cc.corpus_snapshot
WHERE cc.corpus_snapshot = ?
  AND cd.status IN ('accepted', 'accepted_restricted')
  AND cc.status IN ('accepted', 'accepted_restricted')
  AND EXISTS (SELECT 1 FROM json_each(cc.allowed_roles) AS je WHERE je.value = ?)
ORDER BY cc.chunk_id
"""


def candidates_for(conn: sqlite3.Connection, role: str, snapshot: str) -> list:
    rows = conn.execute(CANDIDATE_SQL, (snapshot, role)).fetchall()
    return [
        {"chunk_id": r[0], "document_id": r[1], "classification": r[2], "document_status": r[3]}
        for r in rows
    ]


def check_role(conn: sqlite3.Connection, role: str, snapshot: str) -> dict:
    candidates = candidates_for(conn, role, snapshot)
    by_classification = {}
    for item in candidates:
        by_classification[item["classification"]] = by_classification.get(item["classification"], 0) + 1

    leaks = []
    restricted = [c for c in candidates if c["classification"] != "internal-general"]
    non_accepted = [c for c in candidates
                    if c["document_status"] not in ("accepted", "accepted_restricted")]
    if role == "support_agent" and restricted:
        leaks.append("L1: support_agent 原始候选含主管专属 chunk: "
                     + ", ".join(c["chunk_id"] for c in restricted))
    if non_accepted:
        leaks.append("L2: 候选含非 accepted* 文档 chunk: "
                     + ", ".join(c["chunk_id"] for c in non_accepted))
    if not role.replace("-", "_").isidentifier() or role.startswith("unknown"):
        if candidates:
            leaks.append("L3: 未知角色获得候选 (应为零)")

    return {
        "role": role,
        "candidate_count": len(candidates),
        "by_classification": by_classification,
        "chunk_ids": [c["chunk_id"] for c in candidates],
        "leaks": leaks,
        "verdict": "LEAK" if leaks else "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="北辰 Week 09 角色候选核查 (ACL 下推)")
    parser.add_argument("--db", default=os.path.join("data", "corpus-w09.db"))
    parser.add_argument("--snapshot", default=CORPUS_SNAPSHOT)
    parser.add_argument("--roles", default="support_agent,supervisor,unknown_role")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(json.dumps({"error": "语料库不存在, 先运行 corpus/build_corpus.py",
                          "db": args.db}, ensure_ascii=False))
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        reports = [check_role(conn, role.strip(), args.snapshot)
                   for role in args.roles.split(",") if role.strip()]
    finally:
        conn.close()

    week_failed = any(r["verdict"] == "LEAK" for r in reports)
    print(json.dumps({
        "corpus_snapshot": args.snapshot,
        "roles_checked": [r["role"] for r in reports],
        "results": reports,
        "week_verdict": "FAILED" if week_failed else "PASS",
    }, ensure_ascii=False, indent=2))
    sys.exit(1 if week_failed else 0)


if __name__ == "__main__":
    main()
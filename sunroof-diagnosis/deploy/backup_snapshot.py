"""
备份快照工具 (deploy/backup_snapshot.py) —— 阶段 D 批次 3 生产发布包 (D3-c)

回链: specs/production-hardening-brief-v1.md D3-c + adr-005 (审计哈希链)。
交付「数据卷 + 审计链快照一致性」备份能力:

1. 在线一致快照: SQLite backup API (src.backup(dst)) —— 不锁库、不撕裂 WAL,
   备份的是某一时刻的完整一致视图 (生产容器可边跑边备, 但运维口径建议停写窗口);
2. 快照后立即校验 (哈希链快照一致性门):
   - PRAGMA integrity_check (库完整性);
   - verify_audit_chain() 三重校验 (逐行重算哈希 + 前向链接 + 链头一致) ——
     任何校验失败 => 删除刚产出的快照文件, 退出码 1, 绝不产出「看似好实则坏」的备份;
3. 元数据 JSON (<backup>.meta.json): backup_id / created_at_utc / db_sha256 /
   audit_chain 结果 / 表清单 —— 恢复脚本用 db_sha256 做逐字节比对;
4. --verify-only 模式: 对既有备份文件重跑校验 (runbook v2 运维哈希链校验步骤)。

用法:
    python deploy/backup_snapshot.py --db data/sunroof.db --out-dir data/backups --label nightly
    python deploy/backup_snapshot.py --verify-only --backup-file data/backups/xxx.db

退出码: 0=成功; 1=校验失败 (备份已回滚删除) 或 --verify-only 校验不过; 2=输入不存在。
输出为 JSON 摘要 (零秘密: 库路径与哈希, 不含凭据/令牌/规格正文)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from application.database import verify_audit_chain  # noqa: E402


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_integrity(backup_path: str) -> dict:
    """对快照文件做校验: 库完整性 + 审计哈希链三重校验。

    用普通连接而非 mode=ro URI —— backup API 产出的快照继承源库 WAL 模式,
    只读 URI 打开 WAL 库需要 -shm 可写, 存在环境差异坑; 校验动作本身零写操作。
    """
    conn = sqlite3.connect(backup_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        chain = verify_audit_chain(conn)
    finally:
        conn.close()
    return {"integrity_check": integrity, "audit_chain": chain, "tables": tables}


def verify_backup_file(backup_path: str, meta_path: str | None = None) -> tuple:
    """校验既有备份文件 (verify-only / 恢复前共用): 校验 + 可选元数据逐字节比对。"""
    result = {
        "backup_file": backup_path,
        "db_sha256": sha256_file(backup_path),
        **snapshot_integrity(backup_path),
    }
    ok = result["integrity_check"] == "ok" and result["audit_chain"]["ok"]
    if meta_path and os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        result["meta_file"] = meta_path
        result["meta_sha256_match"] = (meta.get("db_sha256") == result["db_sha256"])
        ok = ok and result["meta_sha256_match"]
    result["ok"] = ok
    return ok, result


def main() -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 数据卷备份快照 (含审计哈希链一致性门)")
    parser.add_argument("--db", help="源库路径 (如 data/sunroof.db 或容器内 /app/data/sunroof.db)")
    parser.add_argument("--out-dir", help="备份输出目录 (建议数据卷内, 如 data/backups)")
    parser.add_argument("--label", default="snapshot", help="备份标签 (默认 snapshot)")
    parser.add_argument("--verify-only", action="store_true", help="只校验既有备份, 不新做快照")
    parser.add_argument("--backup-file", help="--verify-only 模式的备份文件路径")
    args = parser.parse_args()

    if args.verify_only:
        if not args.backup_file or not os.path.exists(args.backup_file):
            print(json.dumps({"error": "backup_file_missing", "path": args.backup_file},
                             ensure_ascii=False))
            sys.exit(2)
        meta = args.backup_file + ".meta.json"
        ok, result = verify_backup_file(args.backup_file, meta)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if ok else 1)

    if not args.db or not os.path.exists(args.db):
        print(json.dumps({"error": "source_db_missing", "path": args.db}, ensure_ascii=False))
        sys.exit(2)
    if not args.out_dir:
        print(json.dumps({"error": "out_dir_required"}, ensure_ascii=False))
        sys.exit(2)
    os.makedirs(args.out_dir, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(args.out_dir, f"{args.label}-{stamp}.db")
    meta_path = backup_path + ".meta.json"

    # 1) 在线一致快照 (SQLite backup API: WAL 安全, 不撕裂)
    src = sqlite3.connect(args.db)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # 2) 快照后立即校验 (哈希链快照一致性门): 任一不过 => 回滚删除, 绝不产出坏备份
    ok, result = verify_backup_file(backup_path)
    meta = {
        "backup_id": f"{args.label}-{stamp}",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_db": os.path.abspath(args.db),
        "backup_file": os.path.abspath(backup_path),
        "db_sha256": result["db_sha256"],
        "integrity_check": result["integrity_check"],
        "audit_chain": result["audit_chain"],
        "tables": result["tables"],
        "tool": "deploy/backup_snapshot.py (release-manifest-v2 backup_tool)",
    }
    if not ok:
        for p in (backup_path, meta_path):
            if os.path.exists(p):
                os.remove(p)
        print(json.dumps({"error": "snapshot_verification_failed", **result},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({**meta, "ok": True}, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
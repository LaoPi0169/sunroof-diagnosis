"""
恢复快照工具 (deploy/restore_snapshot.py) —— 阶段 D 批次 3 生产发布包 (D3-c)

回链: specs/production-hardening-brief-v1.md D3-c + ROLLBACK.md (回滚手册)。
fail-fast 恢复: 校验不过 => 目标库零触碰。

1. 恢复前校验 (全部通过才动手):
   - 备份文件 PRAGMA integrity_check + verify_audit_chain() 三重校验 (只读打开);
   - 元数据 db_sha256 与备份文件实算逐字节一致 (防「备份被静默改过」);
2. 目标库已存在时先做 pre-restore 快照 (<db>.pre-restore-<ts>.db, 自愈保险;
   --skip-pre-backup 可跳过);
3. 原子替换: copy 备份 -> <db>.restore-tmp -> os.replace; 同时清除目标库旧
   -wal/-shm 残留 (WAL 残留与恢复后的库不匹配, 会污染校验);
4. 恢复后复验 (integrity_check + 哈希链) 并输出 JSON 摘要。

前置条件 (runbook v2 §4): 恢复前必须停止应用进程 (容器: docker compose stop);
SQLite 库文件不允许并发写者期间被替换。

用法:
    python deploy/restore_snapshot.py --backup-file data/backups/xxx.db --db data/sunroof.db

退出码: 0=成功; 1=校验失败 (目标零触碰); 2=输入不存在。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from deploy.backup_snapshot import sha256_file, verify_backup_file  # noqa: E402


def _remove_sidecars(db_path: str) -> list:
    """清除 SQLite WAL 侧车文件 (-wal/-shm): 恢复后旧侧车必须不存在。"""
    removed = []
    for suffix in ("-wal", "-shm"):
        side = db_path + suffix
        if os.path.exists(side):
            os.remove(side)
            removed.append(os.path.basename(side))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 备份恢复 (fail-fast + 原子替换)")
    parser.add_argument("--backup-file", required=True, help="备份文件路径")
    parser.add_argument("--db", required=True, help="目标库路径 (恢复前必须停应用)")
    parser.add_argument("--meta", help="元数据 JSON 路径 (缺省 <backup>.meta.json)")
    parser.add_argument("--skip-pre-backup", action="store_true",
                        help="跳过恢复前对目标库的安全快照 (不推荐)")
    args = parser.parse_args()

    if not os.path.exists(args.backup_file):
        print(json.dumps({"error": "backup_file_missing", "path": args.backup_file},
                         ensure_ascii=False))
        sys.exit(2)

    meta_path = args.meta or (args.backup_file + ".meta.json")
    if not os.path.exists(meta_path):
        meta_path = None  # 元数据可选: 无则只做库级校验, 不做逐字节比对

    # 1) 恢复前校验 (fail-fast): 校验不过 => 目标库零触碰
    ok, result = verify_backup_file(args.backup_file, meta_path)
    if not ok:
        print(json.dumps({"error": "restore_rejected_precheck_failed", **result},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    # 2) 目标库安全快照 (自愈保险)
    pre_backup = None
    had_target = os.path.exists(args.db)
    if had_target and not args.skip_pre_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pre_backup = f"{args.db}.pre-restore-{stamp}.db"
        src = sqlite3.connect(args.db)
        dst = sqlite3.connect(pre_backup)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    # 3) 原子替换 + 侧车清理
    tmp = args.db + ".restore-tmp"
    shutil.copyfile(args.backup_file, tmp)
    os.replace(tmp, args.db)
    removed = _remove_sidecars(args.db)

    # 4) 恢复后复验
    ok, recheck = verify_backup_file(args.db, meta_path)
    summary = {
        "restored_from": os.path.abspath(args.backup_file),
        "target_db": os.path.abspath(args.db),
        "db_sha256": recheck["db_sha256"],
        "integrity_check": recheck["integrity_check"],
        "audit_chain": recheck["audit_chain"],
        "pre_restore_safety_snapshot": os.path.abspath(pre_backup) if pre_backup else None,
        "removed_wal_sidecars": removed,
        "ok": ok,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
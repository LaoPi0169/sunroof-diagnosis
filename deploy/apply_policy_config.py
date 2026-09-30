"""
发布配置收敛器 (Policy Config Applier) —— Week 08 部署工具 (回链 specs/mission-week-08.md 第 1/4 天)

职责:
1. 把 config/policy-config.json 的 policy_baseline 断言幂等收敛到已迁移的 SQLite 库 (仅 UPDATE,
   不建表、不删库、不改动 Week 05/06/07 冻结 DDL —— 回滚时数据库兼容性由此保证);
2. 应用 policy_overrides 增量 (仅回滚演练的 rc1-bad 配置使用);
3. 输出 JSON 收敛报告, 每条策略断言逐项核对 (converged), 不打印政策标题/公式等敏感正文。

幂等性: 重复执行同一配置, 数据库终态不变 (第二次执行仅重复同样的 UPDATE)。
用法:
    python deploy/apply_policy_config.py --config config/policy-config.json --db <db_path>
退出码: 0=全部收敛; 1=存在未收敛项或错误 (fail fast); 2=配置文件不存在。
"""
import argparse
import json
import os
import sqlite3
import sys

# 收敛器允许触碰的字段白名单 (绝不触碰主键/版本/正文/权限列)
MUTABLE_FIELDS = ("is_deprecated", "effective_at", "expires_at")
REQUIRED_COLUMNS = {"policy_id", "is_deprecated", "effective_at", "expires_at"}


def apply_config(config_path: str, db_path: str) -> dict:
    """加载配置并幂等收敛数据库, 返回 JSON 可序列化的收敛报告。"""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    report = {
        "config_version": config.get("config_version"),
        "release_id": config.get("release_id"),
        "db_path": os.path.abspath(db_path),
        "applied": [],
        "errors": [],
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(policies)")}
        if not REQUIRED_COLUMNS.issubset(existing):
            report["errors"].append(
                "policies 表缺少必需列, 请先执行数据库迁移 (python -c \"from application.database import init_db; init_db()\")"
            )
            return report

        # 先收敛基线断言, 再应用增量覆盖 (known-good 配置 overrides 为空)
        for section in ("policy_baseline", "policy_overrides"):
            for entry in config.get(section) or []:
                pid = entry.get("policy_id")
                sets, params = [], []
                for field in MUTABLE_FIELDS:
                    if field in entry:
                        sets.append(f"{field} = ?")
                        params.append(entry[field])
                if not sets:
                    report["errors"].append(f"{section}: {pid} 不含可应用字段, 跳过")
                    continue
                params.append(pid)
                cursor = conn.execute(
                    f"UPDATE policies SET {', '.join(sets)} WHERE policy_id = ?", params
                )
                # 逐项核对收敛结果 (fail fast: 目标策略不存在也算未收敛)
                row = conn.execute(
                    "SELECT is_deprecated, effective_at, expires_at FROM policies WHERE policy_id = ?",
                    (pid,),
                ).fetchone()
                converged = row is not None and all(
                    field not in entry or row[field] == entry[field] for field in MUTABLE_FIELDS
                )
                report["applied"].append(
                    {
                        "section": section,
                        "policy_id": pid,
                        "rows_matched": cursor.rowcount,
                        "converged": converged,
                    }
                )
        conn.commit()
    finally:
        conn.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="应用发布配置到北辰数据库 (幂等 UPDATE 收敛, 零删库零建表)"
    )
    parser.add_argument("--config", required=True, help="发布配置 JSON 路径")
    parser.add_argument("--db", required=True, help="目标 SQLite 数据库路径")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(json.dumps({"error": f"config file not found: {args.config}"}))
        sys.exit(2)
    if not os.path.exists(args.db):
        print(json.dumps({"error": f"database not found: {args.db}"}))
        sys.exit(2)

    report = apply_config(args.config, args.db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = bool(report["errors"]) or any(not item["converged"] for item in report["applied"])
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

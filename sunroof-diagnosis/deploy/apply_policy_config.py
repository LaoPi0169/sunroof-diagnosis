"""
发布配置收敛器 (Policy Config Applier) —— 节点 05 部署工具
(回链 ../fde-roadmap-v1.md §2 节点 ⑤ + specs/mission-week-08.md 第 1/4 天)

职责:
1. 把 config/sunroof-config-w08.json 的 config_baseline 断言幂等收敛到已迁移的
   SQLite 库 (仅 UPSERT policy_config 表, 不建表、不删库、不改动节点 02-04 冻结
   DDL —— 回滚时数据库兼容性由此保证);
2. 应用 config_overrides 增量 (仅回滚演练的 rc1-bad 配置使用);
3. 输出 JSON 收敛报告, 每条断言逐项核对 (converged); 收敛器不判对错 —— 对错靠
   评审 + 任务级冒烟双防线, 收敛器只做「白名单 + 幂等 + 逐项核对」。

幂等性: 重复执行同一配置, 业务终态不变 (config_key -> config_value 投影一致)。
用法 (在本目录下):
    python deploy/apply_policy_config.py --config config/sunroof-config-w08.json --db <db_path>
退出码: 0=全部收敛; 1=存在未收敛项或错误 (fail fast); 2=配置文件或数据库不存在。
"""
import argparse
import json
import os
import sqlite3
import sys

# 收敛器允许触碰的白名单键 (绝不触碰 specs/task_runs/spec_check_tasks 等业务表;
# 非白名单键一律拒绝 —— Default-Deny, 不自动扩大可写范围)
WHITELIST_KEYS = (
    "spec_check.hard_limit_deviation_pct",
    "release.config_version",
)
REQUIRED_TABLE = "policy_config"


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
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if REQUIRED_TABLE not in tables:
            report["errors"].append(
                "policy_config 表不存在, 请先执行数据库迁移 "
                '(python -c "from application.database import init_db; init_db()")'
            )
            return report

        # 先收敛基线断言, 再应用增量覆盖 (known-good 配置 overrides 为空)
        for section in ("config_baseline", "config_overrides"):
            for entry in config.get(section) or []:
                key = entry.get("config_key")
                value = entry.get("config_value")
                if key not in WHITELIST_KEYS:
                    report["errors"].append(
                        f"{section}: 配置键 '{key}' 不在白名单, 拒绝收敛 (Default-Deny)"
                    )
                    continue
                if value is None:
                    report["errors"].append(f"{section}: {key} 缺少 config_value")
                    continue
                conn.execute(
                    """
                    INSERT INTO policy_config (config_key, config_value, updated_by)
                    VALUES (?, ?, 'deploy_converger')
                    ON CONFLICT(config_key) DO UPDATE SET
                        config_value = excluded.config_value,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        updated_by = 'deploy_converger'
                    """,
                    (key, str(value)),
                )
                # 逐项核对收敛结果 (fail fast)
                row = conn.execute(
                    "SELECT config_value FROM policy_config WHERE config_key = ?", (key,)
                ).fetchone()
                converged = row is not None and row["config_value"] == str(value)
                report["applied"].append(
                    {"section": section, "config_key": key, "converged": converged}
                )
        conn.commit()
    finally:
        conn.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="应用发布配置到天窗诊断数据库 (幂等 UPSERT 收敛, 零删库零建表)"
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

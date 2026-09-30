"""
日志脱敏扫描 (Redaction Scan) —— Week 08 CI 阶段 (回链 specs/mission-week-08.md 验收标准「敏感信息不入日志」)

规则:
1. 运行时输出语句 (print/logging/logger/sys.stdout.write) 中绝不允许出现:
   凭据明文 (token_agent_valid / token_supervisor_secret)、请求头凭据名 (X-Auth-Token)、
   政策正文标记 (政策标题/公式字段名)。
2. 凭据常量只允许出现在授权文件中:
   - application/auth.py (服务端定义与校验)
   - workbench_cli.py / deploy/smoke_w08.py (客户端用本人 Token 发起请求 —— 教学模拟会话)
   其余任何扫描范围内文件出现凭据明文即违规。

输出: JSON 报告 {scanned_files, emissions_checked, violations, passed}; 退出码 0=通过, 1=存在违规。
"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扫描范围 (运行时代码, 不含 tests/ 与 docs)
SCOPE_FILES = [
    os.path.join("application", "auth.py"),
    os.path.join("application", "database.py"),
    os.path.join("application", "main.py"),
    os.path.join("application", "models.py"),
    os.path.join("application", "services.py"),
    os.path.join("application", "tasks.py"),
    "workbench_cli.py",
    os.path.join("deploy", "apply_policy_config.py"),
    os.path.join("deploy", "smoke_w08.py"),
    os.path.join("ci", "redaction_scan.py"),
    os.path.join("ci", "run_local_ci.py"),
]

# 运行时输出中绝对禁止的内容 (凭据/凭据头/政策正文标记)
FORBIDDEN_IN_EMISSION = [
    "token_agent_valid",
    "token_supervisor_secret",
    "X-Auth-Token",
    "formula_rule",
    "2026 华东套餐差价计费政策",
    "2025 华东套餐差价计费政策",
    "2026 特殊退款政策",
]

# 凭据常量允许出现的文件 (定义或作为本人凭证发起请求)
SECRET_LITERAL_ALLOWED_FILES = {
    os.path.join("application", "auth.py"),
    "workbench_cli.py",
    os.path.join("deploy", "smoke_w08.py"),
}
SECRET_LITERALS = ["token_agent_valid", "token_supervisor_secret"]

EMISSION_RE = re.compile(r"print\s*\(|logging\.\w+|logger\.\w+|sys\.stdout\.write")


def scan() -> dict:
    violations = []
    emissions_checked = 0
    for rel_path in SCOPE_FILES:
        abs_path = os.path.join(PROJECT_ROOT, rel_path)
        if not os.path.exists(abs_path):
            violations.append({"file": rel_path, "line": 0, "rule": "missing_file",
                               "detail": "扫描范围内文件缺失"})
            continue
        with open(abs_path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue  # 跳过纯注释/文档串行 (规则只约束运行时行为)
                # 规则 1: 运行时输出语句不得包含凭据/正文
                if EMISSION_RE.search(line):
                    emissions_checked += 1
                    for token in FORBIDDEN_IN_EMISSION:
                        if token in line:
                            violations.append({
                                "file": rel_path, "line": lineno, "rule": "secret_or_body_in_emission",
                                "detail": f"输出语句包含敏感标记: {token[:4]}***",
                            })
                # 规则 2: 凭据常量不得出现在授权文件之外
                if rel_path not in SECRET_LITERAL_ALLOWED_FILES:
                    for token in SECRET_LITERALS:
                        if token in line:
                            violations.append({
                                "file": rel_path, "line": lineno, "rule": "secret_literal_outside_allowlist",
                                "detail": f"凭据常量出现在非授权文件: {token[:4]}***",
                            })
    return {
        "scanned_files": len(SCOPE_FILES),
        "emissions_checked": emissions_checked,
        "violations": violations,
        "passed": not violations,
    }


def main() -> None:
    report = scan()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

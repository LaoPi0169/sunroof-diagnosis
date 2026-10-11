"""
日志脱敏扫描 (Redaction Scan) —— 节点 05 CI 阶段
(回链 specs/mission-week-08.md 验收标准「敏感信息不入日志」)

规则:
1. 凭据绝不出现在任何运行时输出语句 (print/logging/logger/sys.stdout.write):
   token_pqe_oem_a / token_pqe_oem_b / token_qa_supervisor_secret / X-Auth-Token, 全范围禁止;
2. 规格正文标记 (规格标题/遗留公式规则字段) 绝不出现在服务端与工具链的运行时输出:
   application/* / deploy/* / ci/* 全部禁止; workbench_cli.py 豁免 —— CLI 是客户端
   工作台, 把操作者本人已授权响应中的 candidate_spec 展示给本人属「授权展示」而非
   日志泄漏 (一线 SOP 人工核验 UX, 服务端 never 主动记录正文; 审计表仅存 ID,
   由动态测试复验);
3. 凭据常量只允许出现在授权文件中:
   - application/auth.py (服务端定义与校验)
   - workbench_cli.py / deploy/smoke_w08.py (客户端用本人 Token 发起请求 —— 教学模拟会话)
   - ci/redaction_scan.py (扫描器自身持有字面量清单才能检测违规)
   其余任何扫描范围内文件出现凭据明文即违规。

扫描范围 (运行时代码, 节点 06 起含语料两文件): 不含 tests/ 与 verify_node03/verify_node04
—— 验证脚本与测试同属证据工具面 (其正文断言是刻意的安全验证内容, 非运行时日志)。

输出: JSON 报告 {scanned_files, emissions_checked, violations, passed}; 退出码 0=通过, 1=存在违规。
"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扫描范围 (运行时代码) —— 节点 06 (Week 09): 13 文件 (应用 6 + CLI 1 + 部署 2 + CI 2
#                + 语料 2 + 本扫描器)
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
    os.path.join("corpus", "build_corpus.py"),
    os.path.join("corpus", "check_role_candidates.py"),
]

# 运行时输出中绝对禁止的内容 —— 凭据类 (全范围)
SECRET_MARKERS_IN_EMISSION = [
    "token_pqe_oem_a",
    "token_pqe_oem_b",
    "token_qa_supervisor_secret",
    "X-Auth-Token",
]

# 运行时输出中绝对禁止的内容 —— 规格正文类 (仅服务端与工具链; 客户端授权展示见文件头规则 2)
BODY_MARKERS_IN_EMISSION = [
    "天窗电机固定螺栓拧紧扭矩检验标准",
    "遮阳帘导轨装配间隙检验标准",
    "隔热板装配间隙检验标准",
    "legacy_rule_v4",
]
BODY_MARKER_EXEMPT_FILES = {"workbench_cli.py"}

# 凭据常量允许出现的文件 (定义或作为本人凭证发起请求)
# 注: ci/redaction_scan.py 在列 —— 扫描器自身必须持有字面量清单才能检测违规
SECRET_LITERAL_ALLOWED_FILES = {
    os.path.join("application", "auth.py"),
    "workbench_cli.py",
    os.path.join("deploy", "smoke_w08.py"),
    os.path.join("ci", "redaction_scan.py"),
}
SECRET_LITERALS = [
    "token_pqe_oem_a",
    "token_pqe_oem_b",
    "token_qa_supervisor_secret",
]

EMISSION_RE = re.compile(r"print\s*\(|logging\.\w+|logger\.\w+|sys\.stdout\.write")


def scan(scope_files=None) -> dict:
    """执行脱敏扫描; scope_files 供测试注入临时文件 (默认扫描 SCOPE_FILES)。"""
    files = list(scope_files) if scope_files is not None else SCOPE_FILES
    violations = []
    emissions_checked = 0
    for rel_path in files:
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
                # 规则 1+2: 运行时输出语句不得包含凭据 (全范围) / 规格正文 (服务端与工具链)
                if EMISSION_RE.search(line):
                    emissions_checked += 1
                    for token in SECRET_MARKERS_IN_EMISSION:
                        if token in line:
                            violations.append({
                                "file": rel_path, "line": lineno, "rule": "secret_in_emission",
                                "detail": f"输出语句包含凭据标记: {token[:6]}***",
                            })
                    if rel_path not in BODY_MARKER_EXEMPT_FILES:
                        for token in BODY_MARKERS_IN_EMISSION:
                            if token in line:
                                violations.append({
                                    "file": rel_path, "line": lineno, "rule": "spec_body_in_emission",
                                    "detail": f"服务端/工具链输出语句包含规格正文标记: {token[:6]}***",
                                })
                # 规则 3: 凭据常量不得出现在授权文件之外
                if rel_path not in SECRET_LITERAL_ALLOWED_FILES:
                    for token in SECRET_LITERALS:
                        if token in line:
                            violations.append({
                                "file": rel_path, "line": lineno, "rule": "secret_literal_outside_allowlist",
                                "detail": f"凭据常量出现在非授权文件: {token[:6]}***",
                            })
    return {
        "scanned_files": len(files),
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

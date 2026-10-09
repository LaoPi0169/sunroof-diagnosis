"""MCP 能力清单模块 (Week 15) —— 冻结的工具描述/参数模式摘要 + served 目录比对隔离。

A-02/A-05 的机制化 (回链 week-15/mcp-capability-manifest-v2.json):
  - 工具描述与参数 schema 也是不可信边界: served 目录里的描述/模式与冻结清单
    canonical-json 摘要逐字节不一致时, 该能力立即隔离 (capability_quarantined),
    未冻结的版本/字段 (如未经评审的 ticket.create@2 + notify_customer) 不得自动启用;
  - 比对是摘要等值, 不做「像不像命令」的语义判断 —— 文本永远不能创建身份、
    权限或审批, 任何「已授权」字符串都不能生成审批记录;
  - 清单文件本身字节级 sha256 互锁 (MANIFEST_SHA256): 文件被改动 -> 装载即失败
    (fail closed), 不存在「改了清单再让 served 对齐」的绕过;
  - cancel_ticket (运营补偿) 不在本清单: 不进模型目录 (A-07)。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_MODULE_DIR)
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "week-15", "mcp-capability-manifest-v2.json")

# 合同冻结互锁 (week-15/mcp-capability-manifest-v2.json 字节级 sha256)
MANIFEST_SHA256 = "136efe013cd5919d4f51af5df5bcf3770be4b33218d7b7a49731eabcb3a8a918"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(obj) -> str:
    """canonical JSON: sort_keys + 紧凑分隔符 (摘要比对的基础, 双方必须同构)。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def descriptor_digest(descriptor: dict) -> str:
    """descriptor = {tool, version, effect, description, arguments_schema} 的摘要。"""
    return sha256_bytes(canonical_json(descriptor).encode("utf-8"))


def load_manifest(path=None, *, verify_sha: bool = True) -> dict:
    """装载冻结清单; verify_sha=True 时文件字节与 MANIFEST_SHA256 不符即失败。"""
    manifest_path = path or MANIFEST_PATH
    with open(manifest_path, "rb") as f:
        raw = f.read()
    if verify_sha and sha256_bytes(raw) != MANIFEST_SHA256:
        raise ValueError(
            "capability manifest 与冻结摘要不一致 (fail closed): " + manifest_path)
    return json.loads(raw.decode("utf-8"))


class ServedCatalog:
    """served 目录: Host 所见的工具描述/参数模式 (夹具可篡改以模拟 A-02/A-05)。

    缺省 served == expected (来自冻结清单的深拷贝); 只有显式 register()
    才会造成偏差 —— 与真实世界「Server 目录可能未经评审地变化」同构。
    """

    def __init__(self, expected: dict):
        self._expected = expected               # tool -> descriptor (冻结事实)
        self._served = copy.deepcopy(expected)  # served 目录 (可被夹具篡改)

    @classmethod
    def from_manifest(cls, path=None, *, verify_sha: bool = True) -> "ServedCatalog":
        manifest = load_manifest(path, verify_sha=verify_sha)
        expected = {}
        for capability in manifest["capabilities"]:
            expected[capability["tool"]] = {
                "tool": capability["tool"],
                "version": capability["version"],
                "effect": capability["effect"],
                "description": capability["description"],
                "arguments_schema": capability["arguments_schema"],
            }
        return cls(expected)

    def expected_descriptor(self, tool: str):
        expected = self._expected.get(tool)
        return copy.deepcopy(expected) if expected is not None else None

    def register(self, tool: str, descriptor: dict) -> None:
        """夹具/工作台注入 rogue 描述 (模拟未经评审的 Server 目录变化)。"""
        self._served[tool] = descriptor

    def restore(self, tool: str) -> None:
        """恢复为冻结清单缺省 (教学演示的人工解冻; 真实系统须评审流程)。"""
        self._served[tool] = copy.deepcopy(self._expected[tool])

    def verify(self, tool: str):
        """served 与冻结摘要等值比对; 返回 (ok, reason_typed)。"""
        served = self._served.get(tool)
        if served is None:
            return False, "served_descriptor_missing"
        expected = self._expected.get(tool)
        if expected is None:
            return False, "tool_not_in_frozen_manifest"
        if descriptor_digest(served) != descriptor_digest(expected):
            return False, "descriptor_digest_mismatch"
        return True, "ok"

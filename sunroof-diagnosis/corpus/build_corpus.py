"""
语料构建器 (Corpus Builder) —— Week 09 (回链 specs/mission-week-09.md 第 3 天)

确定性语料摄取: 读入 corpus/sources/ 的 8 份天窗售后知识来源, 按知识所有者合同
(CORPUS_CONTRACTS, 与 knowledge-owner-acl-matrix-v1.md 一一对应) 完成:

  1. 五态路由 (与合同字段绑定, 与文件时间戳无关):
     - 必填字段缺失 (所有者/ACL/源版本/生效期) -> quarantined (默认隔离, 宁可不回答)
     - 本文件谱系键出现在其他文件的 supersedes 里   -> expired (谱系取代)
     - 同一冲突组存在第二份现行来源            -> conflict_review (保留在快照供周 10-12 评审)
     - 其余按分类: internal-restricted -> accepted_restricted; 否则 accepted
  2. 确定性切块 (fixed-heading-chunker-v1): 仅对 accepted* 文档按 '## ' 标题切段,
     首个标题前的导言为第 0 块; 标题或正文出现「仅限质量主管」标记 -> 该段取最严格权限
     (混合权限段不降权, 对应任务书混合权限规则; 标题+正文双扫描)。
  3. 产物: 目标库 corpus_documents / corpus_chunks 两表 (按快照幂等重建) +
     corpus-manifest-v1.json (全条目可由源文件 sha256 复算, 不含墙钟时间,
     两轮构建逐字节一致 -> 确定性证据)。

边界: 零模型调用、不检索、不生成答案; 只负责「来源-版本-权限」可核对的语料层。
冻结面零改动: 底座复用 application.database.init_db() (迁移 001-004), 语料两表由
本模块叠加执行 corpus/corpus_schema.sql, 不进入 MIGRATION_SCRIPTS 冻结清单。
用法:
    python corpus/build_corpus.py [--db data/corpus-w09.db] [--manifest corpus-manifest-v1.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from application.database import init_db  # noqa: E402  (复用冻结基线 001-004 作底座)

CORPUS_SNAPSHOT = "sunroof-corpus-w09-v1"
BATCH_ID = "corpus-batch-w09-v1"
PARSER_ID = "markdown-section-parser-v1"
CHUNKER_ID = "fixed-heading-chunker-v1"
CHUNKER_PARAMS = {"heading_level": 2, "acl_inheritance": "chunk-strictest"}
SOURCES_DIR = os.path.join(PROJECT_ROOT, "corpus", "sources")
CORPUS_SCHEMA_PATH = os.path.join(PROJECT_ROOT, "corpus", "corpus_schema.sql")
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "corpus-manifest-v1.json")
SUPERVISOR_ONLY_MARKER = "仅限质量主管"
ROUTING_STATES = ("accepted", "accepted_restricted", "expired", "conflict_review", "quarantined")
REQUIRED_CONTRACT_FIELDS = ("source_owner", "source_version", "effective_at", "allowed_roles")

# 知识所有者合同 —— 与 knowledge-owner-acl-matrix-v1.md §3 的 8 份来源合同逐字段一致
# 角色口径: 手册 role-and-permission-matrix 的 pqe (一线) / quality_supervisor (质量主管)
CORPUS_CONTRACTS = [
    {
        "document_id": "service-general-v1",
        "file": "service-general-v1.md",
        "title": "售后服务总则 v1（已废止）",
        "lineage_key": "service-general",
        "source_version": "v1.0",
        "source_owner": "售后工程组·孙敏",
        "effective_at": "2025-01-01",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["pqe", "quality_supervisor"],
    },
    {
        "document_id": "service-general-v3",
        "file": "service-general-v3.md",
        "title": "售后服务总则 v3（对工单解释基准）",
        "lineage_key": "service-general",
        "source_version": "v3.2",
        "source_owner": "售后工程组·孙敏",
        "effective_at": "2026-01-01",
        "expires_at": None,
        "supersedes": ["service-general"],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["pqe", "quality_supervisor"],
    },
    {
        "document_id": "torque-east-v5",
        "file": "torque-east-v5.md",
        "title": "东区扭矩复紧细则 v5",
        "lineage_key": "torque-east",
        "source_version": "v5.0",
        "source_owner": "扭矩标准组·周毅",
        "effective_at": "2026-03-01",
        "expires_at": None,
        "supersedes": ["torque-east"],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["pqe", "quality_supervisor"],
    },
    {
        "document_id": "claim-supervisor-v2",
        "file": "claim-supervisor-v2.md",
        "title": "索赔执行细则 v2（质量主管专属）",
        "lineage_key": "claim-supervisor",
        "source_version": "v2.0",
        "source_owner": "索赔流程组·吴桐",
        "effective_at": "2026-02-01",
        "expires_at": None,
        "supersedes": ["claim-supervisor"],
        "conflict_group": None,
        "classification": "internal-restricted",
        "allowed_roles": ["quality_supervisor"],
    },
    {
        "document_id": "retrofit-bulletin-v2",
        "file": "retrofit-bulletin-v2.md",
        "title": "OEM-A 改装通告 v2（2026-04 批次天窗复位）",
        "lineage_key": "retrofit-bulletin",
        "source_version": "v2.0",
        "source_owner": "改装工程组·韩磊",
        "effective_at": "2026-04-10",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": "retrofit-oem-a-2026-04",
        "classification": "internal-general",
        "allowed_roles": ["pqe", "quality_supervisor"],
    },
    {
        "document_id": "retrofit-notice-v4",
        "file": "retrofit-notice-v4.md",
        "title": "OEM-A 改装公告 v4（2026-04 批次天窗复位）",
        "lineage_key": "retrofit-notice",
        "source_version": "v4.0",
        "source_owner": "改装工程组·韩磊",
        "effective_at": "2026-04-15",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": "retrofit-oem-a-2026-04",
        "classification": "internal-general",
        "allowed_roles": ["pqe", "quality_supervisor"],
    },
    # —— 隔离件: 无合同元数据 (document_id/file/title 之外全空) ——
    {
        "document_id": "quick-note",
        "file": "quick-note.txt",
        "title": "口头记录（无合同元数据）",
        "lineage_key": None,
        "source_version": None,
        "source_owner": None,
        "effective_at": None,
        "expires_at": None,
        "supersedes": [],
        "conflict_group": None,
        "classification": None,
        "allowed_roles": [],
    },
    {
        "document_id": "special-claim-copy",
        "file": "special-claim-copy.md",
        "title": "特殊索赔政策（复制件）",
        "lineage_key": None,
        "source_version": None,
        "source_owner": None,
        "effective_at": None,
        "expires_at": None,
        "supersedes": [],
        "conflict_group": None,
        "classification": None,
        "allowed_roles": [],
    },
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_sources() -> dict:
    """按合同顺序读入来源文件, 计算内容摘要 (来源完整性绑定, 与文件时间戳无关)。"""
    sources = {}
    for contract in CORPUS_CONTRACTS:
        path = os.path.join(SOURCES_DIR, contract["file"])
        with open(path, "rb") as f:
            data = f.read()
        sources[contract["file"]] = {
            "rel_path": os.path.join("corpus", "sources", contract["file"]),
            "sha256": _sha256(data),
            "bytes": len(data),
            "text": data.decode("utf-8"),
        }
    return sources


def route_documents(contracts: list, sources: dict) -> list:
    """五态路由 (quarantine-by-default): 顺序 = 合同顺序, 判定只依赖合同字段与谱系。

    优先级: 缺要件 -> quarantined; 被谱系取代 -> expired; 冲突组第二份现行来源 ->
    conflict_review; 其余按分类 accepted / accepted_restricted。
    """
    superseding = {}  # lineage_key -> 声明取代该谱系的 document_id 列表 (排除自身后判定 expired)
    for c in contracts:
        for key in c["supersedes"]:
            superseding.setdefault(key, []).append(c["document_id"])
    conflict_group_sizes = {}
    for c in contracts:
        if c["conflict_group"] and all(c.get(f) for f in REQUIRED_CONTRACT_FIELDS):
            conflict_group_sizes[c["conflict_group"]] = conflict_group_sizes.get(c["conflict_group"], 0) + 1

    documents = []
    for c in contracts:
        missing = [f for f in REQUIRED_CONTRACT_FIELDS if not c.get(f)]
        superseded_by = [d for d in superseding.get(c["lineage_key"] or "", []) if d != c["document_id"]]
        if missing:
            status = "quarantined"
            reason = "缺治理要件, 默认隔离 (quarantine-by-default): " + ", ".join(missing)
        elif c["lineage_key"] and superseded_by:
            status = "expired"
            reason = (f"谱系键 {c['lineage_key']} 被其他来源取代 ({', '.join(superseded_by)}), "
                      "按 supersedes 谱系判定过期, 与文件时间戳无关")
        elif c["conflict_group"] and conflict_group_sizes.get(c["conflict_group"], 0) >= 2:
            status = "conflict_review"
            reason = (f"冲突组 {c['conflict_group']} 存在第二份现行来源; "
                      "冻结在候选池外, 待知识所有者裁决")
        elif c["classification"] == "internal-restricted":
            status = "accepted_restricted"
            reason = "要件齐备且为主管专属分类 (仅 quality_supervisor 可见)"
        else:
            status = "accepted"
            reason = "要件齐备且为普通分类 (pqe 与 quality_supervisor 可见)"
        documents.append({
            "document_id": c["document_id"],
            "file_path": sources[c["file"]]["rel_path"],
            "title": c["title"],
            "lineage_key": c["lineage_key"],
            "source_version": c["source_version"],
            "source_owner": c["source_owner"],
            "effective_at": c["effective_at"],
            "expires_at": c["expires_at"],
            "supersedes": list(c["supersedes"]),
            "conflict_group": c["conflict_group"],
            "classification": c["classification"],
            "allowed_roles": list(c["allowed_roles"]),
            "status": status,
            "missing_fields": missing,
            "route_reason": reason,
            "content_sha256": sources[c["file"]]["sha256"],
            "content_bytes": sources[c["file"]]["bytes"],
        })
    return documents


def chunk_document(contract: dict, source: dict, status: str) -> list:
    """fixed-heading-chunker-v1: 仅 accepted* 文档切块; chunk 原子继承文档身份与 ACL。

    混合权限段规则: 段标题或正文出现「仅限质量主管」标记 -> 该段取最严格权限
    (internal-restricted + 仅 quality_supervisor), 绝不降权。标题与正文双扫描
    (任务书工程记录第 3 条的教训: 只扫正文会漏掉标记位于段标题的混合权限段)。
    """
    if status not in ("accepted", "accepted_restricted"):
        return []
    lines = source["text"].splitlines()
    heading_idx = [i for i, line in enumerate(lines) if line.startswith("## ")]
    sections = []
    intro = "\n".join(lines[:heading_idx[0]]).strip() if heading_idx else source["text"].strip()
    if intro:
        sections.append({"heading": "(导言)", "body": intro})
    for pos, start in enumerate(heading_idx):
        end = heading_idx[pos + 1] if pos + 1 < len(heading_idx) else len(lines)
        heading = lines[start][3:].strip()
        body = "\n".join(lines[start + 1:end]).strip()
        sections.append({"heading": heading, "body": f"{heading}\n\n{body}".strip()})

    chunks = []
    for seq, section in enumerate(sections):
        marked = SUPERVISOR_ONLY_MARKER in section["heading"] or SUPERVISOR_ONLY_MARKER in section["body"]
        classification = "internal-restricted" if marked else contract["classification"]
        allowed_roles = ["quality_supervisor"] if marked else list(contract["allowed_roles"])
        body_text = section["body"]
        chunks.append({
            "chunk_id": f"{contract['document_id']}#s{seq:02d}",
            "document_id": contract["document_id"],
            "seq": seq,
            "heading": section["heading"],
            "classification": classification,
            "allowed_roles": allowed_roles,
            "status": status,
            "text_sha256": _sha256(body_text.encode("utf-8")),
            "body_chars": len(body_text),
            "body_text": body_text,  # 仅落库; 清单不携带正文 (sha256 绑定内容完整性)
        })
    return chunks


def build_tables(db_path: str) -> tuple:
    """确定性构建语料两表: init_db() 冻结底座 (001-004) + 语料 schema 叠加, 按快照幂等重建。

    返回 (documents, chunks) 清单条目; chunks 条目不含 body_text (正文仅落库)。
    """
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with open(CORPUS_SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute("DELETE FROM corpus_chunks WHERE corpus_snapshot = ?", (CORPUS_SNAPSHOT,))
        conn.execute("DELETE FROM corpus_documents WHERE corpus_snapshot = ?", (CORPUS_SNAPSHOT,))

        sources = read_sources()
        routed = route_documents(CORPUS_CONTRACTS, sources)
        documents, chunks = [], []
        for contract, doc in zip(CORPUS_CONTRACTS, routed):
            doc_chunks = chunk_document(contract, sources[contract["file"]], doc["status"])
            doc["chunk_count"] = len(doc_chunks)
            doc["chunk_ids"] = [c["chunk_id"] for c in doc_chunks]
            conn.execute(
                "INSERT INTO corpus_documents (document_id, corpus_snapshot, batch_id, file_path,"
                " title, lineage_key, source_version, source_owner, effective_at, expires_at,"
                " supersedes, conflict_group, classification, allowed_roles, status,"
                " missing_fields, route_reason, content_sha256, content_bytes, chunk_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc["document_id"], CORPUS_SNAPSHOT, BATCH_ID, doc["file_path"], doc["title"],
                    doc["lineage_key"], doc["source_version"], doc["source_owner"],
                    doc["effective_at"], doc["expires_at"], json.dumps(doc["supersedes"]),
                    doc["conflict_group"], doc["classification"], json.dumps(doc["allowed_roles"]),
                    doc["status"], json.dumps(doc["missing_fields"]), doc["route_reason"],
                    doc["content_sha256"], doc["content_bytes"], doc["chunk_count"],
                ),
            )
            for chunk in doc_chunks:
                conn.execute(
                    "INSERT INTO corpus_chunks (chunk_id, document_id, corpus_snapshot, batch_id,"
                    " seq, heading, body_text, text_sha256, classification, allowed_roles, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk["chunk_id"], chunk["document_id"], CORPUS_SNAPSHOT, BATCH_ID,
                        chunk["seq"], chunk["heading"], chunk["body_text"], chunk["text_sha256"],
                        chunk["classification"], json.dumps(chunk["allowed_roles"]), chunk["status"],
                    ),
                )
            documents.append(doc)
            chunks.extend({k: v for k, v in c.items() if k != "body_text"} for c in doc_chunks)
        conn.commit()
    finally:
        conn.close()
    return documents, chunks


def assemble_manifest(documents: list, chunks: list) -> dict:
    """装配确定性清单: 不含墙钟时间, 全部条目可由源文件 sha256 复算。"""
    by_status = {state: 0 for state in ROUTING_STATES}
    for doc in documents:
        by_status[doc["status"]] += 1
    by_classification = {}
    for chunk in chunks:
        by_classification[chunk["classification"]] = by_classification.get(chunk["classification"], 0) + 1

    return {
        "corpus_snapshot": CORPUS_SNAPSHOT,
        "batch_id": BATCH_ID,
        "parser": {"parser_id": PARSER_ID, "chunker_id": CHUNKER_ID,
                   "chunker_params": CHUNKER_PARAMS},
        "routing_states": list(ROUTING_STATES),
        "acl_model": {
            "pushdown": "filter-then-candidate: 权限谓词下推到 SQL WHERE, 先过滤后候选",
            "mixed_chunk_rule": ("混合权限段取最严格权限 (段标题或正文含「仅限质量主管」"
                                 "标记 -> 仅 quality_supervisor), 不降权; 标题+正文双扫描"),
            "leak_policy": "任何主管专属 chunk 出现在 pqe 原始候选集 = 本周失败",
        },
        "counts": {
            "documents_total": len(documents),
            "chunks_total": len(chunks),
            "by_status": by_status,
            "chunks_by_classification": by_classification,
        },
        "documents": documents,
        "chunks": chunks,
        "determinism": {
            "no_wall_clock": "清单不含摄取墙钟时间, 血缘以 batch_id 为锚",
            "rebuild_check": "两次构建 (不同临时库) 的 documents/chunks 全字段逐条一致",
        },
    }


def build_corpus(db_path: str, manifest_path: str = MANIFEST_PATH,
                 write_manifest: bool = True) -> dict:
    """确定性构建入口: 语料两表 + 清单落盘, 返回清单 dict。"""
    documents, chunks = build_tables(db_path)
    manifest = assemble_manifest(documents, chunks)
    if write_manifest:
        os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="天窗诊断 Week 09 确定性语料构建器")
    parser.add_argument("--db", default=os.path.join("data", "corpus-w09.db"))
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--no-manifest", action="store_true")
    args = parser.parse_args()

    manifest = build_corpus(args.db, args.manifest, write_manifest=not args.no_manifest)
    summary = {
        "db": args.db,
        "corpus_snapshot": manifest["corpus_snapshot"],
        "documents_total": manifest["counts"]["documents_total"],
        "chunks_total": manifest["counts"]["chunks_total"],
        "by_status": manifest["counts"]["by_status"],
        "chunks_by_classification": manifest["counts"]["chunks_by_classification"],
        "manifest": None if args.no_manifest else args.manifest,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()



"""
语料构建器 (Corpus Builder) —— Week 09 (回链 specs/mission-week-09.md 第 3 天)

确定性语料摄取: 读入 corpus/sources/ 的 8 份知识来源, 按知识所有者合同
(CORPUS_CONTRACTS, 与 knowledge-owner-acl-matrix-v1.md 一一对应) 完成:

  1. 五态路由 (与合同字段绑定, 与文件时间戳无关):
     - 必填字段缺失 (所有者/ACL/源版本/生效期) -> quarantined (默认隔离, 宁可不回答)
     - 本文件谱系键出现在其他文件的 supersedes 里   -> expired (谱系取代)
     - 同一冲突组存在第二份现行来源            -> conflict_review (保留在快照供周 10-12 评审)
     - 其余按分类: internal-restricted -> accepted_restricted; 否则 accepted
  2. 确定性切块 (fixed-heading-chunker-v1): 仅对 accepted* 文档按 '## ' 标题切段,
     首个标题前的导言为第 0 块; 段内出现「仅限主管」标记 -> 该段取最严格权限
     (混合权限段不降权, 对应任务书混合权限规则)。
  3. 产物: 目标库 corpus_documents / corpus_chunks 两表 (按快照幂等重建) +
     corpus-manifest-v1.json (全条目可由源文件 sha256 复算, 不含墙钟时间,
     两轮构建逐字节一致 -> 确定性证据)。

边界: 零模型调用、不检索、不生成答案; 只负责「来源-版本-权限」可核对的语料层。
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

from application.database import init_db  # noqa: E402  (复用冻结基线 001-003 作底座)

CORPUS_SNAPSHOT = "northstar-corpus-w09-v1"
BATCH_ID = "corpus-batch-w09-v1"
PARSER_ID = "markdown-section-parser-v1"
CHUNKER_ID = "fixed-heading-chunker-v1"
CHUNKER_PARAMS = {"heading_level": 2, "acl_inheritance": "chunk-strictest"}
SOURCES_DIR = os.path.join(PROJECT_ROOT, "corpus", "sources")
CORPUS_SCHEMA_PATH = os.path.join(PROJECT_ROOT, "corpus", "corpus_schema.sql")
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "corpus-manifest-v1.json")
SUPERVISOR_ONLY_MARKER = "仅限主管"

# 知识所有者合同 —— 与 knowledge-owner-acl-matrix-v1.md §3 的 8 份来源合同逐字段一致
CORPUS_CONTRACTS = [
    {
        "document_id": "billing-general-v1",
        "file": "billing-general-v1.md",
        "title": "通用计费口径 v1（已废止）",
        "lineage_key": "billing-general",
        "source_version": "v1.0",
        "source_owner": "费率组·赵倩",
        "effective_at": "2025-01-01",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["support_agent", "supervisor"],
    },
    {
        "document_id": "billing-general-v3",
        "file": "billing-general-v3.md",
        "title": "通用计费口径 v3（对客解释基准）",
        "lineage_key": "billing-general",
        "source_version": "v3.2",
        "source_owner": "费率组·赵倩",
        "effective_at": "2026-01-01",
        "expires_at": None,
        "supersedes": ["billing-general"],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["support_agent", "supervisor"],
    },
    {
        "document_id": "price-difference-east-v5",
        "file": "price-difference-east-v5.md",
        "title": "华东套餐差价细则 v5",
        "lineage_key": "price-difference-east",
        "source_version": "v5.0",
        "source_owner": "费率组·赵倩",
        "effective_at": "2026-03-01",
        "expires_at": None,
        "supersedes": ["price-difference-east"],
        "conflict_group": None,
        "classification": "internal-general",
        "allowed_roles": ["support_agent", "supervisor"],
    },
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
        "document_id": "refund-supervisor-v2",
        "file": "refund-supervisor-v2.md",
        "title": "退款执行细则 v2（主管专属）",
        "lineage_key": "refund-supervisor",
        "source_version": "v2.1",
        "source_owner": "退款组·孙敏",
        "effective_at": "2026-02-01",
        "expires_at": None,
        "supersedes": ["refund-supervisor"],
        "conflict_group": None,
        "classification": "internal-restricted",
        "allowed_roles": ["supervisor"],
    },
    {
        "document_id": "special-refund-copy",
        "file": "special-refund-copy.md",
        "title": "特殊退款政策（复制件，无合同元数据）",
        "lineage_key": None,
        "source_version": None,
        "source_owner": None,
        "effective_at": None,
        "expires_at": None,
        "supersedes": [],
        "conflict_group": None,
        "classification": "internal-restricted",
        "allowed_roles": [],
    },
    {
        "document_id": "upgrade-east-v2",
        "file": "upgrade-east-v2.md",
        "title": "华东套餐升级补偿口径 v2",
        "lineage_key": "upgrade-east",
        "source_version": "v2.0",
        "source_owner": "渠道组·周锐",
        "effective_at": "2026-04-01",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": "upgrade-east-2026-04",
        "classification": "internal-general",
        "allowed_roles": ["support_agent", "supervisor"],
    },
    {
        "document_id": "upgrade-notice-v4",
        "file": "upgrade-notice-v4.md",
        "title": "华东套餐升级通知 v4",
        "lineage_key": "upgrade-notice",
        "source_version": "v4.0",
        "source_owner": "渠道组·周锐",
        "effective_at": "2026-04-15",
        "expires_at": None,
        "supersedes": [],
        "conflict_group": "upgrade-east-2026-04",
        "classification": "internal-general",
        "allowed_roles": ["support_agent", "supervisor"],
    },
]

# 路由必填字段 (缺失任一 -> 默认隔离 quarantined): 所有者 / ACL / 源版本 / 生效期
REQUIRED_FIELDS = ("source_owner", "allowed_roles", "source_version", "effective_at")


def read_source(file_name: str) -> dict:
    """读入来源文件, 返回文本 + 内容摘要 + 字节数 (来源完整性绑定)。"""
    path = os.path.join(SOURCES_DIR, file_name)
    with open(path, "rb") as f:
        raw = f.read()
    return {
        "text": raw.decode("utf-8"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def route_document(contract: dict, contracts: list) -> tuple:
    """五态路由 (确定性, 与文件时间戳无关)。返回 (status, missing_fields, route_reason)。"""
    missing = [field for field in REQUIRED_FIELDS if not contract.get(field)]
    if missing:
        return (
            "quarantined",
            missing,
            "必填合同字段缺失 -> 默认隔离 (宁可不回答, 不冒答)",
        )

    superseded_by = sorted(
        other["document_id"]
        for other in contracts
        if other["document_id"] != contract["document_id"]
        and contract["lineage_key"] in other.get("supersedes", [])
    )
    if superseded_by:
        return (
            "expired",
            [],
            "谱系取代: " + ", ".join(superseded_by) + " 的 supersedes 包含本文件谱系键",
        )

    if contract.get("conflict_group"):
        peers = sorted(
            other["document_id"]
            for other in contracts
            if other["document_id"] != contract["document_id"]
            and other.get("conflict_group") == contract["conflict_group"]
            and other.get("source_owner")  # 仅现行来源参与冲突; 隔离件不构成冲突方
        )
        if peers:
            return (
                "conflict_review",
                [],
                "冲突组 "
                + contract["conflict_group"]
                + " 存在并行现行来源: "
                + ", ".join(peers),
            )

    if contract["classification"] == "internal-restricted":
        return "accepted_restricted", [], "合同字段齐备; 受限分类 (主管专属)"
    return "accepted", [], "合同字段齐备; 谱系现行; 无并行冲突来源"


def split_sections(text: str) -> list:
    """markdown-section-parser-v1 + fixed-heading-chunker-v1 (确定性切块):
    '# ' 行为文档题名 (不入正文), '## ' 行切段; 首个 '## ' 之前的非空导言为第 0 块。
    返回 [(heading, normalized_body), ...], 丢弃无正文的段。"""
    lines = text.replace("\r\n", "\n").split("\n")
    sections, heading, body, seen_h2 = [], "(导言)", [], False
    for line in lines:
        if line.startswith("## "):
            sections.append((heading, body))
            heading, body, seen_h2 = line[3:].strip(), [], True
        elif not seen_h2 and line.startswith("# "):
            continue  # 文档题名行不计入导言正文
        else:
            body.append(line)
    sections.append((heading, body))

    normalized = []
    for sec_heading, sec_body in sections:
        body_text = "\n".join(l.rstrip() for l in sec_body).strip()
        if body_text:
            normalized.append((sec_heading, body_text))
    return normalized


def chunk_acl(contract: dict, section_text: str) -> tuple:
    """段级 ACL (混合权限规则): 段标题或正文含「仅限主管」标记 -> 取最严格权限
    (supervisor 专属), 不允许混合段降权为普通权限; 否则继承文档级分类与 ACL。"""
    if SUPERVISOR_ONLY_MARKER in section_text:
        return "internal-restricted", ["supervisor"]
    return contract["classification"], list(contract["allowed_roles"])


_INSERT_DOC = """
INSERT INTO corpus_documents (
    document_id, corpus_snapshot, batch_id, file_path, title, lineage_key,
    source_version, source_owner, effective_at, expires_at, supersedes,
    conflict_group, classification, allowed_roles, status, missing_fields,
    route_reason, content_sha256, content_bytes, chunk_count
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_INSERT_CHUNK = """
INSERT INTO corpus_chunks (
    chunk_id, document_id, corpus_snapshot, batch_id, seq, heading,
    body_text, text_sha256, classification, allowed_roles, status
) VALUES (?,?,?,?,?,?,?,?,?,?,?)
"""


def build_tables(db_path: str) -> tuple:
    """构建语料两表: 在冻结底座 (001-003) 之上按快照幂等重建。返回 (documents, chunks)。"""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with open(CORPUS_SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute("DELETE FROM corpus_chunks WHERE corpus_snapshot = ?", (CORPUS_SNAPSHOT,))
        conn.execute("DELETE FROM corpus_documents WHERE corpus_snapshot = ?", (CORPUS_SNAPSHOT,))

        documents, chunks = [], []
        for contract in sorted(CORPUS_CONTRACTS, key=lambda c: c["document_id"]):
            source = read_source(contract["file"])
            status, missing_fields, reason = route_document(contract, CORPUS_CONTRACTS)
            chunkables = (
                split_sections(source["text"])
                if status in ("accepted", "accepted_restricted") else []
            )

            conn.execute(_INSERT_DOC, (
                contract["document_id"], CORPUS_SNAPSHOT, BATCH_ID,
                "corpus/sources/" + contract["file"], contract["title"],
                contract["lineage_key"], contract["source_version"],
                contract["source_owner"], contract["effective_at"],
                contract["expires_at"], json.dumps(contract["supersedes"], ensure_ascii=False),
                contract["conflict_group"], contract["classification"],
                json.dumps(contract["allowed_roles"], ensure_ascii=False),
                status, json.dumps(missing_fields, ensure_ascii=False),
                reason, source["sha256"], source["bytes"], len(chunkables),
            ))

            chunk_ids = []
            for seq, (sec_heading, sec_body) in enumerate(chunkables):
                # 段级 ACL 检查范围 = 标题 + 正文 (「仅限主管」标记可出现在任一处)
                classification, allowed_roles = chunk_acl(contract, sec_heading + "\n" + sec_body)
                title_line = (
                    "# " + contract["title"] if sec_heading == "(导言)"
                    else "## " + sec_heading
                )
                chunk_text = title_line + "\n" + sec_body
                chunk_id = contract["document_id"] + "#s%02d" % seq
                text_digest = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                conn.execute(_INSERT_CHUNK, (
                    chunk_id, contract["document_id"], CORPUS_SNAPSHOT, BATCH_ID,
                    seq, sec_heading, chunk_text, text_digest,
                    classification, json.dumps(allowed_roles, ensure_ascii=False), status,
                ))
                chunk_ids.append(chunk_id)
                chunks.append({
                    "chunk_id": chunk_id,
                    "document_id": contract["document_id"],
                    "seq": seq,
                    "heading": sec_heading,
                    "chars": len(chunk_text),
                    "text_sha256": text_digest,
                    "classification": classification,
                    "allowed_roles": allowed_roles,
                    "status": status,
                })

            documents.append({
                "document_id": contract["document_id"],
                "file": "corpus/sources/" + contract["file"],
                "title": contract["title"],
                "lineage_key": contract["lineage_key"],
                "source_version": contract["source_version"],
                "source_owner": contract["source_owner"],
                "effective_at": contract["effective_at"],
                "expires_at": contract["expires_at"],
                "supersedes": contract["supersedes"],
                "conflict_group": contract["conflict_group"],
                "classification": contract["classification"],
                "allowed_roles": contract["allowed_roles"],
                "status": status,
                "missing_fields": missing_fields,
                "route_reason": reason,
                "content_sha256": source["sha256"],
                "content_bytes": source["bytes"],
                "chunk_count": len(chunkables),
                "chunk_ids": chunk_ids,
            })
        conn.commit()
    finally:
        conn.close()
    return documents, chunks


def assemble_manifest(documents: list, chunks: list) -> dict:
    """装配确定性清单: 不含墙钟时间, 全部条目可由源文件 sha256 复算。"""
    by_status = {state: 0 for state in
                 ("accepted", "accepted_restricted", "expired", "conflict_review", "quarantined")}
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
        "routing_states": ["accepted", "accepted_restricted", "expired",
                           "conflict_review", "quarantined"],
        "acl_model": {
            "pushdown": "filter-then-candidate: 权限谓词下推到 SQL WHERE, 先过滤后候选",
            "mixed_chunk_rule": "混合权限段取最严格权限 (段含主管标记 -> supervisor 专属), 不降权",
            "leak_policy": "任何 supervisor 专属 chunk 出现在 support_agent 原始候选集 = 本周失败",
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
    parser = argparse.ArgumentParser(description="北辰 Week 09 确定性语料构建器")
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
-- ============================================================================
-- 语料治理表 (Week 09) —— 回链 specs/mission-week-09.md 第 3 天
-- 设计要点:
--   1. 独立 schema: 由 corpus/build_corpus.py 挂接到 init_db() 冻结底座之后执行,
--      不进入 application.database.MIGRATION_SCRIPTS 冻结清单 (release-manifest
--      schema_version=004 与 sunroof-w08-r0 冻结基线绑定, 不因语料层而改变);
--   2. 血缘列沿用节点 03 风格: source_version / source_owner / batch_id;
--   3. 权限列 allowed_roles 存 JSON 数组, 候选查询用 json_each 下推到 WHERE
--      (filter-then-candidate, 先过滤后候选);
--   4. 五态路由: accepted / accepted_restricted / expired / conflict_review /
--      quarantined; 仅 accepted* 文档产出 chunk。
-- ============================================================================
CREATE TABLE IF NOT EXISTS corpus_documents (
    document_id    TEXT PRIMARY KEY,              -- 业务键: 来源ID-版本 (service-general-v3)
    corpus_snapshot TEXT NOT NULL,                -- 语料快照 ID (sunroof-corpus-w09-v1)
    batch_id       TEXT NOT NULL,                 -- 摄取批次 (血缘/对账锚点)
    file_path      TEXT NOT NULL,                 -- 来源文件相对路径 (corpus/sources/...)
    title          TEXT NOT NULL,
    lineage_key    TEXT,                          -- 版本谱系业务键 (service-general); 隔离件可为 NULL
    source_version TEXT,                          -- 上游源版本 (缺失即隔离)
    source_owner   TEXT,                          -- 知识所有者 (缺失即隔离)
    effective_at   TEXT,                          -- 生效日 (YYYY-MM-DD)
    expires_at     TEXT,                          -- 失效日 (可为空)
    supersedes     TEXT NOT NULL DEFAULT '[]',    -- JSON: 本版本取代的谱系键列表
    conflict_group TEXT,                          -- 冲突组 (同组两份现行文件 -> conflict_review)
    classification TEXT,                          -- internal-general | internal-restricted
    allowed_roles  TEXT NOT NULL DEFAULT '[]',    -- JSON: 文档级可读角色 (pqe / quality_supervisor)
    status         TEXT NOT NULL,                 -- 五态路由结果
    missing_fields TEXT NOT NULL DEFAULT '[]',    -- JSON: 隔离原因 (缺失必填字段清单)
    route_reason   TEXT NOT NULL DEFAULT '',      -- 路由依据 (人类可读, 可复核)
    content_sha256 TEXT NOT NULL,                 -- 源文件内容摘要 (来源完整性绑定)
    content_bytes  INTEGER NOT NULL,
    chunk_count    INTEGER NOT NULL DEFAULT 0     -- 非 accepted* 文档恒为 0
);

CREATE TABLE IF NOT EXISTS corpus_chunks (
    chunk_id        TEXT PRIMARY KEY,             -- {document_id}#s{seq:02d}
    document_id     TEXT NOT NULL REFERENCES corpus_documents(document_id),
    corpus_snapshot TEXT NOT NULL,
    batch_id        TEXT NOT NULL,
    seq             INTEGER NOT NULL,             -- 0 = 导言块, 其后按标题顺序
    heading         TEXT NOT NULL,                -- 段标题 (导言块为 "(导言)")
    body_text       TEXT NOT NULL,                -- 段落全文 (标题行+正文)
    text_sha256     TEXT NOT NULL,                -- 段落内容摘要 (两轮构建逐字节一致性的核对键)
    classification  TEXT NOT NULL,                -- 段级分类 (混合权限段取最严格)
    allowed_roles   TEXT NOT NULL,                -- JSON: 段级可读角色 (chunk 继承文档 ACL 并按标记收紧)
    status          TEXT NOT NULL,                -- 继承文档 accepted / accepted_restricted
    UNIQUE (document_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_corpus_documents_snapshot ON corpus_documents (corpus_snapshot, status);
CREATE INDEX IF NOT EXISTS idx_corpus_chunks_snapshot    ON corpus_chunks (corpus_snapshot, status);

-- M11 长期记忆（docs/26 §2/§4；fact/preference 统一表，kind 区分）。
-- 迁移序号 006：承接 001_enable_pgvector.sql（vector 扩展已在 001 创建，此处不重复）。
-- vector 维度必须与 src/atlas/memory/embeddings.py EMBED_DIM=256 严格一致；改维度须另立迁移。
-- 对齐 002 约定：created_at 用 TEXT 存 Python 端生成的 UTC ISO-8601 字符串（两档形状一致、便于对拍），
-- JSONB 列 scope/meta，行内 tenant_id 语句级过滤（不引入 RLS），id 取 storage_id_seq → mem-N。

CREATE TABLE IF NOT EXISTS memory_items (
    id          TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('fact', 'preference')),
    content     TEXT NOT NULL,
    scope       JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding   vector(256) NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    source      TEXT NOT NULL DEFAULT 'tool',
    meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_tenant_kind
    ON memory_items (tenant_id, kind);

-- 余弦 ANN 索引（沙盘小数据量规划器可能顺序扫描、结果等价）。
-- 若目标 pgvector 版本对空表建 ivfflat 报错，集成实测时降级为暂不建 ANN（精确余弦已够沙盘）。
CREATE INDEX IF NOT EXISTS idx_memory_items_embedding
    ON memory_items USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- docs/56 §3：OpenAPI 导入内容指纹去重 + 软删除/恢复。
--   content_hash 解析后稳定子集的 sha256（同文档重复导入必得同值）；
--   deleted_at   软删时间（UTC ISO），NULL 表示未删；list/get/名额只看未删行。
-- 部分索引仅覆盖未删规格的 (租户, 指纹)，支撑去重查询且不约束已删历史。
-- 幂等：ADD COLUMN / CREATE INDEX IF NOT EXISTS，可重复执行。
ALTER TABLE openapi_imports ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE openapi_imports ADD COLUMN IF NOT EXISTS deleted_at TEXT;
CREATE INDEX IF NOT EXISTS idx_openapi_imports_hash
    ON openapi_imports (tenant_id, content_hash) WHERE deleted_at IS NULL;

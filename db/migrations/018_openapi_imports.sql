-- Persisted imported OpenAPI specs (docs/43, PG persistence batch)
CREATE TABLE IF NOT EXISTS openapi_imports (
    id          TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    seq         BIGINT NOT NULL,
    title       TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    operations  JSONB NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_openapi_imports_tenant_seq
    ON openapi_imports (tenant_id, seq);

COMMENT ON TABLE openapi_imports IS
    'Imported OpenAPI specs; ids via storage_id_seq, operations hold importable OperationDescriptors (docs/43)';

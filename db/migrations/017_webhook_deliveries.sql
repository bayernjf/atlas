-- Inbound webhook delivery records (docs/40, hardening batch)
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    tenant_id   TEXT NOT NULL,
    webhook_id  TEXT NOT NULL,
    binding_id  TEXT NOT NULL,
    topic       TEXT NOT NULL,
    shop        TEXT NOT NULL,
    status      TEXT NOT NULL,
    reasons     JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload     JSONB,
    duplicates  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    replayed_at TEXT,
    PRIMARY KEY (tenant_id, webhook_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_tenant_status
    ON webhook_deliveries (tenant_id, status, created_at);

COMMENT ON TABLE webhook_deliveries IS
    'Inbound webhook dedup/dead-letter records; payload stored only for dead rows (docs/40)';

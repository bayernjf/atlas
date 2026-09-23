-- Per-tenant alert external notification settings (docs/52)
CREATE TABLE IF NOT EXISTS alert_notify_settings (    tenant_id          TEXT PRIMARY KEY,
    enabled            BOOLEAN NOT NULL DEFAULT FALSE,
    channel            TEXT NOT NULL DEFAULT 'dingtalk',
    to_addr            TEXT NOT NULL DEFAULT '',
    secret             TEXT NOT NULL DEFAULT '',
    min_severity       TEXT NOT NULL DEFAULT 'critical',
    updated_at         TEXT NOT NULL DEFAULT '',
    last_notified_at   TEXT,
    last_error_code    TEXT,
    last_error_message TEXT
);

COMMENT ON TABLE alert_notify_settings IS
    'Tenant singleton alert notification channel and last delivery status (docs/52)';

-- M9 rollout_gate alerts carry an action (rollback record); PG table previously lacked it
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS action JSONB;

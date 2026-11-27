-- T4 任务总线 PG 化（docs/73 W3、docs/19 §2.3.1）：task_envelopes。
-- 进程内 TaskStore 与 SQLite 档保留（测试/本机旧行为）；ATLAS_STORAGE_BACKEND=pg
-- 时 TenantRegistry 装配 PgTaskStore，跨重启/跨实例共享同一租户的任务信封。
-- 行内 tenant_id 过滤（非 RLS，照 monitoring_* 先例）；envelope 存完整 JSONB，
-- state/conflict_key/priority 冗余列只用于仲裁与过滤，权威仍是 envelope。

CREATE TABLE IF NOT EXISTS task_envelopes (
    tenant_id       TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    seq             BIGSERIAL,
    state           TEXT NOT NULL,
    conflict_key    TEXT NOT NULL DEFAULT '',
    priority        INTEGER NOT NULL DEFAULT 0,
    envelope        JSONB NOT NULL,
    PRIMARY KEY (tenant_id, task_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_task_envelopes_tenant_seq
    ON task_envelopes (tenant_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_task_envelopes_conflict
    ON task_envelopes (tenant_id, conflict_key, state);

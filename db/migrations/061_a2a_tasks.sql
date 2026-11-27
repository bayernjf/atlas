-- A2A 任务台账 PG 化（docs/90、docs/73 W3）：a2a_tasks。
-- 边界：A2A 任务仍同步执行；本表只持久化任务快照与幂等键，支持跨重启
-- tasks/get / tasks/cancel / 幂等重放。`ATLAS_STORAGE_BACKEND=pg` 时启用。
-- 行内 tenant_id 仅用于幂等查找，主键仍是 task_id（UUID）。

CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id         TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL,
    expires_at      DOUBLE PRECISION NOT NULL,
    idempotency_key TEXT,
    fingerprint     TEXT,
    task            JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_a2a_tasks_idem
    ON a2a_tasks (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_a2a_tasks_expires
    ON a2a_tasks (expires_at);

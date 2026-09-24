-- docs/61 §5 H4：影子运行 PG 化（ShadowStore → 两档；D26 部分取回、不解除）。
--   进程内档为 deque(maxlen=100) ring；PG 档落本表，add 后惰性裁到最近 100 行，
--   list 按 seq 倒序、clamp 1-200，reset（demo 清库）清空本租户行。
-- id 用全局 storage_id_seq 生成 sr-N（与内存档 sr-N 同形），数字部分另存 seq 供排序。
-- 四个子模型（decisions/tool_intents/human_outcome/comparison）走 JSONB，读写经
--   pydantic model_validate/model_dump，保证两档投影逐键一致（照 openapi_imports.operations
--   与 monitoring_runs.spans 先例）。created_at 为 TEXT UTC ISO-8601（对齐 002/024 主约定）。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
CREATE TABLE IF NOT EXISTS shadow_runs (
    tenant_id     TEXT NOT NULL,
    id            TEXT NOT NULL,
    seq           BIGINT NOT NULL,
    graph_id      TEXT NOT NULL,
    inputs        JSONB,
    status        TEXT NOT NULL,
    error         TEXT,
    decisions     JSONB NOT NULL DEFAULT '[]',
    tool_intents  JSONB NOT NULL DEFAULT '[]',
    trace_id      TEXT NOT NULL,
    auto_action   TEXT,
    human_outcome JSONB,
    comparison    JSONB NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_tenant_seq
    ON shadow_runs (tenant_id, seq DESC);

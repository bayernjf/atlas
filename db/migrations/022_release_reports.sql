-- docs/56 §2：批量回放发布报告沉淀（ReleaseReport）PG 持久化。
-- 录制用例早已由 007/008 PG 化；报告表此前不存在（进程内 deque ring 100，重启即失）。
-- id 用全局 storage_id_seq（rr-N），数字部分另存 seq 供 (租户,图,seq) 排序；
-- cases 为逐例结果 JSONB（摘要列表不取此列），pass_rate total=0 时为 NULL。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行。
CREATE TABLE IF NOT EXISTS release_reports (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    graph_id    TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT 'draft',
    trigger     TEXT NOT NULL,
    total       INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    skipped     BOOLEAN NOT NULL,
    blocked     BOOLEAN NOT NULL,
    pass_rate   DOUBLE PRECISION,
    cases       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_release_reports_tenant_graph
    ON release_reports (tenant_id, graph_id, seq);

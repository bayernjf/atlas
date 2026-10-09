-- 打包 AC（docs/110）：AI 评估 Harness v1——evaluations 表（离线批评估结果持久化）。
-- 评估结果含审计价值（谁在什么图上跑过什么评估、结论如何），属审计价值数据，
-- 照 docs/110 §一.5 与 §六.3 决策：PG 两档持久化，reset 同清。
-- 幂等：IF NOT EXISTS；同 id 重复写走 UPSERT（PgEvaluationStore 已处理）。
CREATE TABLE IF NOT EXISTS evaluations (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    summary JSONB NOT NULL,
    cases JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_evaluations_tenant_created
    ON evaluations (tenant_id, created_at DESC);

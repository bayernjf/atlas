-- 打包 ZU（docs/94）：反思进化 L2 v3——候选采纳状态持久化＋节点级定位。
--   进程内 ReflectionStore 双 deque ring（reports/candidates 各 100）PG 化：两表均每租户
--   一行一记录、行内 tenant_id 过滤（非 RLS），写入后惰性裁到最近 100 行（OFFSET :keep DELETE，
--   照 028 shadow_runs 先例）。reports 无业务 id，seq 走全局 storage_id_seq 仅用于排序，不进投影；
--   candidates 的 id＝refl-N，数字部分同样取自 storage_id_seq（与内存档 refl-N 同形）。
-- changes/prompt_suggestions/reasons 走 JSONB，读写经 pydantic model_validate/model_dump，
--   保证两档投影逐键一致（照 028）。generated_at 为 TEXT UTC ISO-8601（对齐 002/024/028 主约定）。
-- decision_status/decided_at 为候选级人工处理标记（null=pending；adopted|dismissed，可改判覆盖）。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库由迁移运行器顺序应用 001→最新，不改 002。
CREATE TABLE IF NOT EXISTS reflection_reports (
    tenant_id    TEXT NOT NULL,
    seq          BIGINT NOT NULL,
    candidate_id TEXT,
    graph_id     TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    status       TEXT NOT NULL,
    reasons      JSONB NOT NULL DEFAULT '[]',
    generated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_reflection_reports_tenant_seq
    ON reflection_reports (tenant_id, seq DESC);

CREATE TABLE IF NOT EXISTS reflection_candidates (
    tenant_id          TEXT NOT NULL,
    id                 TEXT NOT NULL,
    seq                BIGINT NOT NULL,
    graph_id           TEXT NOT NULL,
    base_version       INTEGER NOT NULL,
    changes            JSONB NOT NULL DEFAULT '[]',
    prompt_suggestions JSONB NOT NULL DEFAULT '[]',
    evidence_digest    TEXT NOT NULL DEFAULT '',
    generated_at       TEXT NOT NULL,
    decision_status    TEXT,
    decided_at         TEXT,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_reflection_candidates_tenant_seq
    ON reflection_candidates (tenant_id, seq DESC);

COMMENT ON TABLE reflection_reports IS
    '反思收尾报告（docs/94 打包 ZU）：每租户 seq 倒序 ring 100，seq 走 storage_id_seq 仅排序不进投影';
COMMENT ON TABLE reflection_candidates IS
    '反思候选（docs/94 打包 ZU）：id=refl-N；decision_status null=pending/adopted/dismissed，可改判；changes 内含可选 node_id 节点定位';

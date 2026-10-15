-- 打包 BQ（docs/133）：D71 NL 造工具——adapter_drafts 表（草稿采纳持久化，配置资产跨重启不可丢）。
--   LLM 结构化输出 AdapterDefinition（JSONB definition）→ dry-run 连通校验（reachability）
--   → 人工采纳（decision_status adopted/dismissed，复用 D51 采纳形态）→ adopted 后 runtime
--   动态装配（generated:{draft_id}，docs/133 BQ-D4）。
--   draft_id 数字部分取 storage_id_seq（与 autoapply_runs 同款）；行内 tenant_id 过滤；
--   reset 同清本租户。不挂 ATLAS_RETENTION_DAYS（配置资产）。

CREATE TABLE IF NOT EXISTS adapter_drafts (
    tenant_id       TEXT NOT NULL,
    draft_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    definition      JSONB NOT NULL,
    decision_status TEXT,              -- NULL=待决策；adopted/dismissed（可改判）
    reachability    JSONB,             -- {checked_at, ok, reason} 或 NULL
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, draft_id)
);

CREATE INDEX IF NOT EXISTS idx_adapter_drafts_tenant_status
    ON adapter_drafts (tenant_id, decision_status);

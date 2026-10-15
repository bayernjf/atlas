-- 打包 BP（docs/132）：D75 进化候选门控自动生效——autoapply_runs 表（每圈证据持久化）。
--   run_autoapply 推进链（patch→影子→黄金回放→发布→canary）每一步落一条 AutoApplyRun；
--   "自动"的每一步都留下可否证记录（docs/127 里程碑 E Done-when），跨重启/跨实例可见。
--   内存档为进程内 ring 100（reflection/autoapply.py）；本表 reset 同清本租户。
--   行内 tenant_id 过滤（非 RLS，照 reflection_* 先例）；不挂 ATLAS_RETENTION_DAYS
--   （docs/129 AU-D7 retention 余部与本批无关）。run_id 数字部分取 storage_id_seq。

CREATE TABLE IF NOT EXISTS autoapply_runs (
    tenant_id       TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    candidate_id    TEXT NOT NULL,
    graph_id        TEXT NOT NULL,
    base_version    INTEGER NOT NULL,
    enabled_scopes  JSONB NOT NULL DEFAULT '[]'::jsonb,
    patch           JSONB,
    shadow          JSONB,
    gate            JSONB,
    publish_version INTEGER,
    rollout_status  TEXT,
    status          TEXT NOT NULL,
    reason          TEXT,
    stages          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_autoapply_runs_tenant_graph ON autoapply_runs (tenant_id, graph_id);

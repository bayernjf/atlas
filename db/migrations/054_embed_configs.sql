-- 打包 BQ（docs/133）：D76 网页嵌入——embed_configs 表（第七类 SchemaRegistry 实体）。
--   graph 维度的"允许只读嵌入"开关（docs/127 里程碑 F Done-when：签名短链打开只读嵌入面）。
--   enabled 缺省 false（fail-closed）；token_ttl_seconds 1–86400（嵌入短链时效，端点层门控）。
--   行内 tenant_id 过滤（非 RLS，照 reflection_* 先例）；不挂 ATLAS_RETENTION_DAYS（配置资产）。
--   内存档为进程内 dict（embeds/store.py）；本表 reset 同清本租户。

CREATE TABLE IF NOT EXISTS embed_configs (
    tenant_id         TEXT NOT NULL,
    graph_id          TEXT NOT NULL,
    enabled           BOOLEAN NOT NULL DEFAULT FALSE,
    token_ttl_seconds INTEGER NOT NULL DEFAULT 3600,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, graph_id)
);

CREATE INDEX IF NOT EXISTS idx_embed_configs_tenant ON embed_configs (tenant_id);

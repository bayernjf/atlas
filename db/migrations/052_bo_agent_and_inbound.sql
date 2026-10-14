-- 打包 BO（docs/131）：D73 入站会话 + D74 智能体团队面——数据模型基础。
--   (1) runs 加 agent_id：run/节点带 agent 维度（D74 智能体团队面），缺省 ''
--       代表「非 agent 运行」；旧行按 '' 继续代表非 agent 运行，投影逐字不变
--       （启用面缺省空，D-D6）。索引 (tenant_id, agent_id) 供「某 agent 跑过哪些 run」。
--   (2) inbound_deliveries：入站消息投递记录（去重/死信/指标），复用
--       webhook_deliveries 范式（docs/40），幂等键 (tenant_id, channel, message_id)；
--       不存 token/密钥，payload 仅死信行保留（与 webhook_deliveries 同策略）。
--       本批只用假供应商（docs/73 1.3 ⛔），channel 为通用维度（非 shopify 专属）。
-- 全脚本幂等可重入。

ALTER TABLE runs ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs (tenant_id, agent_id);

-- 渠道绑定加会话↔图映射（D-D3：挂 binding，不做独立实体）。
ALTER TABLE channel_bindings ADD COLUMN IF NOT EXISTS inbound_mappings JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS inbound_deliveries (
    tenant_id       TEXT NOT NULL,
    channel         TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    binding_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    status          TEXT NOT NULL,
    reasons         JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload         JSONB,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    replayed_at     TEXT,
    PRIMARY KEY (tenant_id, channel, message_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_deliveries_tenant_status
    ON inbound_deliveries (tenant_id, status, created_at);

COMMENT ON TABLE inbound_deliveries IS
    'Inbound message dedup/dead-letter records (packaging BO, docs/131); payload stored only for dead rows';

-- 打包 BO（docs/131 D74）：agents 表——「可管理 agent 实体」（Bot 升格，D-D6）。
--   agent 是 run/节点带 agent_id 维度后的可管理实例：配置（name/enabled/skills/
--   adapter_ids/coordinator）落库；运行数据从 runs（按 agent_id 过滤）聚合，不在此存。
--   不存 token/密钥；coordinator 为可选 JSONB（来自 skills/Bot 底层件形状）。
-- 全脚本幂等可重入。

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    skills       JSONB NOT NULL DEFAULT '[]'::jsonb,
    adapter_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    coordinator  JSONB,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_agents_tenant ON agents (tenant_id);

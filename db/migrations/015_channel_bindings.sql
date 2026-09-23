-- =====================================================
-- Migration 015: real channel bindings (v1: Shopify)
-- File: 015_channel_bindings.sql
-- Date: 2026-09-22
-- Depends on: 002_storage.sql, 014_oauth_connections.sql
-- Run: python -m scripts.ops.apply_migrations
-- =====================================================
-- Note: docs/38（ADR T28）。渠道绑定架在 014 generic OAuth2 连接之上，本表只存
--       绑定关系与平台配置（config JSONB：shop / apiVersion），不存任何 token
--       （token 调用时经 connection service 现解密）。connection_id 全表唯一
--       （一个连接至多绑定一个渠道）。绑定属客户配置，demo reset 不清本表。
--       id 为 ch-{全局序列 storage_id_seq}；时间为 UTC iso TEXT；幂等可重跑。
-- -----------------------------------------------------

CREATE TABLE IF NOT EXISTS channel_bindings (
    tenant_id       TEXT NOT NULL,
    id              TEXT NOT NULL,
    provider        TEXT NOT NULL,
    connection_id   TEXT NOT NULL,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'connected',
    last_error      TEXT,
    created_by      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (connection_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_bindings_tenant
    ON channel_bindings (tenant_id, created_at);

COMMENT ON TABLE channel_bindings IS 'Real channel bindings (v1 Shopify); no tokens stored; not cleared by demo reset';

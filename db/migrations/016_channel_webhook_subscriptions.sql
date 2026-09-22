-- =====================================================
-- Migration 016: inbound webhook subscriptions
-- File: 016_channel_webhook_subscriptions.sql
-- Date: 2026-09-23
-- Depends on: 015_channel_bindings.sql
-- Run: python -m scripts.ops.apply_migrations
-- =====================================================
-- Note: docs/39（ADR T29）。per-binding topic→graph 订阅列表存为 JSONB：
--       每项 {topic, graph_id, enabled}（snake_case 存储；REST 投影 camelCase）。
--       默认 '[]'；不存任何 webhook body 或共享密钥（验签密钥每次经
--       connection service 现解密 client_secret）。幂等可重跑。
-- -----------------------------------------------------

ALTER TABLE channel_bindings
    ADD COLUMN IF NOT EXISTS webhook_subscriptions JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN channel_bindings.webhook_subscriptions IS
    'Per-binding inbound webhook subscriptions: [{topic, graph_id, enabled}]';

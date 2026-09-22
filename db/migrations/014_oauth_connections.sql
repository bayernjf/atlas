-- =====================================================
-- Migration 014: OAuth2 connections (generic, platform-agnostic)
-- File: 014_oauth_connections.sql
-- Date: 2026-09-22
-- Depends on: 002_storage.sql
-- Run: python -m scripts.ops.apply_migrations
-- =====================================================
-- Note: docs/35 §4（T4，D22 generic OAuth2 子集）。平台无关的授权码流程连接配置：
--       client_secret / access_token / refresh_token 一律以 SecretProvider 信封
--       （enc$v1$...）落 TEXT，绝不明文；scopes 以 JSON 文本数组存储；时间为 UTC iso TEXT。
--       id 为 conn-{全局序列}，seq 存数字部分用于同租户排序；幂等可重跑。
--       连接属租户配置+凭据，demo reset 不清本表（与审计/录制一致）。
--       本批不写死任何真实平台端点，也不做真实业务 API 调用。
-- -----------------------------------------------------

CREATE TABLE IF NOT EXISTS oauth_connections (
    tenant_id                TEXT NOT NULL,
    id                       TEXT NOT NULL,
    seq                      BIGINT NOT NULL,
    provider                 TEXT NOT NULL,
    display_name             TEXT NOT NULL,
    auth_url                 TEXT NOT NULL,
    token_url                TEXT NOT NULL,
    client_id                TEXT NOT NULL,
    client_secret_envelope   TEXT,
    scopes                   TEXT NOT NULL DEFAULT '[]',
    redirect_uri             TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'draft',
    access_token_envelope    TEXT,
    refresh_token_envelope   TEXT,
    token_type               TEXT,
    expires_at               TEXT,
    last_error               TEXT,
    created_by               TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_oauth_connections_tenant_seq
    ON oauth_connections (tenant_id, seq DESC);

COMMENT ON TABLE oauth_connections IS 'Generic OAuth2 connection configs; secrets stored as SecretProvider envelopes, never plaintext; not cleared by demo reset';

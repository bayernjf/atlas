-- =====================================================
-- Migration 013: Audit log events
-- File: 013_audit_events.sql
-- Date: 2026-09-22
-- Depends on: 002_storage.sql
-- Run: python -m scripts.ops.apply_migrations
-- =====================================================
-- Note: docs/35 §6（T6，docs/34 P1 #8）。审计事件仅记录写操作元数据
--       （actor/action 路由模板/status_code/path/ip/at），不含请求体/凭据/token。
--       demo reset 不清本表（安全痕迹不随演示数据重置）。
--       id 为 aud-{全局序列}，seq 存数字部分用于同租户时间排序；幂等可重跑。
--       v1 不做保留/轮转（生产化保留策略随 D11/运维批次）。
-- -----------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_events (
    tenant_id   TEXT NOT NULL,
    id          TEXT NOT NULL,
    seq         BIGINT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    path        TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_seq
    ON audit_events (tenant_id, seq DESC);

COMMENT ON TABLE audit_events IS 'Write-action audit trail (metadata only, no request body/credentials); not cleared by demo reset';

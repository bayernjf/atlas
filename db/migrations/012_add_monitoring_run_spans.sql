-- =====================================================
-- Migration 012: Add trace spans to monitoring_runs
-- File: 012_add_monitoring_run_spans.sql
-- Date: 2026-09-21 21:57
-- Depends on: 008_recording_graph_subgraphs_tool_calls.sql
-- Run: docker exec -i atlas-pg psql -U atlas -d atlas < db/migrations/012_add_monitoring_run_spans.sql
-- =====================================================
-- Note: docs/33 §4 D28 trace 时间线钻取。monitoring_runs 增 spans JSONB，
--       存 tracer.to_tree() 根 span（列表 API 投影剔除，trace 端点懒加载）。
--       历史行回填空对象，读回归一为 None（该运行无 Trace：历史数据/debug/回放）。
--       ADD COLUMN IF NOT EXISTS + 幂等回填，可安全重跑。
-- -----------------------------------------------------

ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS spans JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE monitoring_runs SET spans = '{}'::jsonb WHERE spans IS NULL;

COMMENT ON COLUMN monitoring_runs.spans IS 'Root trace span tree (tracer.to_tree()); empty object means no trace (legacy/debug/replay)';

-- M9 批 1：monitoring_runs 补 trace_id（M10 span 追踪，PG 后端漏透传一并补齐）
-- 与 resolved_version（M9 入站 Router 解析钉住的发布版本，03 `route_decision`/`monitoring`）。
-- 002 的 CREATE TABLE 已含这两列（新装库直接到位）；本迁移供已建旧表的库升级。
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS resolved_version INTEGER;

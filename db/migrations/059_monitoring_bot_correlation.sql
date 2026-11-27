-- T7 多智能体可观测：monitoring_runs 增加 task→bot→run 关联键。
-- 缺省空串＝非 A2A/Bot 运行（既有人工/入站/调度运行保持原形状）。
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS task_id TEXT NOT NULL DEFAULT '';
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT '';
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT '';
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS runtime_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_agent
    ON monitoring_runs (tenant_id, agent_id);

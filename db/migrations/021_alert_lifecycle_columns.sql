-- docs/55：PG 档告警 lifecycle 与内存档对齐——monitoring_alerts 补三列。
--   rule_name    自定义/内置规则名（内存档早有，PG 此前读回为 None）
--   escalated_at warning→critical 惰性升级时间（此前仅进程内、重启重评）
--   assignee     新建时值班人（此前仅进程内 OpsStore、重启丢失）
-- action（rollout_gate 回滚动作）已由迁移 020 补；新装库四列均见 002_storage.sql。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS rule_name TEXT;
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS escalated_at TEXT;
ALTER TABLE monitoring_alerts ADD COLUMN IF NOT EXISTS assignee TEXT;

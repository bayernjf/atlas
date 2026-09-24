-- docs/59 F-2：PG 档告警静默 / 值班排班持久化（此前仅进程内 OpsStore，重启清空）。
--   monitoring_silences：静默规则（命中压下告警并累计 suppressed_count）。
--   monitoring_oncall  ：per-tenant 单行值班表（成员 JSON 数组 + 轮换下标，current=members[rot_index % n]）。
-- 内存档继续用 OpsStore，行为不变；REST 形状不变。assignee 列已由 021 加在 monitoring_alerts。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
CREATE TABLE IF NOT EXISTS monitoring_silences (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    rule_id TEXT,
    graph_id TEXT,
    reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    suppressed_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_monitoring_silences_tenant ON monitoring_silences (tenant_id);

CREATE TABLE IF NOT EXISTS monitoring_oncall (
    tenant_id TEXT PRIMARY KEY,
    members JSONB NOT NULL,
    rot_index INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    updated_by TEXT
);

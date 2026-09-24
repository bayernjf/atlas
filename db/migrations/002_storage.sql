-- M5b 存储层 DDL（docs/24 §5 / docs/11 S1；无向量列需求）。
-- 迁移序号 002：承接 001_enable_pgvector.sql（vector 扩展）。
-- 全部运行时表按 tenant_id 语句级过滤（docs/24 §1.2②，不引入 PG RLS）。

-- 通用 id 序列：各 store 以 <prefix>-<n> 生成 id（graph-N / run-N / feedback-N / rec-N / alt-N）。
CREATE SEQUENCE IF NOT EXISTS storage_id_seq;

-- 图定义（latest 草稿 + 已发布版本；docs/24 §5、20 §4.1 M6 版本化）
CREATE TABLE IF NOT EXISTS graphs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    definition JSONB NOT NULL,
    node_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graphs_tenant ON graphs (tenant_id);

CREATE TABLE IF NOT EXISTS graph_versions (
    graph_id TEXT NOT NULL,
    release_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    definition JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (graph_id, release_version)
);
CREATE INDEX IF NOT EXISTS idx_graph_versions_tenant ON graph_versions (tenant_id);

-- 运行记录（run 状态落库，docs/24 §3.3；挂起信息供 §4 /api/runs 查询）
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    suspended_at TEXT,
    finished_at TEXT,
    error TEXT,
    kind TEXT,
    node_id TEXT,
    deadline_at TEXT,
    resume_token TEXT,
    outputs JSONB,
    trace JSONB
);
CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs (tenant_id);

-- 中断帧（docs/24 §2.3 interruption_frame；payload 含 graph_snapshot/resume_state）
CREATE TABLE IF NOT EXISTS interruptions (
    resume_token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    deadline_at TIMESTAMPTZ,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interruptions_tenant ON interruptions (tenant_id);

-- 登录会话（docs/24 §5；进程内 SessionStore 的 PG 映射）
CREATE TABLE IF NOT EXISTS iam_sessions (
    token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    issued_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iam_sessions_tenant ON iam_sessions (tenant_id);

-- 录制用例（persistent 档，reset 不清除；docs/24 §5）
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    graph JSONB NOT NULL,
    inputs JSONB,
    steps JSONB NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recordings_tenant ON recordings (tenant_id);

-- 反馈（persistent 档，reset 不清除；docs/24 §5）
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    contact TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON feedback (tenant_id);

-- 监控运行记录 / 告警 / 规则（docs/24 §5）
CREATE TABLE IF NOT EXISTS monitoring_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms DOUBLE PRECISION NOT NULL,
    nodes JSONB NOT NULL,
    error TEXT,
    trace_id TEXT,
    resolved_version INTEGER,
    business JSONB
);
CREATE INDEX IF NOT EXISTS idx_monitoring_runs_tenant ON monitoring_runs (tenant_id);

CREATE TABLE IF NOT EXISTS monitoring_alerts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_run_id TEXT,
    count INTEGER NOT NULL DEFAULT 1,
    action JSONB,
    rule_name TEXT,
    escalated_at TEXT,
    assignee TEXT
);
CREATE INDEX IF NOT EXISTS idx_monitoring_alerts_tenant ON monitoring_alerts (tenant_id);

CREATE TABLE IF NOT EXISTS monitoring_rules (
    tenant_id TEXT PRIMARY KEY,
    config JSONB NOT NULL
);

-- 告警静默 / 值班排班（docs/59 F-2；增量迁移见 024_monitoring_silences_oncall.sql）
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

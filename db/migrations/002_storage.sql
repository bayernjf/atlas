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
    created_at TEXT NOT NULL,
    -- 迁移 029（docs/62 §3 L2）：一次性认领——NULL＝还没人越过该挂起点，非空＝已消费、
    -- 任何进程都不得再执行其下游。时钟只走 DB（CURRENT_TIMESTAMP）。
    resumed_at TIMESTAMPTZ,
    resumed_by TEXT
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
    updated_by TEXT,
    -- docs/60 §4.2：惰性按日自动轮换（迁移 026）
    rotation_interval_days INTEGER NULL,
    last_rotated_at TEXT NULL
);

-- 消息投递日志（docs/60 §6 G5；增量迁移见 025_message_deliveries.sql）
-- 群发逐目标多条共享消息 id，行唯一键用 (tenant_id, seq)。
CREATE TABLE IF NOT EXISTS message_deliveries (
    tenant_id     TEXT NOT NULL,
    id            TEXT NOT NULL,
    seq           BIGINT NOT NULL,
    channel       TEXT NOT NULL,
    to_targets    JSONB NOT NULL,
    subject       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    attempts      INTEGER NOT NULL,
    elapsed_ms    INTEGER NOT NULL,
    error_code    TEXT,
    error_message TEXT,
    sent_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_message_deliveries_seq
    ON message_deliveries (tenant_id, seq DESC);

-- 已决审批历史（docs/61 §3 H2；增量迁移见 027_approval_history.sql）
-- token 为审批 uuid4，一条审批最多一行；时间列 TEXT 存 UTC ISO-8601；排序走 seq。
CREATE TABLE IF NOT EXISTS approval_history (
    tenant_id        TEXT NOT NULL,
    token            TEXT NOT NULL,
    seq              BIGINT NOT NULL,
    node_id          TEXT NOT NULL,
    graph_id         TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    approver         TEXT NOT NULL DEFAULT '',
    decision         TEXT NOT NULL,
    resolved_by      TEXT NOT NULL DEFAULT '',
    comment          TEXT,
    card_template_id TEXT,
    created_at       TEXT NOT NULL,
    resolved_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, token)
);
CREATE INDEX IF NOT EXISTS idx_approval_history_tenant_seq
    ON approval_history (tenant_id, seq DESC);

-- 影子运行（docs/61 §5 H4；增量迁移见 028_shadow_runs.sql）
-- 四子模型走 JSONB；id=sr-N 与内存档同形，seq 存数字部分供排序；ring 100 惰性裁剪。
CREATE TABLE IF NOT EXISTS shadow_runs (
    tenant_id     TEXT NOT NULL,
    id            TEXT NOT NULL,
    seq           BIGINT NOT NULL,
    graph_id      TEXT NOT NULL,
    inputs        JSONB,
    status        TEXT NOT NULL,
    error         TEXT,
    decisions     JSONB NOT NULL DEFAULT '[]',
    tool_intents  JSONB NOT NULL DEFAULT '[]',
    trace_id      TEXT NOT NULL,
    auto_action   TEXT,
    human_outcome JSONB,
    comparison    JSONB NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_tenant_seq
    ON shadow_runs (tenant_id, seq DESC);

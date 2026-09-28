-- 打包 X：租户私有用户模板（docs/85 D-5；D25 切片）。
CREATE TABLE IF NOT EXISTS user_templates (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    graph JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_templates_tenant_seq
    ON user_templates (tenant_id, seq);

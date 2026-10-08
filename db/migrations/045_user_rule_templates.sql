-- 打包 ZS：租户私有告警规则模板（docs/102 D-2；D28 切片）。
CREATE TABLE IF NOT EXISTS user_rule_templates (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    config JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_rule_templates_tenant_seq
    ON user_rule_templates (tenant_id, seq);

-- 打包 A2：消息模板系统（docs/98；D24 最小切片）。
-- 租户消息通知模板：name 租户内唯一（LOWER casefold）、kind∈{approval,alert}、
-- subject/body 含 {{var}} 占位（variables 声明白名单，正文占位 ⊆ 声明由 API 层校验）、
-- variables JSONB。消费：审批通知（EmailApprovalNotifier）与告警通知（notify.py）取对应 kind 模板渲染，
-- 未配置模板回退默认正文逐字不变（纯超集）。
CREATE TABLE IF NOT EXISTS message_templates (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    variables JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_message_templates_tenant_seq
    ON message_templates (tenant_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS ux_message_templates_tenant_name
    ON message_templates (tenant_id, LOWER(name));
COMMENT ON TABLE message_templates IS '租户消息通知模板（docs/98 打包 A2）：kind=approval 供审批通知、kind=alert 供告警通知，未配置回退默认正文';

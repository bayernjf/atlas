-- docs/61 §3 H2：已决审批历史 PG 化（ApprovalHistoryStore；D20 部分取回、不解除）。
--   进程内档为 deque(maxlen=200) ring；PG 档落本表，record 后惰性裁到最近 200 行，
--   list 按 seq 倒序、clamp 1-200，reset（demo 清库）清空本租户行。
-- pending 挂起与中断帧不在本表：仍由 002_storage.sql 的 interruptions 表承担。
-- 时间列用 TEXT 存 UTC ISO-8601（对齐 002/024 主约定，不采 025 sent_at TIMESTAMPTZ 例外）：
--   _Pending 内存态是 time.time() float epoch，写库前经 epoch_to_iso 转换。
-- 行唯一键 (tenant_id, token)：token 是审批 uuid4，一条审批最多一条历史；
--   跨重启 restore 后再次决策走 upsert，seq 推到最新（后到的决策排序在上）。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
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

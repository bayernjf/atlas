-- docs/60 §6 G5：消息投递日志 PG 化（DeliveryStore；MessageService 仍为服务）。
--   进程内档为 deque(maxlen=200) ring；PG 档落本表，record 后惰性裁到最近 200 行，
--   list 按 seq 倒序、clamp 1-200，reset（demo 清库）清空本租户行。
-- 落码偏差（收口注记）：契约草拟 PRIMARY KEY (tenant_id, id)，但群发（webhook/IM 多
--   URL）一条消息逐目标产生多条记录、共享同一消息 id（message uuid），故行唯一键改用
--   全局单调 seq（nextval('storage_id_seq')），id 作为普通列保留、可重复。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
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

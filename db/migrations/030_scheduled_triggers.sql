-- docs/68 §2.2（打包 N）：定时触发调度器的两张表。
--   实测成因：`schedule/cron` 自 W 系列起只有 graph/dsl.py:1433 的"填没填"校验，src 内没有任何
--   调度器 ⇒ 运营者能把 trigger 选成定时、能发布、图永远不会自己跑（docs/63 §0A N4）。
-- 键形态：跟 webhook_deliveries / approval_history / shadow_runs 一致取**租户复合键**，不引入
--   schedule_id 代理键——代理键只多出"谁生成""图删了谁回收"两件无收益的事（docs/68 §2.2）。
-- 时间列用 TIMESTAMPTZ 而非仓库主约定的 TEXT ISO-8601：slot_utc 是**认领键**，TEXT 唯一性依赖
--   字节完全一致（`Z` 与 `+00:00`、微秒省略都会给同一个槽位造出两行，那就把"至多一次"变成了
--   "至多几次看序列化心情"）；比较与去重交给 DB，读回后仍经 scheduling.models.to_utc_iso 归一
--   出投影，两档投影逐键一致不受影响。同类判断见 029（该列参与比较判定就跟表内口径）。
-- schedule_fires **只记实际派发**：重叠跳过不写本表、不消耗槽位（docs/68 §1 D-6），因此没有
--   outcome 列；跳过计数在 schedules.skip_count / last_skipped_at（运维可见性走那两列）。
-- 不建额外索引：认领只按 PK 单行命中，而本批没有任何"按租户列近期槽位"的读端点
--   （GET /api/schedules 读的是 schedules）。
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
CREATE TABLE IF NOT EXISTS schedules (
    tenant_id       TEXT NOT NULL,
    graph_id        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    cron            TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fired_at   TIMESTAMPTZ,
    last_skipped_at TIMESTAMPTZ,
    skip_count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, graph_id)
);
COMMENT ON TABLE schedules IS '定时触发注册项：发布时派生（docs/68 §1 D-3/D-4），只跑已发布钉版，UTC-only';
COMMENT ON COLUMN schedules.version IS '发布号；派发时按此钉版，永不回落到 latest 草稿';
COMMENT ON COLUMN schedules.cron IS '最小 5 字段 cron 子集（ADR T30），保存期已校验';
COMMENT ON COLUMN schedules.enabled IS '运营者开关，跨重启保留；false 只停未来触发，不取消在途运行';
COMMENT ON COLUMN schedules.last_fired_at IS '最近一次真实派发的槽位（UTC）';
COMMENT ON COLUMN schedules.last_skipped_at IS '最近一次因同图仍在跑而跳过的槽位（UTC）';
COMMENT ON COLUMN schedules.skip_count IS '累计跳过次数；运维可见性用，不参与派发判定';

CREATE TABLE IF NOT EXISTS schedule_fires (
    tenant_id TEXT NOT NULL,
    graph_id  TEXT NOT NULL,
    slot_utc  TIMESTAMPTZ NOT NULL,
    fired_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, graph_id, slot_utc)
);
COMMENT ON TABLE schedule_fires IS '槽位一次性认领账本：INSERT ... ON CONFLICT DO NOTHING 的 rowcount 即互斥（docs/68 §1 D-5，照 029/docs/62 L2 同一条纪律）';
COMMENT ON COLUMN schedule_fires.slot_utc IS '被认领的分钟槽（秒/微秒归零）；一行＝该槽至多派发一次';
COMMENT ON COLUMN schedule_fires.fired_at IS '实际写认领行的时刻，与 slot_utc 不同：槽位是计划，这列是发生';

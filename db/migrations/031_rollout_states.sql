-- 打包 T：RoutingStore PG 化＝rollout 运行态 PG 落库（docs/81；D32 余部切片）。
--   实测成因：灰度配置与 rollout 状态机只活在进程内 RoutingStore，PG 档重启后 canary/full/
--   rolled_back 状态、stable/candidate 版本钉与分流计数全部归零——配置过的灰度在重启后悄悄回到 idle。
-- 键形态：同 schedules/shadow_runs 取租户复合键 PK(tenant_id, graph_id)，不引入代理键
--   （图 id 即作用域；代理键只多出"谁生成、图删了谁回收"的无收益问题）。
-- 时间列用 TEXT ISO-8601 而非 TIMESTAMPTZ：这些列不参与认领/唯一判定，runs 表同款；
--   读回投影与内存档逐键同型（字符串），无 datetime 转换漂移。config/traffic 用 JSONB：
--   结构化配置与计数整体读改写，行锁内序列化。
-- 并发口径：状态机写与 resolve 读改写一律 SELECT ... FOR UPDATE 串行（代码侧，pg_store.py），
--   不引应用锁；updated_at 供排障读最后变更时刻。
-- 不做 retention：配置型数据不进 prune_expured，不按保留期自动删。
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行；新装库由迁移 CLI 顺序应用 001→最新，
--   002_storage.sql 为冻结基线，新表不回登（030 schedules 同口径）。
CREATE TABLE IF NOT EXISTS rollout_states (
    tenant_id       TEXT NOT NULL,
    graph_id        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    config          JSONB,
    stable          INTEGER,
    candidate       INTEGER,
    started_at      TEXT,
    rolled_back_at  TEXT,
    rollback_reason TEXT,
    rollback_actor  TEXT,
    traffic         JSONB NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, graph_id)
);
COMMENT ON TABLE rollout_states IS '灰度 rollout 运行态 PG 落库（打包 T/docs/81；D32 余部，不解锁多实例）';
COMMENT ON COLUMN rollout_states.status IS 'idle|canary|full|rolled_back；行锁内状态机迁移，REST 语义同内存档';
COMMENT ON COLUMN rollout_states.config IS 'RolloutConfig JSON（model_dump）；canary 期 full 剔除在代码侧不固化';
COMMENT ON COLUMN rollout_states.stable IS '前一发布版；start 时取最新两发布版';
COMMENT ON COLUMN rollout_states.candidate IS '最新发布版；rolled_back 后新流量回 stable';
COMMENT ON COLUMN rollout_states.traffic IS '分流计数 {stable,candidate,segments}；resolve 每请求行锁内 UPSERT';
COMMENT ON COLUMN rollout_states.updated_at IS '最后写时刻（TEXT ISO），仅供排障';

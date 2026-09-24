-- docs/60 §4.2 G3：值班表惰性按日自动轮换（不引定时器；get_oncall / 告警命中取值班人
--   的读路径惰性 maybe_auto_rotate 推进，跨实例以 PG 读路径为准，不做分布式锁）。
--   rotation_interval_days：1-365 自动轮换间隔（天），NULL＝不自动；
--   last_rotated_at：上次轮换基准日期（UTC，YYYY-MM-DD）。
-- 落码偏差（收口注记）：契约草拟 last_rotated_at 为 TIMESTAMPTZ，但本表 updated_at/
--   created_at 等时间列在迁移 024 中统一为 TEXT，故这里同型用 TEXT，避免同表混用时间类型。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
ALTER TABLE monitoring_oncall
    ADD COLUMN IF NOT EXISTS rotation_interval_days INTEGER NULL,
    ADD COLUMN IF NOT EXISTS last_rotated_at TEXT NULL;

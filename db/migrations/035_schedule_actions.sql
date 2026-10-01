-- docs/88 §3 P-4（打包 ZH，2026-10-01）：调度注册表加「动作」维度。
--   实测成因：既有调度项**全部是发布派生**的（api/main.py:_derive_schedule_on_publish 按图内
--   schedule/cron 触发节点建行），而「跑图」与「反思」是同一张图的两种动作 ⇒ 一图一行装不下
--   （同一个 (tenant_id, graph_id) 只能有一行），必须把动作并进键。
-- 键形态：schedules 主键 (tenant_id, graph_id) → (tenant_id, graph_id, action)；
--   schedule_fires 认领键 (tenant_id, graph_id, slot_utc) → (tenant_id, graph_id, action, slot_utc)。
--   **认领键必须一并换**：否则同一槽的跑图与反思会互相吃掉对方的认领（DEFAULT 'run' 会让
--   两条 INSERT 落在同一主键上，后到的那次静默丢掉派发权）。
-- 既有行由 DEFAULT 'run' 补齐，语义逐字不变（全部历史行都是跑图项）。
-- 幂等：ADD COLUMN IF NOT EXISTS ＋ DROP CONSTRAINT IF EXISTS ＋ ADD PRIMARY KEY，可重复执行。
--   重跑时 drop 掉的是已含 action 的主键、再原样建回，结果一致。
-- 不建额外索引：两处命中都按主键单行。
-- 不改 db/migrations/002_storage.sql：它是**冻结基线**（docs/81 §，030 起即未登记进 002），
--   新装库由迁移运行器顺序应用 001→最新（storage/migrations.py，版本权威＝文件名 glob）。
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'run';
ALTER TABLE schedules DROP CONSTRAINT IF EXISTS schedules_pkey;
ALTER TABLE schedules ADD PRIMARY KEY (tenant_id, graph_id, action);
COMMENT ON COLUMN schedules.action IS '动作：run＝跑一次已发布钉版；reflect＝跑一次反思 pass（只出建议，不改图）';

ALTER TABLE schedule_fires ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'run';
ALTER TABLE schedule_fires DROP CONSTRAINT IF EXISTS schedule_fires_pkey;
ALTER TABLE schedule_fires ADD PRIMARY KEY (tenant_id, graph_id, action, slot_utc);
COMMENT ON COLUMN schedule_fires.action IS '同 schedules.action：跑图与反思各自认领同一槽位，互不吞并';

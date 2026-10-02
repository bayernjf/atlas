-- docs/08 打包 ZN（2026-10-03；D41 ③④）：调度身份从「动作」改为「调度 id」，并加并发策略。
--   实测成因：v1 一张图只按第一个定时触发节点建一条 run＋一条 reflect（api/main.py 的
--   _schedule_spec_of_published 对多个定时节点只取第一个、其余 warning），而真实图可挂
--   多个定时节点（不同 cron）⇒ 行键 (tenant_id, graph_id, action) 装不下多条 run，必须给
--   每条调度一个稳定身份。
-- 键形态：schedules 主键 (tenant_id, graph_id, action) → (tenant_id, graph_id, schedule_id)；
--   schedule_fires 认领键 (tenant_id, graph_id, action, slot_utc)
--     → (tenant_id, graph_id, schedule_id, slot_utc)。action 退为列属性。
--   schedule_id 口径：run＝图内该定时触发节点 id（随图钉版、稳定）；reflect 为图级保留
--   '__reflect__'（反思是整图 pass，不随每个定时节点倍增）。
-- 存量行回填（DEFAULT '' 后立即 UPDATE，再改主键）：
--   reflect → '__reflect__'；run → '__primary__'（v1 run 行的节点 id 在迁移期不可知，
--   用保留命名空间；该图下次发布即由派生 reconcile 改回真实节点 id）。
-- overlap_policy：同一调度上一次触发仍 busy（running/suspended）时本槽的策略——
--   'skip'（缺省，v1 逐字行为：跳过）／'allow'（照常认领派发、并发起一条新 run）；
--   'queue'（排队）明确缓做。reflect 由派生逻辑强制 'skip'。
-- 幂等：ADD COLUMN IF NOT EXISTS ＋ 回填带 schedule_id='' 条件 ＋ DROP CONSTRAINT IF EXISTS
--   ＋ ADD PRIMARY KEY，可重复执行；重跑时回填条件不再命中、主键 drop 后原样建回。
-- 不改 db/migrations/002_storage.sql：它是冻结基线（030 起即未登记进 002），新装库由迁移
--   运行器顺序应用 001→最新（storage/migrations.py，版本权威＝文件名 glob）。
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS schedule_id TEXT NOT NULL DEFAULT '';
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS overlap_policy TEXT NOT NULL DEFAULT 'skip';
UPDATE schedules SET schedule_id = '__reflect__'
    WHERE action = 'reflect' AND schedule_id = '';
UPDATE schedules SET schedule_id = '__primary__'
    WHERE action = 'run' AND schedule_id = '';
ALTER TABLE schedules DROP CONSTRAINT IF EXISTS schedules_pkey;
ALTER TABLE schedules ADD PRIMARY KEY (tenant_id, graph_id, schedule_id);
COMMENT ON COLUMN schedules.schedule_id IS '调度身份：run＝定时触发节点 id（一图可多条），reflect＝图级保留 __reflect__';
COMMENT ON COLUMN schedules.overlap_policy IS '并发策略：skip（busy 即跳过，v1 行为）/allow（busy 也并发再起一条）；queue 缓做';

ALTER TABLE schedule_fires ADD COLUMN IF NOT EXISTS schedule_id TEXT NOT NULL DEFAULT '';
UPDATE schedule_fires SET schedule_id = '__reflect__'
    WHERE action = 'reflect' AND schedule_id = '';
UPDATE schedule_fires SET schedule_id = '__primary__'
    WHERE action = 'run' AND schedule_id = '';
ALTER TABLE schedule_fires DROP CONSTRAINT IF EXISTS schedule_fires_pkey;
ALTER TABLE schedule_fires ADD PRIMARY KEY (tenant_id, graph_id, schedule_id, slot_utc);
COMMENT ON COLUMN schedule_fires.schedule_id IS '同 schedules.schedule_id：同一图的多条调度各认各的槽，互不吞并';

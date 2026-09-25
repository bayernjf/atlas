-- docs/62 §3.1 L2：挂起帧一次性认领（at-most-once；D19/D20/D27/D31/D32 不解除）。
--   实测成因：第二个进程启动时 recover_pending（api/main.py:154）会为第一个进程仍持有的
--   活帧重建 pending 并起 _resume_run 续跑线程，一次人工审批可让下游节点执行两遍、
--   且两侧分支相反（通过×1／拒绝×1）。证据：scripts/dev/multi_instance_resume_recon.py。
-- 语义：resumed_at 为 NULL ＝ 还没人越过这个挂起点；任一进程要沿边继续执行下游，必须先把它
--   从 NULL 原子翻成 CURRENT_TIMESTAMP（rowcount==1 才算赢），输家停止驱动、不写 run 终态。
-- 时钟只用 DB（CURRENT_TIMESTAMP），不用应用侧 now_iso()——微秒省略与跨进程偏斜都会咬人
--   （教训同 docs/61 注记 ⑥）。
-- 类型跟**表内**口径而非仓库主约定：本表 deadline_at 已是 TIMESTAMPTZ（002_storage.sql:55），
--   该列要参与比较判定，故同型；created_at 是 TEXT 属本表历史异类，不跟（docs/62 §3.1）。
-- 不建新索引：认领只按 resume_token 单行命中，而它是本表主键（002_storage.sql:49）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行；新装库全量建表见 002_storage.sql。
ALTER TABLE interruptions ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMPTZ;
ALTER TABLE interruptions ADD COLUMN IF NOT EXISTS resumed_by TEXT;

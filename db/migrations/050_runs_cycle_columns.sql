-- 打包 BN（docs/130）：D69 运行期自走循环——runs 表补两列（圈归属）。
-- 一圈＝一次真实 run（同 runs 表既有语义），故不建新表：加两列即可回答
-- 「这条 run 是第几圈、属于哪条圈会话」，且旧行按缺省值(0/'')继续代表
-- 「非圈运行」——**存量行为与投影逐字不变**（打包 BN 的启用面缺省关闭，C-D2）。
--   cycle_seq       INTEGER  该 run 是第几圈（0＝非圈运行）
--   cycle_group_id  TEXT     圈会话 id（''＝非圈运行）
-- 圈会话的活体状态（累计 token/墙钟/是否被停）只在进程内（cycles/state.py，
-- 单副本语义，照 docs/62；重启即结束圈、不自动续跑——与 D44 的取舍同款）。
-- 索引 (tenant_id, cycle_group_id) 供「这条会话跑过哪些圈」按会话查。
-- 本迁移只加列不加约束：旧行不需要回填（0/'' 已是正确语义），全脚本幂等可重入。

ALTER TABLE runs ADD COLUMN IF NOT EXISTS cycle_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS cycle_group_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_runs_cycle_group
    ON runs (tenant_id, cycle_group_id);

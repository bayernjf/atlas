-- C（docs/27 §2.4）：录制用例回放冻结时钟锚点 recorded_at。
-- 新用例入库即写录制时刻（ISO TEXT，UTC，与 created_at 同刻）；
-- 回放（单用例 replay / 发布门禁 gate）把 run_graph 时钟冻结到该时刻，
-- 使 today()/now() 等非确定条件在录制与回放间确定可比。
-- 历史行回填为 created_at（语义等价：用例记录时刻）。
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS recorded_at TEXT;
UPDATE recordings SET recorded_at = created_at WHERE recorded_at IS NULL;

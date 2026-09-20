-- 工程内可闭环缓做项打包（docs/28）：
-- 批 1 D26①：recordings 富字段持久化缺口修复。
--   graph_id  所属图 id（进程内签名早有，PG 档「录为用例」因缺列 500）；
--   subgraphs 录制时递归冻结的 subgraph 引用快照（对象 map，key＝节点 config.graphId
--             引用原文含 @N 钉版；单用例冻结回放「内联优先」解析）。
-- 批 3 D28⑧：monitoring_runs 工具调用埋点（tool_metric 三出口收集，见 docs/28 §4）。
-- 均 ADD COLUMN IF NOT EXISTS + 历史行幂等回填；无迁移运行器，手动应用：
--   docker exec -i atlas-pg psql -U atlas -d atlas < db/migrations/008_*.sql

ALTER TABLE recordings ADD COLUMN IF NOT EXISTS graph_id TEXT NOT NULL DEFAULT '';
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS subgraphs JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE recordings SET graph_id = '' WHERE graph_id IS NULL;
UPDATE recordings SET subgraphs = '{}'::jsonb WHERE subgraphs IS NULL;

ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb;
UPDATE monitoring_runs SET tool_calls = '[]'::jsonb WHERE tool_calls IS NULL;

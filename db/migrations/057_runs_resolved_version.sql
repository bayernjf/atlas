-- O-2 修复（2026-10-19）：runs 投影暴露 resolved_version——入站路由（带 event）经
--   RoutingStore 解析钉住的发布版在 begin 时落库，/api/runs 列表与详情投影即可外部
--   断言灰度分流/实验分桶（docs/135 §6.5 O-2 验证方法缺口）。
--   键形态：INTEGER 可空列，缺省 NULL＝"非入站路由运行"（无 event 手动/草稿 run，
--   与 memory 档投影逐键对齐，零回归）。幂等：已存在时不动。
ALTER TABLE runs ADD COLUMN IF NOT EXISTS resolved_version INTEGER;

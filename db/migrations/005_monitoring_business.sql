-- M9 批 3：monitoring_runs 增 business（业务结果提取，03 `business_metrics`）。
-- JSONB：{auto_refunded, manual_escalated, refunded_amount, expected_amount, amount_diff}，
-- 无业务结果的运行为 NULL。002 的 CREATE TABLE 已含本列（新装库直接到位）；
-- 本迁移供已建旧表的库升级（迁移由集成测试按序号 glob 拾取，生产手动执行）。
ALTER TABLE monitoring_runs ADD COLUMN IF NOT EXISTS business JSONB;

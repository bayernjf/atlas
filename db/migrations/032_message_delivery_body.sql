-- 打包 U：出站消息 DLQ——失败投递可查可重放（docs/82；D24 余部切片）。
--   实测成因：message_deliveries（迁移 025）记录每次投递成败，但失败行不存消息正文，
--   通知失败后内容不可恢复、运营无补放入口。
-- 口径：body 可空——历史失败行落 NULL，重放明确失败（DLQ_BODY_UNAVAILABLE），
--   不猜测、不用 subject 顶替；内容本就不存在，迁移不回填。
-- 长度：body 不截断，是重放唯一权威副本（subject 有 100、error 有 300 的日志上限，body 没有）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行；新装库由迁移 CLI 顺序应用 001→最新，
--   002_storage.sql 为冻结基线，新列不回登（031 rollout_states 同口径）。
ALTER TABLE message_deliveries ADD COLUMN IF NOT EXISTS body TEXT;
COMMENT ON COLUMN message_deliveries.body IS '出站消息正文（重放权威副本，不截断）；2026-09-29 前历史行为 NULL，不可重放（打包 U/docs/82）';

-- 打包 W：录制用例补表达式 RNG 种子列（docs/84 D-3；D15 余部切片）。
--   背景：random()/randint()/uuid() 的随机序列以运行级种子复现，种子是录制事实，
--   回放 seed_anchor 据此钉住随机分支。
-- 口径：rng_seed 可空——2026-09-29 前历史用例落 NULL，回放回退新种子并在报告中
--   注明漂移风险，不猜测、不伪造锚点（与 recorded_at 回退口径一致）。
-- 类型：BIGINT——种子为 63-bit 非负整数（secrets.randbits(63)）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行；新装库由迁移 CLI 顺序应用 001→最新，
--   002_storage.sql 为冻结基线，新列不回登（032 message_delivery_body 同口径）。
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS rng_seed BIGINT;
COMMENT ON COLUMN recordings.rng_seed IS '录制运行的表达式 RNG 种子（random/randint/uuid 回放锚点）；2026-09-29 前历史用例为 NULL，回放使用新种子并出 note（打包 W/docs/84）';

-- 打包 AV（docs/95）：首登强制改密的**服务端强制位**。
--   password_rotated_at 为 NULL ＝ 该账号的口令从未经本人之手（prod 引导播种、admin 建号、
--   admin 重置都算），非 NULL 是本人改密成功的 UTC ISO-8601 时刻。判定读这一列而不是现场
--   比对引导口令哈希：bcrypt 单次校验 ≈100ms，可以进登录、不能进每个请求（docs/95 §1.1）。
--   强制点在 iam/deps.require()，只在演示面之外生效（demo_surface_enabled() 为真即豁免），
--   所以 dev/test 与 prod＋ATLAS_ENABLE_DEMO_MOCK=1 的账号一律不受约束（docs/95 §2 D-4）。
--   幂等：ADD COLUMN IF NOT EXISTS，可重复执行；不改冻结的 010（iam_users 原表），
--   新装库由迁移运行器按 001→最新顺序应用，故 NULL 也是新行的自然缺省。
ALTER TABLE iam_users ADD COLUMN IF NOT EXISTS password_rotated_at TEXT;

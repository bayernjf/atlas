-- docs/08 打包 ZR R1（2026-10-03；P2 工程债，029 注释点名异类归队）：
--   interruptions.created_at 由 TEXT 改为 TIMESTAMPTZ，并补 (tenant_id, created_at) 复合索引。
--   实测成因（勘察实证）：
--     * 写入侧走 DB 时钟 CURRENT_TIMESTAMP（recovery.py:74 的 INSERT 直插），TEXT 列接收后
--       按会话 DateStyle 落成空格分隔文本，与其它路径的 ISO-8601 串格式混排时，
--       ORDER BY created_at（recovery.py:124/137）的**字典序**即错序（空格 0x20 < 'T' 0x54）。
--     * 清理侧被强转 created_at::timestamptz（pg.py:110）才能与 cutoff 比较——类型不对，
--       每次清理都要强转、索引用不上。
--     * 本表 deadline_at 已是 TIMESTAMPTZ（002_storage.sql:55），created_at 归队（029 注释
--       「created_at 是 TEXT 属本表历史异类」；docs/62 §3.1）。
--   幂等：DO 块判型——仅当列仍是 text 才 ALTER（ALTER COLUMN TYPE 无 IF EXISTS 语法），
--   USING created_at::timestamptz 把存量文本就地转换（ISO-8601 与 CURRENT_TIMESTAMP 文本
--   PG 均可解析）；CREATE INDEX IF NOT EXISTS 可重复。重跑时判型不命中、索引原样存在。
--   不改 db/migrations/002_storage.sql：冻结基线（030 起即未登记进 002，037 同惯例）；
--   新装库由迁移运行器顺序应用 001→最新，空表转换零成本、终态同型。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'interruptions'
          AND column_name = 'created_at'
          AND data_type = 'text'
    ) THEN
        ALTER TABLE interruptions
            ALTER COLUMN created_at TYPE TIMESTAMPTZ
            USING created_at::timestamptz;
    END IF;
END $$;

-- 租户内查询（recovery.py:137 WHERE tenant_id=:t ORDER BY created_at）与 retention 清理
-- （pg.py:110 按 created_at 截止）共用：tenant 前缀 + created_at 排序键。
CREATE INDEX IF NOT EXISTS idx_interruptions_tenant_created
    ON interruptions (tenant_id, created_at);

COMMENT ON COLUMN interruptions.created_at IS
    '帧创建时刻（DB 时钟 CURRENT_TIMESTAMP）；迁移 038 由 TEXT 归队 TIMESTAMPTZ（docs/08 打包 ZR）';

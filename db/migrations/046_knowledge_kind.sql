-- 打包 AA（docs/108）：知识库 / RAG MVP——memory_items.kind 放行 knowledge。
-- 006 的 CHECK 只允许 ('fact','preference')，知识库复用统一表以 kind 区分（docs/108 §2.1）。
-- 知识子类放 meta.category（faq/sop/manual/rule/case），由 Python 端 models 校验，不在 DDL 约束
-- （meta 是 JSONB，PG 无简易列级 CHECK 且分类白名单属于领域规则，收敛在 validate 层单一事实源）。
-- 幂等：若约束不存在则直接建；存在则无条件重建为三值（重复执行收敛到同一形状）。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'memory_items_kind_check'
    ) THEN
        ALTER TABLE memory_items DROP CONSTRAINT memory_items_kind_check;
    END IF;
END $$;

ALTER TABLE memory_items ADD CONSTRAINT memory_items_kind_check
    CHECK (kind IN ('fact', 'preference', 'knowledge'));

-- 迁移 009：迁移运行器登记表（docs/30 §3，ADR T24）。
-- 运行器自身亦会 ensure 此表（双保险）；本表仅承载版本前进，不做 checksum/down。

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

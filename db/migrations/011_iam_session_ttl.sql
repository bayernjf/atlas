-- 迁移 011：会话绝对 TTL（docs/31 §4，ADR T25）。
-- 存量行按 issued_at + 12h 回填；issued_at 非 ISO 形态的行保持 NULL（NULL 视为不过期）。

ALTER TABLE iam_sessions ADD COLUMN IF NOT EXISTS expires_at TEXT;

UPDATE iam_sessions
SET expires_at = (issued_at::timestamptz + INTERVAL '12 hours')::text
WHERE expires_at IS NULL
  AND issued_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)$';

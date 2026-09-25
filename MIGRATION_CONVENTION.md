# Migration Convention

Convention for database migration files in this repository.

## 1. Directory

- **Other projects** (self-hosted Postgres, SQLite, etc.) → `db/migrations/`

Atlas 主库为 PostgreSQL（+pgvector），所有新迁移进入本仓库的 `db/migrations/` 目录。

## 2. File naming

```
NNN_verb_snake_case.sql
```

- `NNN` — three digits, starting at `001`, strictly incremental and globally unique. **No duplicates, no skips.**
- Description is English `snake_case`, verb first: `create_`, `add_`, `alter_`, `fix_`, `drop_`.
- One file = one change. Split multi-step changes into sequential files (e.g. `001_...`, `002_...`).

Examples:

```
001_create_profiles.sql
002_add_theme_preference.sql
003_fix_updated_at_trigger.sql
```

## 3. Header comment — **revised 2026-09-25 to a rule that is actually enforceable**

**Why this changed.** The block in §3.1 has been the written rule since 2026-09-13 (`e9c2b73`) and was followed
by exactly **6 of 29** migrations (`001`, `012`–`016`). The runner (`src/atlas/storage/migrations.py`) never
parsed it: versions come from the filename glob plus `schema_migrations` bookkeeping, with no content checksum.
So the rule had no teeth, and every batch from `017` on settled on a leading provenance comment instead.
Retrofitting the 23 non-conforming files would rewrite already-applied migration content that the runner cannot
verify anyway — strictly worse than admitting the old rule was decorative.

**Required now — and checked by `tests/test_migration_convention.py`:**

1. The file name is the version authority: `NNN_verb_snake_case.sql` (§2), strictly incremental from `001`,
   no duplicates and no gaps.
2. The first non-empty line **must be a `--` comment**, so a file always reads as provenance before SQL.

**Expected, not machine-enforced:** the leading comment should name its source contract (e.g. `docs/62 §3.1`,
or `docs/40, hardening batch`). Chinese or English is fine; the runner executes the file through
`cursor.execute(sql)` and comment lines are inert.

### 3.1 Superseded text kept for the record (2026-09-13 – 2026-09-25)

Every migration file MUST start with the following header. All comments are in English.

```sql
-- =====================================================
-- Migration 001: Add theme preference to profiles
-- File: 001_add_theme_preference.sql
-- Date: 2026-09-13 10:30
-- Depends on: 0XX_xxx.sql
-- Ref: https://prd/requirement/42
-- Run: psql -d atlas -f db/migrations/001_xxx.sql
-- =====================================================
-- Note: why this migration exists, background, impact.
-- -----------------------------------------------------
```

| Field | Required | Format |
|---|---|---|
| `-- Migration NNN:` | yes | `Migration 001: One-line title` |
| `-- File:` | yes | file name, must match the actual file |
| `-- Date:` | yes | `YYYY-MM-DD HH:mm`, 24-hour, minute precision |
| `-- Depends on:` | no | previous migration this one relies on; omit if none |
| `-- Ref:` | no | PRD / requirement / issue link |
| `-- Run:` | no | how to execute (psql, alembic, etc.) |
| `-- Note:` | recommended | background, reason, impact; multi-line allowed |


## 4. SQL style

- **Idempotent** — use `IF NOT EXISTS` / `IF EXISTS` / `DROP ... IF EXISTS` so a migration can be re-run safely.
- Add `COMMENT ON` for every new column and table.
- Identifiers in `snake_case`; keep statements simple and readable.
- 向量列使用 `pgvector` 类型时注明 embedding 维度与索引（HNSW/IVFFlat），与 Schema 契约（docs/03）保持一致。

## 5. Example

```sql
-- docs/24 §2.3 (M5b): persist interruption frames so a suspended run survives a restart.
--   One row per resume_token; payload holds the exact state the loader needs to continue
--   from the suspended node. deadline_at is an absolute instant so recovery waits only the
--   remaining time. Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS interruptions (
    resume_token TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    deadline_at TIMESTAMPTZ,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interruptions_tenant ON interruptions (tenant_id);
```

Two things the example shows: the first line names the contract the migration serves, and every
statement is re-runnable (`IF NOT EXISTS`) because `apply_migrations.py` may meet a half-built database.
Columns added later live in their own file (e.g. `029_frame_resume_claim.sql` adds `resumed_at` /
`resumed_by` to this same table) — never edit an applied migration to grow a table.

## 6. Do / Don't

| Do | Don't |
|---|---|
| Keep numbering strictly incremental | Reuse a number (e.g. two `002_` files) |
| Set `-- Date:` at creation, keep it unchanged | Edit the original date later |
| Make every statement idempotent | Assume the migration runs only once on a fresh DB |
| One change per file | Bundle unrelated changes into one file |
| Flag obsoletes in place or drop in a new file | Delete / rewrite history files |
| Keep schema in sync with docs/03 contract index | Let schema drift from docs |

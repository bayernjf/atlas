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

## 3. Header comment (required)

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

Rules:

- `-- Date:` is set **once** when the file is created and is never edited afterwards.
- Later amendments are appended in the `-- Note:` section as `-- Updated: <YYYY-MM-DD HH:mm> <reason>`.
- Obsolete migrations: keep the history file untouched; create a separate drop migration. If the file itself must be flagged, add `-- Obsoleted: YYYY-MM-DD HH:mm` at the top of the header.

## 4. SQL style

- **Idempotent** — use `IF NOT EXISTS` / `IF EXISTS` / `DROP ... IF EXISTS` so a migration can be re-run safely.
- Add `COMMENT ON` for every new column and table.
- Identifiers in `snake_case`; keep statements simple and readable.
- 向量列使用 `pgvector` 类型时注明 embedding 维度与索引（HNSW/IVFFlat），与 Schema 契约（docs/03）保持一致。

## 5. Example

```sql
-- =====================================================
-- Migration 001: Create graphs table
-- File: 001_create_graphs.sql
-- Date: 2026-09-13 10:30
-- Depends on: none
-- Ref: https://prd/requirement/1
-- Run: psql -d atlas -f db/migrations/001_create_graphs.sql
-- =====================================================
-- Note: Persists executable causal graphs (Harness/Graph/Loop
--       core entity). Aligns with docs/03 schema index.
-- -----------------------------------------------------

CREATE TABLE IF NOT EXISTS graphs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  definition  JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  graphs IS 'Executable causal graph definitions';
COMMENT ON COLUMN graphs.definition IS 'Graph DSL (nodes/edges) JSON payload';
```

## 6. Do / Don't

| Do | Don't |
|---|---|
| Keep numbering strictly incremental | Reuse a number (e.g. two `002_` files) |
| Set `-- Date:` at creation, keep it unchanged | Edit the original date later |
| Make every statement idempotent | Assume the migration runs only once on a fresh DB |
| One change per file | Bundle unrelated changes into one file |
| Flag obsoletes in place or drop in a new file | Delete / rewrite history files |
| Keep schema in sync with docs/03 contract index | Let schema drift from docs |

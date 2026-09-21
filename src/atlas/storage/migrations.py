"""迁移运行器（docs/30 §3，ADR T24）。

手写有序幂等 SQL：db/migrations/*.sql 按文件名顺序应用，每个文件在单事务内
执行，成功后登记到 schema_migrations；失败回滚不登记。不引入 Alembic。
CLI 薄封装在 scripts/ops/apply_migrations.py。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, text

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def default_migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def list_migration_versions(migrations_dir: Path) -> list[str]:
    return sorted(path.name for path in migrations_dir.glob("*.sql"))


def applied_versions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in rows}


def ensure_schema_migrations(engine: Engine) -> None:
    """建登记表（迁移 009 亦建表，双保险；幂等）。"""
    with engine.begin() as conn:
        conn.execute(text(SCHEMA_MIGRATIONS_DDL))


def apply_pending(
    engine: Engine,
    migrations_dir: Path | None = None,
    *,
    mark_existing: bool = False,
) -> list[str]:
    """应用未登记迁移，返回本次处理的版本列表。

    mark_existing=True：把未登记版本直接登记为已应用而不执行 SQL
    （供手动应用过 001–008 的旧库首次纳管用）。
    """
    migrations_dir = migrations_dir or default_migrations_dir()
    ensure_schema_migrations(engine)
    already = applied_versions(engine)
    pending = [v for v in list_migration_versions(migrations_dir) if v not in already]

    processed: list[str] = []
    for version in pending:
        sql = (migrations_dir / version).read_text(encoding="utf-8")
        with engine.begin() as conn:
            if not mark_existing:
                dbapi_conn = conn.connection.dbapi_connection
                with dbapi_conn.cursor() as cursor:
                    cursor.execute(sql)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, :applied_at)"
                ),
                {
                    "version": version,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        processed.append(version)
    return processed

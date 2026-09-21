# -*- coding: utf-8 -*-
"""迁移运行器 PG 集成测试（docs/30 §7，U224/U225）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_migrations_pg_integration.py

U224：临时空库 apply 001–009 全过，二次运行零变更；
U225：旧库（未登记但迁移 SQL 已手动应用）--mark-existing 全部登记且不重跑。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from atlas.storage.migrations import (
    applied_versions,
    apply_pending,
    default_migrations_dir,
)

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run migration PG integration",
    ),
]


@pytest.fixture()
def temp_database_url():
    base = make_url(DATABASE_URL)
    admin_url = base.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"atlas_migtest_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name}"))
        admin_engine.dispose()


def test_apply_all_on_fresh_db_is_complete_and_idempotent(temp_database_url: str) -> None:
    engine = create_engine(temp_database_url)
    migrations_dir = default_migrations_dir()
    all_versions = sorted(p.name for p in migrations_dir.glob("*.sql"))

    processed = apply_pending(engine, migrations_dir)
    assert processed == all_versions
    assert applied_versions(engine) == set(all_versions)

    second = apply_pending(engine, migrations_dir)
    assert second == []

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM schema_migrations")).scalar_one() == len(
            all_versions
        )
    engine.dispose()


def test_mark_existing_registers_without_rerun(temp_database_url: str) -> None:
    engine = create_engine(temp_database_url)
    migrations_dir = default_migrations_dir()
    all_versions = sorted(p.name for p in migrations_dir.glob("*.sql"))

    processed = apply_pending(engine, migrations_dir, mark_existing=True)
    assert processed == all_versions
    # 登记但未执行 SQL：业务表不应存在（schema_migrations 除外）。
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
    assert tables == {"schema_migrations"}
    engine.dispose()

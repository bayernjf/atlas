"""PostgreSQL + pgvector 集成测试（13 文档 §3 集成层，标记 integration）。

默认跳过；显式开启时连真实库：
    DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run DB integration tests",
    ),
]


@pytest.fixture(scope="module")
def engine():
    from atlas.memory.database import create_database_engine

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    yield engine
    engine.dispose()


def test_ping(engine):
    from atlas.memory.database import ping

    assert ping(engine) is True


def test_pgvector_extension_installed(engine):
    from atlas.memory.database import pgvector_version

    version = pgvector_version(engine)
    assert version is not None
    assert tuple(int(part) for part in version.split(".")[:2]) >= (0, 5)


def test_vector_type_roundtrip_and_similarity(engine):
    """vector 类型可用 + 余弦距离排序正确（临时表，测试后自动清理）。"""
    with engine.begin() as conn:
        conn.execute(text("CREATE TEMP TABLE v_smoke (id serial PRIMARY KEY, embedding vector(3))"))
        conn.execute(
            text("INSERT INTO v_smoke (embedding) VALUES (:a), (:b), (:c)"),
            {
                "a": "[1,0,0]",
                "b": "[0,1,0]",
                "c": "[0.9,0.1,0]",
            },
        )
        rows = conn.execute(
            text(
                "SELECT id FROM v_smoke ORDER BY embedding <=> CAST('[1,0,0]' AS vector)"
            )
        ).all()
        ordered = [row[0] for row in rows]

    assert ordered == [1, 3, 2]

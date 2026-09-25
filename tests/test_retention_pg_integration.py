"""docs/65 K-A retention PG 集成测试（integration 标记）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_retention_pg_integration.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run retention PG integration",
    ),
]


def _run_migration(engine) -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def _cleanup(engine) -> None:
    """清空全部用户表（动态枚举，防表名漂移）。"""
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).all()
        for (table,) in rows:
            conn.execute(text(f'DELETE FROM "{table}"'))


@pytest.fixture(scope="module")
def backend():
    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine)
    _cleanup(engine)
    yield PgBackend(engine)
    _cleanup(engine)
    engine.dispose()


def _now_iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_prune_audit_events(backend) -> None:
    engine = backend.engine
    with engine.begin() as conn:
        for i, days in enumerate((200, 10)):  # 旧删 / 新留
            conn.execute(
                text(
                    "INSERT INTO audit_events (tenant_id, id, seq, actor, action, status_code, path, at) "
                    "VALUES (:t, :id, :seq, :actor, :action, 200, '/api/x', :at)"
                ),
                {"t": "rt", "id": f"aud-{days}", "seq": i + 1, "actor": "u", "action": "test", "at": _now_iso(days)},
            )
    deleted = backend.prune_expired({"audit_events": _now_iso(90)})
    assert deleted["audit_events"] == 1
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT id FROM audit_events WHERE tenant_id='rt'")).all()
    assert [r[0] for r in remaining] == ["aud-10"]


def test_prune_runs_keeps_suspended(backend) -> None:
    engine = backend.engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (id, tenant_id, graph_id, status, started_at, finished_at) "
                "VALUES ('r-fin', 'rt', 'g', 'completed', :s, :f)"
            ),
            {"s": _now_iso(200), "f": _now_iso(200)},
        )
        conn.execute(
            text(
                "INSERT INTO runs (id, tenant_id, graph_id, status, started_at, suspended_at, finished_at) "
                "VALUES ('r-susp', 'rt', 'g', 'suspended', :s, :sus, NULL)"
            ),
            {"s": _now_iso(200), "sus": _now_iso(200)},
        )
    deleted = backend.prune_expired({"runs": _now_iso(90)})
    assert deleted["runs"] == 1
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT id FROM runs WHERE tenant_id='rt'")).all()
    assert [r[0] for r in remaining] == ["r-susp"]


def test_prune_interruptions_keeps_unclaimed(backend) -> None:
    engine = backend.engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO interruptions (resume_token, tenant_id, run_id, node_id, kind, payload, created_at, resumed_at) "
                "VALUES (:tok, 'rt', 'r', 'n', 'wait', '{}'::jsonb, :created, :resumed)"
            ),
            {"tok": "i-claimed", "created": _now_iso(200), "resumed": "2026-09-01T00:00:00+00:00"},
        )
        conn.execute(
            text(
                "INSERT INTO interruptions (resume_token, tenant_id, run_id, node_id, kind, payload, created_at, resumed_at) "
                "VALUES (:tok, 'rt', 'r', 'n', 'wait', '{}'::jsonb, :created, NULL)"
            ),
            {"tok": "i-unclaimed", "created": _now_iso(200)},
        )
    deleted = backend.prune_expired({"interruptions": _now_iso(30)})
    assert deleted["interruptions"] == 1
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT resume_token FROM interruptions WHERE tenant_id='rt'")).all()
    assert [r[0] for r in remaining] == ["i-unclaimed"]


def test_prune_openapi_imports_and_message_deliveries(backend) -> None:
    engine = backend.engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO openapi_imports (id, tenant_id, seq, title, base_url, created_at, operations) "
                "VALUES ('o-old', 'rt', 1, 't', 'https://x', :c, '{}'::jsonb)"
            ),
            {"c": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()},
        )
        conn.execute(
            text(
                "INSERT INTO message_deliveries (tenant_id, id, seq, channel, to_targets, subject, status, attempts, elapsed_ms, sent_at) "
                "VALUES ('rt', 'm-old', 1, 'webhook', '[]'::jsonb, '', 'sent', 1, 12, :s)"
            ),
            {"s": datetime.now(timezone.utc) - timedelta(days=60)},
        )
    deleted = backend.prune_expired(
        {"openapi_imports": _now_iso(365), "message_deliveries": _now_iso(30)}
    )
    assert deleted["openapi_imports"] == 1
    assert deleted["message_deliveries"] == 1


def test_prune_session_sweep(backend) -> None:
    engine = backend.engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO iam_sessions (token, tenant_id, username, role, issued_at, expires_at) "
                "VALUES ('s-exp', 'rt', 'u', 'viewer', :i, :e)"
            ),
            {"i": _now_iso(200), "e": _now_iso(2)},
        )
        conn.execute(
            text(
                "INSERT INTO iam_sessions (token, tenant_id, username, role, issued_at, expires_at) "
                "VALUES ('s-keep', 'rt', 'u', 'viewer', :i, :e)"
            ),
            {"i": _now_iso(1), "e": _now_iso(-1)},  # 未来过期 → 留
        )
        conn.execute(
            text(
                "INSERT INTO iam_sessions (token, tenant_id, username, role, issued_at, expires_at) "
                "VALUES ('s-null', 'rt', 'u', 'viewer', :i, NULL)"
            ),
            {"i": _now_iso(200)},  # NULL 视为不过期 → 留
        )
    deleted = backend.prune_expired({}, session_sweep=True)
    assert deleted["iam_sessions"] == 1
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT token FROM iam_sessions WHERE tenant_id='rt'")).all()
    assert sorted(r[0] for r in remaining) == ["s-keep", "s-null"]


def test_prune_unknown_and_none_ignored(backend) -> None:
    deleted = backend.prune_expired({"no_such_table": _now_iso(1), "audit_events": None})
    assert "no_such_table" not in deleted
    assert "audit_events" not in deleted

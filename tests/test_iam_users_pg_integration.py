"""U232 账号 PG 集成（integration 标记，ADR T25，docs/31 §2/§7）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_iam_users_pg_integration.py
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run iam PG integration",
    ),
]


def _run_migration(engine, name: str) -> None:
    path = Path(__file__).resolve().parents[1] / "db" / "migrations" / name
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


@pytest.fixture(scope="module")
def backend():
    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine, "010_iam_users.sql")
    _run_migration(engine, "011_iam_session_ttl.sql")
    yield PgBackend(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM iam_users WHERE tenant_id LIKE 'iutest%'"))
    engine.dispose()


def test_seed_is_idempotent_and_password_verifies(backend) -> None:
    users = backend.user_store()
    first = users.seed()
    assert users.seed() == 0
    assert first in (0, 4)
    account = users.get("t1", "admin-a")
    assert account is not None
    from atlas.iam.passwords import verify_password

    assert verify_password("admin123", account.password_hash)


def test_create_persists_across_engines_and_duplicate_rejected(backend) -> None:
    from atlas.iam.accounts import UserExists
    from atlas.iam.passwords import verify_password
    from atlas.iam.principals import Role

    tenant = f"iutest-{uuid.uuid4().hex[:8]}"
    username = f"iu-{uuid.uuid4().hex[:8]}"
    users = backend.user_store()
    account = users.create(
        tenant_id=tenant,
        username=username,
        password="persisted-pw",
        display_name="集成用户",
        role=Role.OPERATOR,
    )
    assert verify_password("persisted-pw", account.password_hash)

    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    fresh = PgBackend(create_database_engine(DATABASE_URL, pool_size=1))
    fetched = fresh.user_store().get_by_username(username)
    assert fetched is not None
    assert fetched.tenant_id == tenant
    assert fetched.display_name == "集成用户"
    fresh.engine.dispose()

    with pytest.raises(UserExists):
        users.create(
            tenant_id=tenant,
            username=username,
            password="other-pw",
            display_name="重复",
            role=Role.VIEWER,
        )


def test_disable_and_reset_password_revoke_pg_sessions(backend) -> None:
    from atlas.iam.principals import Principal, Role

    tenant = f"iutest-{uuid.uuid4().hex[:8]}"
    username = f"iu-{uuid.uuid4().hex[:8]}"
    users = backend.user_store()
    users.create(
        tenant_id=tenant,
        username=username,
        password="initial-pw",
        display_name="吊销用户",
        role=Role.VIEWER,
    )
    sessions = backend.session_store()
    principal = Principal(
        tenant_id=tenant,
        tenant_name=tenant,
        username=username,
        display_name="吊销用户",
        role=Role.VIEWER,
    )
    token = sessions.issue(principal)
    assert sessions.principal_for_token(token) is not None

    users.set_password(tenant, username, "rotated-pw")
    assert sessions.principal_for_token(token) is None
    account = users.get(tenant, username)
    from atlas.iam.passwords import verify_password

    assert account is not None
    assert verify_password("rotated-pw", account.password_hash)

    token2 = sessions.issue(principal)
    users.update(tenant, username, status="disabled")
    assert sessions.principal_for_token(token2) is None
    assert users.get(tenant, username).status == "disabled"


def test_list_is_scoped_per_tenant(backend) -> None:
    from atlas.iam.principals import Role

    tenant = f"iutest-{uuid.uuid4().hex[:8]}"
    users = backend.user_store()
    users.create(
        tenant_id=tenant,
        username=f"iu-{uuid.uuid4().hex[:8]}",
        password="scoped-pw",
        display_name="租户用户",
        role=Role.VIEWER,
    )
    accounts = users.list(tenant)
    assert len(accounts) == 1
    assert all(account.tenant_id == tenant for account in accounts)

# -*- coding: utf-8 -*-
"""渠道绑定 PG 集成测试（docs/38 §5；需 ATLAS_RUN_INTEGRATION=1 + DATABASE_URL）。

前置：002_storage.sql（storage_id_seq）与 015_channel_bindings.sql 已可应用。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

DATABASE_URL = os.environ.get("DATABASE_URL", "")
RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION", "") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run channel PG integration",
)


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
def setup():
    from atlas.channels.pg import PgChannelStore
    from atlas.memory.database import create_database_engine

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine, "015_channel_bindings.sql")
    yield engine, PgChannelStore
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM channel_bindings WHERE tenant_id LIKE 'chpit%'"))
    engine.dispose()


def _binding(conn_id="conn-pit-1", tenant="chpit-a"):
    from atlas.channels.base import ChannelBinding

    return ChannelBinding(
        id="", tenant_id=tenant, provider="shopify", connection_id=conn_id,
        config={"shop": "acme", "apiVersion": "2025-01"},
    )


def test_create_get_roundtrip(setup):
    engine, Store = setup
    store = Store(engine, "chpit-a")
    binding = _binding()
    store.create(binding)
    assert binding.id.startswith("ch-")
    loaded = store.get(binding.id)
    assert loaded is not None
    assert loaded.connection_id == "conn-pit-1"
    assert loaded.config == {"shop": "acme", "apiVersion": "2025-01"}
    assert loaded.created_at and loaded.updated_at


def test_list_tenant_scoped(setup):
    engine, Store = setup
    store_a = Store(engine, "chpit-a")
    store_b = Store(engine, "chpit-b")
    store_a.create(_binding(conn_id="conn-pit-a1"))
    store_b.create(_binding(conn_id="conn-pit-b1", tenant="chpit-b"))

    a_ids = {b.connection_id for b in store_a.list()}
    b_ids = {b.connection_id for b in store_b.list()}
    assert a_ids == {"conn-pit-1", "conn-pit-a1"}
    assert b_ids == {"conn-pit-b1"}
    # 跨租户取不到（不泄漏存在性）
    bid = store_b.list()[0].id
    assert store_a.get(bid) is None


def test_save_error_state_and_delete(setup):
    engine, Store = setup
    store = Store(engine, "chpit-a")
    binding = _binding(conn_id="conn-pit-save")
    store.create(binding)
    binding.status = "error"
    binding.last_error = "渠道鉴权失败"
    store.save(binding)

    reloaded = store.get(binding.id)
    assert reloaded.status == "error" and reloaded.last_error == "渠道鉴权失败"
    assert store.delete(binding.id) is True
    assert store.get(binding.id) is None
    assert store.delete(binding.id) is False


def test_duplicate_connection_rejected(setup):
    import pytest as _pt
    from sqlalchemy.exc import IntegrityError

    engine, Store = setup
    store = Store(engine, "chpit-a")
    store.create(_binding(conn_id="conn-pit-dup"))
    with _pt.raises(IntegrityError):
        store.create(_binding(conn_id="conn-pit-dup", tenant="chpit-b"))

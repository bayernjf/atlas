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
    _run_migration(engine, "016_channel_webhook_subscriptions.sql")
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


def test_webhook_subscriptions_roundtrip(setup):
    engine, Store = setup
    store = Store(engine, "chpit-a")
    binding = _binding(conn_id="conn-pit-wh1")
    binding.webhook_subscriptions = [
        {"topic": "orders/create", "graph_id": "graph-1", "enabled": True},
        {"topic": "refunds/create", "graph_id": "graph-2", "enabled": False},
    ]
    store.create(binding)

    loaded = store.get(binding.id)
    assert loaded.webhook_subscriptions == [
        {"topic": "orders/create", "graph_id": "graph-1", "enabled": True},
        {"topic": "refunds/create", "graph_id": "graph-2", "enabled": False},
    ]
    assert loaded.view()["webhookSubscriptions"] == [
        {"topic": "orders/create", "graphId": "graph-1", "enabled": True},
        {"topic": "refunds/create", "graphId": "graph-2", "enabled": False},
    ]
    # save 覆写
    loaded.webhook_subscriptions = [
        {"topic": "orders/updated", "graph_id": "graph-3", "enabled": True}
    ]
    store.save(loaded)
    assert store.get(binding.id).webhook_subscriptions == [
        {"topic": "orders/updated", "graph_id": "graph-3", "enabled": True}
    ]


def test_webhook_subscriptions_default_empty(setup):
    engine, Store = setup
    store = Store(engine, "chpit-a")
    binding = _binding(conn_id="conn-pit-wh2")
    store.create(binding)
    assert store.get(binding.id).webhook_subscriptions == []
    with engine.connect() as conn:
        raw = conn.execute(
            text("SELECT webhook_subscriptions FROM channel_bindings WHERE id = :id"),
            {"id": binding.id},
        ).scalar_one()
    assert raw == []


def test_webhook_subscriptions_tenant_scoped(setup):
    engine, Store = setup
    store_a = Store(engine, "chpit-a")
    store_b = Store(engine, "chpit-b")
    binding_a = _binding(conn_id="conn-pit-wha")
    binding_a.webhook_subscriptions = [
        {"topic": "orders/create", "graph_id": "graph-a", "enabled": True}
    ]
    store_a.create(binding_a)
    binding_b = _binding(conn_id="conn-pit-whb", tenant="chpit-b")
    binding_b.webhook_subscriptions = [
        {"topic": "orders/create", "graph_id": "graph-b", "enabled": True}
    ]
    store_b.create(binding_b)

    assert store_a.get(binding_a.id).webhook_subscriptions[0]["graph_id"] == "graph-a"
    assert store_b.get(binding_b.id).webhook_subscriptions[0]["graph_id"] == "graph-b"


def test_webhook_subscriptions_survive_reset(setup):
    # docs/39 §6：客户配置不随 demo reset 清除。完整 TenantRegistry.reset_tenant
    # 依赖全量表结构；缺失时跳过（半迁移测试库）。
    import pytest as _pt
    from sqlalchemy.exc import OperationalError

    engine, Store = setup
    store = Store(engine, "chpit-a")
    binding = _binding(conn_id="conn-pit-whr")
    binding.webhook_subscriptions = [
        {"topic": "orders/create", "graph_id": "graph-r", "enabled": True}
    ]
    store.create(binding)

    from atlas.iam.registry import TenantRegistry

    try:
        TenantRegistry().reset_tenant("chpit-a")
    except OperationalError:
        _pt.skip("tenant reset requires full schema in integration database")
    reloaded = store.get(binding.id)
    assert reloaded is not None
    assert reloaded.webhook_subscriptions == [
        {"topic": "orders/create", "graph_id": "graph-r", "enabled": True}
    ]

# -*- coding: utf-8 -*-
"""PgRoutingStore 集成（docs/81 §4 U951–U957；打包 T）。

跑法（与其余 PG 集成用例同）：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \\
    .venv/bin/pytest -m integration tests/test_routing_pg_integration.py

条款聚焦三件内存档给不了的事：跨连接/跨 engine 存活（U951/U952/U953/U956）、
跨租户隔离（U955）、行锁下并发计数不丢（U957）。语义同构部分仍由
tests/test_routing_store.py 守，这里不重复。
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from atlas.routing.models import (
    BucketRule,
    CanaryRule,
    FullRule,
    InternalRule,
    RolloutConfig,
    TriggerEvent,
)
from atlas.routing.pg_store import PgRoutingStore
from atlas.routing.store import RolloutError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run routing PG integration",
    ),
]

_G = "refund-flow"


def _config() -> RolloutConfig:
    return RolloutConfig(
        rules=[
            InternalRule(tenants=["t-internal"]),
            BucketRule(value=200, percent=100),
            CanaryRule(percent=100),  # 确定性：命中 canary；full 段是否生效由状态机决定
            FullRule(),
        ]
    )


def _ev(**payload) -> TriggerEvent:
    return TriggerEvent(channel="webhook", payload=payload)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
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
        with eng.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    yield eng
    eng.dispose()


@pytest.fixture()
def tenant(engine):
    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"roll-pg-{suffix}"
    yield tenant_id
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM rollout_states WHERE tenant_id LIKE :pattern"),
            {"pattern": f"roll-%-{suffix}"},
        )


# ---------- U951 configure / snapshot 跨连接 ----------

def test_u951_configure_persists_and_idle_snapshot_is_write_free(engine, tenant):
    store = PgRoutingStore(engine, tenant)
    state = store.configure(_G, _config())
    assert state.status == "idle"

    seen = PgRoutingStore(engine, tenant).snapshot(_G)
    assert seen.status == "idle"
    assert seen.config is not None
    assert seen.config.model_dump(mode="json") == _config().model_dump(mode="json")

    untouched = f"never-configured-{uuid.uuid4().hex[:6]}"
    fresh = PgRoutingStore(engine, tenant).snapshot(untouched)
    assert fresh.status == "idle"
    assert fresh.config is None
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM rollout_states WHERE tenant_id = :t AND graph_id = :g"),
            {"t": tenant, "g": untouched},
        ).scalar()
    assert rows == 0


# ---------- U952 状态机全序跨连接 ----------

def test_u952_state_sequence_survives_new_connections(engine, tenant):
    store = PgRoutingStore(engine, tenant)
    with pytest.raises(RolloutError):
        store.start(f"bare-{_G}", [3, 4])
    store.configure(_G, _config())
    with pytest.raises(RolloutError):
        store.start(_G, [4])

    state = store.start(_G, [3, 4])
    assert state.status == "canary"
    seen = PgRoutingStore(engine, tenant).snapshot(_G)
    assert (seen.status, seen.stable, seen.candidate) == ("canary", 3, 4)
    assert seen.started_at is not None

    with pytest.raises(RolloutError):
        store.start(_G, [5, 6])

    state = store.promote(_G)
    assert state.status == "full"
    seen = PgRoutingStore(engine, tenant).snapshot(_G)
    assert (seen.status, seen.stable, seen.candidate) == ("full", 3, 4)
    with pytest.raises(RolloutError):
        store.promote(_G)


# ---------- U953 rollback 持久与幂等 ----------

def test_u953_rollback_persists_and_is_idempotent(engine, tenant):
    graph = f"rb-{_G}"
    store = PgRoutingStore(engine, tenant)
    store.configure(graph, _config())
    with pytest.raises(RolloutError):
        store.rollback(graph)

    store.start(graph, [3, 4])
    state = store.rollback(graph, actor="ops-a", reason="gate breach")
    assert (state.status, state.rollback_actor, state.rollback_reason) == (
        "rolled_back", "ops-a", "gate breach",
    )
    assert state.rolled_back_at is not None

    seen = PgRoutingStore(engine, tenant).snapshot(graph)
    assert (seen.status, seen.rollback_actor, seen.rollback_reason) == (
        "rolled_back", "ops-a", "gate breach",
    )

    again = store.rollback(graph, actor="ops-b", reason="second attempt")
    assert (again.rollback_actor, again.rollback_reason, again.rolled_back_at) == (
        "ops-a", "gate breach", state.rolled_back_at,
    )


# ---------- U954 resolve 计数存活与语义 ----------

def test_u954_resolve_counters_survive_and_segments_hold(engine, tenant):
    store = PgRoutingStore(engine, tenant)
    store.configure(_G, _config())

    store.start(_G, [3, 4])
    version, segment = store.resolve(_G, tenant=tenant, event=_ev(order_id="o-1", amount=999))
    assert (version, segment) == (4, "canary")

    store.promote(_G)
    version, segment = store.resolve(_G, tenant=tenant, event=_ev(order_id="o-2", amount=999))
    assert (version, segment) == (4, "full")

    store.rollback(_G, reason="gate breach")
    version, segment = store.resolve(_G, tenant=tenant, event=_ev(order_id="o-3", amount=999))
    assert (version, segment) == (3, "stable")

    seen = PgRoutingStore(engine, tenant).snapshot(_G)
    assert seen.traffic["candidate"] == 2
    assert seen.traffic["stable"] == 1
    segments = seen.traffic["segments"]
    assert segments["canary"] == 1
    assert segments["full"] == 1
    assert seen.traffic["stable"] + seen.traffic["candidate"] == 3


# ---------- U955 跨租户隔离与 reset ----------

def test_u955_tenants_are_isolated_and_reset_scopes_per_tenant(engine, tenant):
    other = f"roll-pg-{uuid.uuid4().hex[:8]}"
    try:
        PgRoutingStore(engine, tenant).configure(_G, _config())
        PgRoutingStore(engine, tenant).start(_G, [3, 4])

        bystander = PgRoutingStore(engine, other).snapshot(_G)
        assert bystander.status == "idle"
        assert bystander.config is None

        PgRoutingStore(engine, other).reset()
        assert PgRoutingStore(engine, tenant).snapshot(_G).status == "canary"

        PgRoutingStore(engine, tenant).reset()
        assert PgRoutingStore(engine, tenant).snapshot(_G).status == "idle"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM rollout_states WHERE tenant_id = :t"), {"t": other})


# ---------- U956 真·跨 engine 存活 ----------

def test_u956_state_survives_engine_dispose_and_rebuild(engine, tenant):
    url = os.environ["DATABASE_URL"]
    ephemeral = create_engine(url)
    store = PgRoutingStore(ephemeral, tenant)
    store.configure(_G, _config())
    store.start(_G, [7, 8])
    store.resolve(_G, tenant=tenant, event=_ev(order_id="o-x", amount=10))
    ephemeral.dispose()

    rebuilt = create_engine(url)
    try:
        seen = PgRoutingStore(rebuilt, tenant).snapshot(_G)
        assert (seen.status, seen.stable, seen.candidate) == ("canary", 7, 8)
        assert seen.traffic["candidate"] == 1
    finally:
        rebuilt.dispose()


# ---------- U957 并发不丢数 ----------

def test_u957_concurrent_resolves_lose_no_counts(engine, tenant):
    graph = f"concurrent-{_G}"
    store = PgRoutingStore(engine, tenant)
    store.configure(graph, _config())
    store.start(graph, [3, 4])
    store.promote(graph)  # full：每请求都计 candidate，结果可精确求和

    calls = 40
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            local = PgRoutingStore(engine, tenant)
            local.resolve(graph, tenant=tenant, event=_ev(order_id=f"c-{index}", amount=10))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    seen = PgRoutingStore(engine, tenant).snapshot(graph)
    total = seen.traffic["stable"] + seen.traffic["candidate"]
    assert total == calls
    assert seen.traffic["candidate"] == calls
    assert seen.traffic["segments"]["full"] == calls

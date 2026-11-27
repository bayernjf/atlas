# -*- coding: utf-8 -*-
"""T4 任务总线 PG 档（迁移 060）：幂等、抢占、跨实例持久化。

跑法：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \\
    .venv/bin/pytest -m integration tests/test_task_store_pg_integration.py
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from atlas.coordination.pg_store import PgTaskStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run task bus PG integration",
    ),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    from atlas.storage.migrations import apply_pending

    apply_pending(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def store(engine):
    tenant = f"task-pg-{uuid.uuid4().hex[:8]}"
    pg = PgTaskStore(engine, tenant)
    pg.reset()
    yield pg
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM task_envelopes WHERE tenant_id=:tenant"), {"tenant": tenant}
        )


def _dispatch(store, key: str, *, conflict_key: str = "", priority: int = 0):
    return store.dispatch(
        run_id="run-1",
        idempotency_key=key,
        trace_id="trace-1",
        graph_version="g@1",
        type="node",
        assignee="bot-a",
        payload={"order": 1},
        deadline_ms=1000,
        conflict_key=conflict_key,
        priority=priority,
    )


def test_idempotency_returns_first_envelope(store):
    first, created = _dispatch(store, "idem-1")
    assert created is True
    second, created_again = _dispatch(store, "idem-1")
    assert created_again is False
    assert second.taskId == first.taskId
    assert second.state == "pending"


def test_priority_preempts_active_task(store):
    active, _ = _dispatch(store, "idem-active", conflict_key="ck", priority=1)
    store.accept(active.taskId)
    store.start(active.taskId)
    fresh, created = _dispatch(store, "idem-fresh", conflict_key="ck", priority=2)
    assert created is True
    old = store.get(active.taskId)
    assert old is not None and old.state == "failed"
    assert old.result["error"] == "TASK_PREEMPTED"
    assert old.result["preemptedBy"] == fresh.taskId
    assert store.get(fresh.taskId).state == "pending"


def test_lower_priority_reuses_active_task(store):
    active, _ = _dispatch(store, "idem-active", conflict_key="ck", priority=5)
    reused, created = _dispatch(store, "idem-lower", conflict_key="ck", priority=1)
    assert created is False
    assert reused.taskId == active.taskId


def test_persists_across_store_instances(store):
    envelope, _ = _dispatch(store, "idem-persist", conflict_key="ck", priority=1)
    store.accept(envelope.taskId)
    store.start(envelope.taskId)
    store.complete(envelope.taskId, {"ok": True})
    reopened = PgTaskStore(store._engine, store._tenant_id)
    loaded = reopened.get(envelope.taskId)
    assert loaded is not None
    assert loaded.state == "done"
    assert loaded.result == {"ok": True}
    assert [item.taskId for item in reopened.list()] == [envelope.taskId]


def test_reset_is_tenant_scoped(store):
    envelope, _ = _dispatch(store, "idem-reset")
    store.reset()
    assert store.get(envelope.taskId) is None
    assert store.list() == []

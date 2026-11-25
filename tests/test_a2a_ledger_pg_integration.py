# -*- coding: utf-8 -*-
"""A2A 任务台账 PG 档（迁移 061）：幂等键、跨重启 tasks/get。

跑法：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \\
    .venv/bin/pytest -m integration tests/test_a2a_ledger_pg_integration.py
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import create_engine, text

from atlas.a2a.ledger import PgA2ALedger
from atlas.a2a.auth import CallerIdentity
from atlas.iam.deps import tenant_registry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run A2A ledger PG integration",
    ),
]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    from atlas.storage.migrations import apply_pending

    apply_pending(eng)
    yield eng
    eng.dispose()


def _task(task_id: str, *, state: str = "completed") -> dict:
    return {
        "kind": "task",
        "id": task_id,
        "contextId": str(uuid.uuid4()),
        "status": {"state": state, "timestamp": "2026-11-26T00:00:00Z"},
        "artifacts": [{"artifactId": "a-1", "name": "plan", "parts": []}],
        "metadata": {"a2a": {"tenantId": "t-a2a-ledger"}},
    }


def test_put_get_and_idempotency(engine):
    ledger = PgA2ALedger(engine)
    task_id = f"a2a-{uuid.uuid4().hex}"
    tenant = f"a2a-ledger-{uuid.uuid4().hex[:8]}"
    task = _task(task_id)
    ledger.put(
        task,
        expires_at=time.time() + 60,
        tenant_id=tenant,
        idempotency_key="idem-1",
        fingerprint="fp-1",
    )

    loaded = ledger.get(task_id)
    assert loaded is not None
    assert loaded["id"] == task_id
    assert loaded["status"]["state"] == "completed"
    assert loaded["_tenant_id"] == tenant

    found = ledger.find_by_idempotency(tenant, "idem-1")
    assert found is not None
    assert found[0]["id"] == task_id
    assert found[1] == "fp-1"

    ledger.delete(task_id)
    assert ledger.get(task_id) is None


def test_prune_removes_expired_only(engine):
    ledger = PgA2ALedger(engine)
    expired_id = f"a2a-{uuid.uuid4().hex}"
    live_id = f"a2a-{uuid.uuid4().hex}"
    ledger.put(_task(expired_id), expires_at=time.time() - 1)
    ledger.put(_task(live_id), expires_at=time.time() + 60)
    deleted = ledger.prune(time.time())
    assert deleted >= 1
    assert ledger.get(expired_id) is None
    assert ledger.get(live_id) is not None
    ledger.delete(live_id)


def _graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a2a-ledger"},
            },
            {
                "id": "tool-1",
                "type": "tool_call",
                "name": "count",
                "config": {
                    "tool": "database/query",
                    "params": '{"sql": "SELECT COUNT(*) AS n FROM orders"}',
                },
            },
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def _send(skill: str, params: dict, caller: CallerIdentity) -> dict:
    message = {"role": "user", "parts": [{"kind": "data", "data": {"skill": skill, **params}}]}
    from atlas.a2a import rpc

    return rpc.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {"message": message}},
        caller=caller,
    )


def test_idempotent_replay_survives_memory_reset(engine, monkeypatch):
    from atlas.a2a import rpc

    ledger = PgA2ALedger(engine)
    monkeypatch.setattr(rpc, "_pg_ledger_instance", lambda: ledger)
    rpc.reset_for_tests()

    tenant = f"a2a-ledger-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    params = {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "same"}

    first = _send("run-graph", params, caller)["result"]
    run_id = first["artifacts"][0]["parts"][0]["data"]["runId"]
    assert first["status"]["state"] == "completed"

    rpc.reset_for_tests()  # 内存台账清空；PG 幂等键应能救回同一任务
    second = _send("run-graph", params, caller)["result"]
    assert second["id"] == first["id"]
    assert second["artifacts"][0]["parts"][0]["data"]["runId"] == run_id
    assert len([r for r in services.run_store.list(limit=100) if r["graphId"] == graph_id]) == 1

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM a2a_tasks WHERE tenant_id=:tenant"), {"tenant": tenant})
    rpc.reset_for_tests()


def test_tasks_cancel_survives_memory_reset(engine, monkeypatch):
    from atlas.a2a import rpc

    ledger = PgA2ALedger(engine)
    monkeypatch.setattr(rpc, "_pg_ledger_instance", lambda: ledger)
    rpc.reset_for_tests()

    tenant = f"a2a-ledger-{uuid.uuid4().hex[:8]}"
    task_id = f"a2a-{uuid.uuid4().hex}"
    ledger.put(
        _task(task_id, state="working"),
        expires_at=time.time() + 60,
        tenant_id=tenant,
        idempotency_key="cancel-1",
        fingerprint="fp-cancel",
    )

    rpc.reset_for_tests()  # 只清内存，等价进程重启；working 任务应能从 PG 读回
    canceled = rpc.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/cancel", "params": {"id": task_id}}
    )
    assert canceled["result"]["status"]["state"] == "canceled"

    loaded = ledger.get(task_id)
    assert loaded is not None
    assert loaded["status"]["state"] == "canceled"
    # cancel 的后续写穿没有带幂等键，COALESCE 必须保留原键，否则重启后幂等重放会丢。
    assert loaded["_idempotency_key"] == "cancel-1"

    again = rpc.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 4, "method": "tasks/cancel", "params": {"id": task_id}}
    )
    assert again["error"]["code"] == -32002  # TASK_NOT_CANCELABLE

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM a2a_tasks WHERE task_id=:task_id"), {"task_id": task_id})
    rpc.reset_for_tests()


def test_tasks_get_survives_memory_reset(engine, monkeypatch):
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "pg")
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])

    from atlas.a2a import rpc

    rpc.reset_for_tests()
    tenant = f"a2a-ledger-{uuid.uuid4().hex[:8]}"
    message = {
        "role": "user",
        "parts": [
            {
                "kind": "data",
                "data": {
                    "skill": "plan-approval-flow",
                    "tenant_id": tenant,
                    "flow_name": "ledger-pg",
                },
            }
        ],
    }
    resp = rpc.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {"message": message}}
    )
    task = resp["result"]
    assert task["status"]["state"] == "completed"

    rpc.reset_for_tests()  # 只清内存，等价进程重启；PG 行应还在
    got = rpc.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task["id"]}}
    )
    assert got["result"]["id"] == task["id"]
    assert got["result"]["status"]["state"] == "completed"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM a2a_tasks WHERE task_id=:task_id"), {"task_id": task["id"]})
    rpc.reset_for_tests()

"""A-02 真实 run 契约：租户绑定、幂等、冲突、交互图拒绝、HTTP 路径。"""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from atlas.a2a.bot_runtime import reset_for_tests as reset_bot_runtimes
from atlas.a2a.auth import CallerIdentity, authenticate
from atlas.a2a.rpc import handle_jsonrpc, reset_for_tests
from atlas.a2a.run import GraphRunResult
from atlas.api.main import app
from atlas.iam.agents_store import Agent
from atlas.iam.deps import tenant_registry


def _graph(*, interactive: bool = False) -> dict:
    nodes = [
        {
            "id": "trigger-1",
            "type": "trigger",
            "name": "start",
            "config": {"triggerType": "webhook", "webhookUrl": "/hooks/a2a"},
        },
    ]
    if interactive:
        nodes.append(
            {"id": "human-1", "type": "human_approval", "name": "approve", "config": {}}
        )
    else:
        nodes.append(
            {
                "id": "tool-1",
                "type": "tool_call",
                "name": "count",
                "config": {
                    "tool": "database/query",
                    "params": '{"sql": "SELECT COUNT(*) AS n FROM orders"}',
                },
            }
        )
    return {
        "version": 1,
        "variables": [],
        "nodes": nodes,
        "edges": [{"id": "e1", "source": "trigger-1", "target": nodes[1]["id"]}],
    }


def _send(skill: str, params: dict, caller: CallerIdentity | None) -> dict:
    message = {
        "role": "user",
        "parts": [{"kind": "data", "data": {"skill": skill, **params}}],
    }
    return handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {"message": message}},
        caller=caller,
    )


@pytest.fixture(autouse=True)
def _clean_a2a(monkeypatch):
    monkeypatch.delenv("ATLAS_A2A_TASK_TOKENS", raising=False)
    monkeypatch.delenv("ATLAS_A2A_TASK_TOKEN", raising=False)
    reset_for_tests()
    reset_bot_runtimes()
    yield
    reset_for_tests()
    reset_bot_runtimes()


# --- auth ---

def test_token_map_binds_caller_and_tenant(monkeypatch):
    monkeypatch.setenv("ATLAS_A2A_TASK_TOKENS", "bot-a:t1:token-a,bot-b:t2:token-b,broken")
    a = authenticate("Bearer token-a")
    b = authenticate("Bearer token-b")
    assert a == CallerIdentity("bot-a", "t1", "token-map")
    assert b == CallerIdentity("bot-b", "t2", "token-map")
    assert authenticate("Bearer nope") is None


def test_token_map_disables_legacy_token_fallback(monkeypatch):
    monkeypatch.setenv("ATLAS_A2A_TASK_TOKENS", "bot-a:t1:token-a")
    monkeypatch.setenv("ATLAS_A2A_TASK_TOKEN", "legacy")
    assert authenticate("Bearer legacy") is None


def test_prod_without_tokens_fails_closed(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    assert authenticate(None) is None


# --- real run ---

def test_real_run_writes_run_store_and_artifact():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")

    resp = _send(
        "run-graph",
        {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "k1"},
        caller,
    )
    task = resp["result"]
    assert task["status"]["state"] == "completed"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["mode"] == "run"
    assert data["tenantId"] == tenant
    assert data["graphId"] == graph_id
    assert data["outputs"]["tool-1"]["result"]["rows"][0]["n"] >= 0
    assert data["trace"] == ["trigger-1(trigger): executed", "tool-1(tool_call): executed"]
    assert task["artifacts"][0]["x-zeus-report"]["evidence"][0].startswith("real run")
    run = services.run_store.get(data["runId"])
    assert run is not None
    assert run["status"] == "completed"
    assert run["graphId"] == graph_id
    assert run["outputs"] == data["outputs"]


def test_idempotent_replay_returns_same_run_without_new_record():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    params = {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "same"}

    first = _send("run-graph", params, caller)["result"]
    second = _send("run-graph", params, caller)["result"]
    run_id = first["artifacts"][0]["parts"][0]["data"]["runId"]
    assert second["id"] == first["id"]
    assert second["status"]["state"] == "completed"
    assert second["artifacts"][0]["parts"][0]["data"]["runId"] == run_id
    assert len([r for r in services.run_store.list(limit=100) if r["graphId"] == graph_id]) == 1


def test_idempotency_key_reuse_with_different_request_is_rejected():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    other_graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    base = {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "same"}
    assert _send("run-graph", base, caller)["result"]["status"]["state"] == "completed"

    resp = _send("run-graph", {**base, "graph_id": other_graph_id}, caller)
    assert resp["error"]["code"] == -32004
    assert resp["error"]["data"]["taskId"]


def test_tenant_mismatch_and_unbound_caller_are_rejected():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    params = {"tenant_id": tenant, "graph_id": graph_id}

    mismatch = _send("run-graph", params, CallerIdentity("bot-b", "other", "token-map"))
    assert mismatch["error"]["code"] == -32011
    assert mismatch["error"]["data"]["requestedTenantId"] == tenant

    unbound = _send("run-graph", params, CallerIdentity("legacy", None, "legacy-token"))
    assert unbound["error"]["code"] == -32010
    assert not services.run_store.list(limit=10)


def test_interactive_graph_is_rejected_before_execution():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph(interactive=True))
    caller = CallerIdentity("bot-a", tenant, "token-map")

    task = _send("run-graph", {"tenant_id": tenant, "graph_id": graph_id}, caller)["result"]
    assert task["status"]["state"] == "failed"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["errorCode"] == "INTERACTIVE_GRAPH_UNSUPPORTED"
    assert data["nodeType"] == "human_approval"
    assert services.run_store.list(limit=10) == []


def test_failed_graph_preserves_global_error_and_trace(monkeypatch):
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")

    def failed_run(*args, **kwargs):
        return {
            "status": "failed",
            "outputs": {"tool-1": {"result": {"status": "FAILED"}}},
            "trace": ["tool-1(tool_call): failed: boom"],
            "global_error": {
                "code": "NODE_EXECUTION_FAILED",
                "nodeId": "tool-1",
                "detail": "boom",
            },
        }

    monkeypatch.setattr("atlas.a2a.run.run_graph", failed_run)
    task = _send("run-graph", {"tenant_id": tenant, "graph_id": graph_id}, caller)["result"]
    assert task["status"]["state"] == "failed"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["status"] == "failed"
    assert data["error"] == "NODE_EXECUTION_FAILED: boom"
    assert data["outputs"] == {"tool-1": {"result": {"status": "FAILED"}}}
    assert data["trace"] == ["tool-1(tool_call): failed: boom"]
    run = services.run_store.get(data["runId"])
    assert run["status"] == "failed"
    assert run["error"] == "NODE_EXECUTION_FAILED: boom"
    assert run["trace"] == ["tool-1(tool_call): failed: boom"]


def test_runtime_suspended_graph_records_interrupted_run(monkeypatch):
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")

    def suspended_run(*args, **kwargs):
        return {
            "status": "suspended",
            "outputs": {},
            "trace": ["human-1(human_approval): suspended"],
        }

    monkeypatch.setattr("atlas.a2a.run.run_graph", suspended_run)
    task = _send("run-graph", {"tenant_id": tenant, "graph_id": graph_id}, caller)["result"]
    assert task["status"]["state"] == "input-required"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["status"] == "input-required"
    assert "no resume path" in data["error"]
    run = services.run_store.get(data["runId"])
    assert run["status"] == "interrupted"
    assert run["trace"] == ["human-1(human_approval): suspended"]
    assert run["error"] == "run suspended: A2A v1 has no resume path; resolve in the platform session"


def test_conflict_lock_rejects_second_caller_while_run_active(monkeypatch):
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    entered = threading.Event()
    release = threading.Event()

    def slow_run(**kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return GraphRunResult(
            run_id=f"slow-{uuid.uuid4().hex[:8]}",
            graph_id=kwargs["graph_id"],
            status="completed",
            outputs={"tool-1": {"result": {"row_count": 1}}},
            trace=["tool-1(tool_call): executed"],
            error=None,
            wall_seconds=0.1,
        )

    monkeypatch.setattr("atlas.a2a.rpc.execute_graph_run", slow_run)
    holder: dict = {}

    def first_call():
        holder["resp"] = _send(
            "run-graph",
            {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "first"},
            caller,
        )

    thread = threading.Thread(target=first_call)
    thread.start()
    assert entered.wait(timeout=3)
    second = _send(
        "run-graph",
        {"tenant_id": tenant, "graph_id": graph_id, "idempotency_key": "second"},
        caller,
    )
    assert second["error"]["code"] == -32003
    assert second["error"]["data"]["activeTaskId"]
    release.set()
    thread.join(timeout=3)
    assert holder["resp"]["result"]["status"]["state"] == "completed"


def test_task_ledger_is_volatile_but_run_store_survives_reset(monkeypatch):
    # 这条用例断言的是 memory 档语义；显式隔离，避免外部 ATLAS_STORAGE_BACKEND=pg 时
    # reset 后从 PG 回读到任务，导致用例本身失效。
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "memory")
    reset_for_tests()

    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    task = _send("run-graph", {"tenant_id": tenant, "graph_id": graph_id}, caller)["result"]
    run_id = task["artifacts"][0]["parts"][0]["data"]["runId"]

    reset_for_tests()  # 等价本进程任务台账重启
    assert handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": {"id": task["id"]}}
    )["error"]["code"] == -32001
    # run_store 是租户服务，不会被 A2A 任务台账 reset 清掉；进程退出才失。
    assert services.run_store.get(run_id)["status"] == "completed"
    reset_for_tests()


# --- HTTP ---

def test_http_token_map_runs_real_graph(monkeypatch):
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    monkeypatch.setenv("ATLAS_A2A_TASK_TOKENS", f"bot-a:{tenant}:token-a")
    client = TestClient(app)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tasks/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "data",
                        "data": {"skill": "run-graph", "tenant_id": tenant, "graph_id": graph_id},
                    }
                ],
            }
        },
    }
    assert client.post("/api/a2a/tasks", json=body).status_code == 401
    ok = client.post(
        "/api/a2a/tasks", json=body, headers={"Authorization": "Bearer token-a"}
    )
    assert ok.status_code == 200
    task = ok.json()["result"]
    assert task["status"]["state"] == "completed"
    assert task["metadata"]["a2a"]["runId"].startswith("a2a-")


def test_agent_id_binds_task_to_bot_runtime_and_run_store():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    services.agents_store.put(
        Agent(
            id="bot.finance",
            tenant_id=tenant,
            name="Finance Bot",
            adapter_ids=["database"],
        )
    )
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")

    task = _send(
        "run-graph",
        {
            "tenant_id": tenant,
            "graph_id": graph_id,
            "agent_id": "bot.finance",
            "idempotency_key": "agent-k1",
        },
        caller,
    )["result"]
    assert task["status"]["state"] == "completed"
    assert task["metadata"]["a2a"]["agentId"] == "bot.finance"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["agentId"] == "bot.finance"
    assert data["runtimeId"] == f"bot-runtime:{tenant}:bot.finance"
    run = services.run_store.get(data["runId"])
    assert run["agentId"] == "bot.finance"
    assert "agent runtime: agentId=bot.finance" in task["artifacts"][0]["x-zeus-report"]["evidence"][2]


def test_unknown_or_disabled_agent_is_rejected_before_run():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    graph_id = services.graph_store.save(_graph())
    caller = CallerIdentity("bot-a", tenant, "token-map")
    base = {"tenant_id": tenant, "graph_id": graph_id}

    missing = _send("run-graph", {**base, "agent_id": "bot.missing"}, caller)
    assert missing["error"]["code"] == -32015

    services.agents_store.put(
        Agent(id="bot.off", tenant_id=tenant, name="Off", enabled=False, adapter_ids=[])
    )
    disabled = _send("run-graph", {**base, "agent_id": "bot.off"}, caller)
    assert disabled["error"]["code"] == -32016
    assert services.run_store.list(limit=10) == []


def test_bot_runtime_filters_adapters_to_agent_scope():
    tenant = f"a2a-{uuid.uuid4().hex[:8]}"
    services = tenant_registry.get(tenant)
    services.agents_store.put(
        Agent(id="bot.no-message", tenant_id=tenant, name="No Message", adapter_ids=["database"])
    )
    graph = _graph()
    graph["nodes"][1]["config"] = {
        "tool": "message/send",
        "params": '{"channel":"email","to":["ops@example.com"],"subject":"x","body":"y"}',
    }
    graph_id = services.graph_store.save(graph)
    caller = CallerIdentity("bot-a", tenant, "token-map")

    task = _send(
        "run-graph",
        {"tenant_id": tenant, "graph_id": graph_id, "agent_id": "bot.no-message"},
        caller,
    )["result"]
    assert task["status"]["state"] == "failed"
    data = task["artifacts"][0]["parts"][0]["data"]
    assert data["status"] == "failed"
    assert "适配器未注册：message" in (data.get("error") or "")

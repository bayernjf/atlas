# -*- coding: utf-8 -*-
"""T7：多智能体可观测回归（task→bot→run→span、每 Bot 指标、告警、回放）。

保留既有 span/metrics/alerts/replay 语义；本文件只追加 A2A Bot 运行的可观测关联：
- A2A task.id ↔ task.metadata.a2a ↔ RunRecord.task_id/run_id/agent_id/runtime_id；
- RunRecord.spans 根 span attrs 带同组 taskId/agentId/runtimeId/runId/traceId；
- /api/monitoring/metrics 的 per_bot 与 Prometheus atlas_bot_* 序列；
- Bot 运行失败照常触发内置告警，告警 last_run_id 可回查到同一关联链；
- 同 idempotency_key 回放不重跑、不新增监控记录，task/run/trace 三键不变。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.a2a.auth import CallerIdentity
from atlas.a2a.bot_runtime import reset_for_tests as reset_bot_runtimes
from atlas.a2a.rpc import handle_jsonrpc, reset_for_tests
from atlas.api.main import app
from atlas.iam.agents_store import Agent
from atlas.iam.deps import tenant_registry
from atlas.monitoring.records import MonitoringStore
from atlas.observability.metrics_export import render_prometheus
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_a2a(monkeypatch):
    monkeypatch.delenv("ATLAS_A2A_TASK_TOKENS", raising=False)
    monkeypatch.delenv("ATLAS_A2A_TASK_TOKEN", raising=False)
    reset_for_tests()
    reset_bot_runtimes()
    yield
    reset_for_tests()
    reset_bot_runtimes()


def _graph(tool: str, params: dict | None = None) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "start",
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/t7"},
            },
            {
                "id": "tool-1",
                "type": "tool_call",
                "name": "call",
                "config": {"tool": tool, "params": json.dumps(params or {})},
            },
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def _send(caller: CallerIdentity, params: dict) -> dict:
    message = {
        "role": "user",
        "parts": [{"kind": "data", "data": {"skill": "run-graph", **params}}],
    }
    return handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send", "params": {"message": message}},
        caller=caller,
    )["result"]


def _artifact_data(task: dict) -> dict:
    for part in task["artifacts"][0]["parts"]:
        if part.get("kind") == "data":
            return part["data"]
    raise AssertionError("artifact has no data part")


def _bot(services, *, adapter_ids: list[str]) -> str:
    bot_id = f"t7.bot.{uuid.uuid4().hex[:8]}"
    services.agents_store.put(
        Agent(
            id=bot_id,
            tenant_id=services.tenant_id,
            name="T7 Audit Bot",
            adapter_ids=adapter_ids,
            skills=["observability.audit"],
        )
    )
    return bot_id


def _record_for_task(services, task_id: str):
    records = services.monitoring.list_runs(limit=200)
    matches = [record for record in records if record.task_id == task_id]
    assert len(matches) == 1, f"expected one monitoring record for task {task_id}"
    return matches[0]


def _assert_chain(task: dict, data: dict, record) -> None:
    assert data["taskId"] == task["id"] == record.task_id
    assert data["runId"] == record.run_id
    assert data["traceId"] == record.trace_id
    assert data["agentId"] == record.agent_id
    assert data["runtimeId"] == record.runtime_id
    assert task["metadata"]["a2a"]["runId"] == data["runId"]
    assert task["metadata"]["a2a"]["traceId"] == data["traceId"]
    assert record.spans is not None
    attrs = record.spans.get("attrs") or {}
    assert attrs["taskId"] == task["id"]
    assert attrs["runId"] == data["runId"]
    assert attrs["agentId"] == data["agentId"]
    assert attrs["runtimeId"] == data["runtimeId"]


def test_t7_a2a_task_bot_run_span_chain_and_per_bot_metrics():
    services = tenant_registry.get("t1")
    bot_id = _bot(services, adapter_ids=["database"])
    graph_id = services.graph_store.save(
        _graph("database/query", {"sql": "SELECT COUNT(*) AS n FROM orders"})
    )
    caller = CallerIdentity("bot-a", "t1", "token-map")

    task = _send(
        caller,
        {
            "tenant_id": "t1",
            "graph_id": graph_id,
            "agent_id": bot_id,
            "idempotency_key": f"t7-{uuid.uuid4().hex}",
        },
    )
    if task["status"]["state"] != "completed":
        raise AssertionError(task["artifacts"][0]["x-zeus-report"]["evidence"])
    data = _artifact_data(task)
    record = _record_for_task(services, task["id"])
    _assert_chain(task, data, record)
    assert record.mode == "a2a"
    assert record.status == "completed"

    # 每 Bot 指标：进程内 snapshot + REST 同源。
    snapshot = services.monitoring.snapshot_metrics()
    per_bot = {row["bot_id"]: row for row in snapshot["per_bot"]}
    assert per_bot[bot_id]["total"] >= 1
    assert per_bot[bot_id]["success_rate"] is not None

    body = client.get("/api/monitoring/metrics", headers=DEFAULT_AUTH_HEADER).json()
    assert bot_id in {row["bot_id"] for row in body["per_bot"]}

    # /api/monitoring/runs?agent_id= 可直接按 Bot 过滤，并返回关联键。
    runs = client.get(
        "/api/monitoring/runs",
        params={"agent_id": bot_id, "limit": 200},
        headers=DEFAULT_AUTH_HEADER,
    ).json()["items"]
    assert any(run["task_id"] == task["id"] and run["run_id"] == data["runId"] for run in runs)

    # trace 端点取回同一 span 树，根 attrs 仍是同一关联链。
    trace = client.get(
        f"/api/monitoring/runs/{record.id}/trace", headers=DEFAULT_AUTH_HEADER
    ).json()
    assert trace["trace_id"] == data["traceId"]
    assert trace["spans"]["attrs"]["taskId"] == task["id"]
    assert trace["spans"]["attrs"]["runId"] == data["runId"]


def test_t7_bot_failure_raises_alert_with_same_chain():
    services = tenant_registry.get("t1")
    bot_id = _bot(services, adapter_ids=["database"])
    graph_id = services.graph_store.save(
        _graph("shop/execute_refund", {"order_id": "T7-ORDER-FAIL", "amount": 10})
    )
    caller = CallerIdentity("bot-a", "t1", "token-map")
    task = _send(
        caller,
        {
            "tenant_id": "t1",
            "graph_id": graph_id,
            "agent_id": bot_id,
            "idempotency_key": f"t7-fail-{uuid.uuid4().hex}",
        },
    )
    data = _artifact_data(task)
    record = _record_for_task(services, task["id"])
    _assert_chain(task, data, record)
    assert record.status in ("completed", "error")

    alerts = services.monitoring.list_alerts()
    linked = [alert for alert in alerts if alert.last_run_id == record.id]
    assert linked, "Bot 失败运行必须进入既有告警规则"
    assert {alert.rule_id for alert in linked} & {"run_error", "node_failed", "consecutive_failures", "failure_rate"}


def test_t7_idempotent_replay_keeps_task_run_trace_without_new_record():
    services = tenant_registry.get("t1")
    bot_id = _bot(services, adapter_ids=["database"])
    graph_id = services.graph_store.save(
        _graph("database/query", {"sql": "SELECT COUNT(*) AS n FROM orders"})
    )
    caller = CallerIdentity("bot-a", "t1", "token-map")
    params = {
        "tenant_id": "t1",
        "graph_id": graph_id,
        "agent_id": bot_id,
        "idempotency_key": f"t7-replay-{uuid.uuid4().hex}",
    }

    first = _send(caller, params)
    first_data = _artifact_data(first)
    second = _send(caller, params)
    second_data = _artifact_data(second)
    assert second["id"] == first["id"]
    assert second_data["runId"] == first_data["runId"]
    assert second_data["traceId"] == first_data["traceId"]
    assert second_data["taskId"] == first_data["taskId"]

    records = [r for r in services.monitoring.list_runs(limit=200) if r.task_id == first["id"]]
    assert len(records) == 1, "回放不得新建监控记录/重跑"
    _assert_chain(first, first_data, records[0])


def test_t7_per_bot_prometheus_series_and_empty_agent_skip():
    store = MonitoringStore()
    now = datetime.now(timezone.utc).isoformat()
    store.record_run(
        graph_id="g1", mode="a2a", status="completed",
        started_at=now, duration_ms=10.0, nodes=[], agent_id="bot-a", run_id="run-a",
        task_id="task-a", runtime_id="bot-runtime:t:bot-a",
    )
    store.record_run(
        graph_id="g1", mode="sync", status="error",
        started_at=now, duration_ms=20.0, nodes=[], error="boom",
    )
    snapshot = store.snapshot_metrics()
    assert [row["bot_id"] for row in snapshot["per_bot"]] == ["bot-a"]

    text = render_prometheus(storage_backend="memory", tenant_snapshots=[("t1", snapshot)])
    assert 'atlas_bot_runs_total{tenant_id="t1",bot_id="bot-a",status="healthy"} 1' in text
    assert 'atlas_bot_runs_success_rate{tenant_id="t1",bot_id="bot-a"} 1' in text
    assert 'atlas_bot_runs_total{tenant_id="t1",bot_id="bot-a",status="unhealthy"} 0' in text

"""B3：GET /api/graphs/{id}/diff 端点测试（D26 跨版本配置差异子集）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)


def _graph(mark="节点A", amount=100):
    return {
        "version": 1,
        "variables": [
            {"name": "threshold", "type": "int", "value": amount, "scope": "global"}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool-1", "type": "tool_call", "name": mark,
             "config": {"tool": "op-1"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


@pytest.fixture(autouse=True)
def _session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


def test_default_diff_draft_vs_latest_release():
    from atlas.iam.deps import tenant_registry

    gid = client.post("/api/graphs", json=_graph()).json()["id"]
    assert client.post(f"/api/graphs/{gid}/publish").status_code == 200
    store = tenant_registry.get("t1").graph_store
    store._graphs[gid] = _graph("节点B", 200)  # 改草稿：节点改名 + 变量值变

    body = client.get(f"/api/graphs/{gid}/diff").json()
    assert body["fromVersion"] == 1
    assert body["toVersion"] is None  # latest 草稿
    assert body["summary"]["nodesChanged"] == 1
    assert body["summary"]["variablesChanged"] == 1
    changed = body["diff"]["nodes"]["changed"][0]
    assert changed["id"] == "tool-1"
    assert {c["field"] for c in changed["changes"]} == {"name"}


def test_diff_before_first_release_uses_empty_baseline():
    gid = client.post("/api/graphs", json=_graph()).json()["id"]
    body = client.get(f"/api/graphs/{gid}/diff").json()
    assert body["fromVersion"] is None
    assert body["summary"]["nodesAdded"] == 2
    assert body["summary"]["edgesAdded"] == 1
    assert body["summary"]["variablesAdded"] == 1


def test_diff_between_two_named_versions():
    from atlas.iam.deps import tenant_registry

    gid = client.post("/api/graphs", json=_graph("节点A")).json()["id"]
    client.post(f"/api/graphs/{gid}/publish")
    store = tenant_registry.get("t1").graph_store
    store._graphs[gid] = _graph("节点B")
    client.post(f"/api/graphs/{gid}/publish")

    body = client.get(
        f"/api/graphs/{gid}/diff?fromVersion=1&toVersion=2"
    ).json()
    assert body["fromVersion"] == 1 and body["toVersion"] == 2
    assert body["summary"]["nodesChanged"] == 1


def test_diff_unknown_graph_and_versions_are_404():
    assert client.get("/api/graphs/graph-nope/diff").status_code == 404
    gid = client.post("/api/graphs", json=_graph()).json()["id"]
    assert client.get(f"/api/graphs/{gid}/diff?fromVersion=9").status_code == 404
    assert client.get(f"/api/graphs/{gid}/diff?toVersion=9").status_code == 404

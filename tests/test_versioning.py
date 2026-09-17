"""M6 Graph 版本化与 subgraph 钉版（docs/20 §4.1 / 08 M6 立项条 / ADR T19，U46）。

纯逻辑（GraphStore + versioning.publish）+ REST（复用 t1 admin 会话的 graphs TestClient）。
"""

from __future__ import annotations

from atlas.storage.memory import GraphStore
from atlas.versioning.publish import publish


def _subgraph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "t", "type": "trigger", "name": "触发", "config": {"triggerType": "manual"}},
            {"id": "x", "type": "tool_call", "name": "工具", "config": {"tool": "shop/process_refund"}},
        ],
        "edges": [{"id": "e", "source": "t", "target": "x"}],
    }


def _parent(sub_id: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "t", "type": "trigger", "name": "触发", "config": {"triggerType": "manual"}},
            {"id": "s", "type": "subgraph", "name": "子图", "config": {"graphId": sub_id, "inputs": {}}},
        ],
        "edges": [{"id": "e", "source": "t", "target": "s"}],
    }


def test_publish_freezes_immutable_snapshot():
    store = GraphStore()
    graph_id = store.save(_subgraph())

    assert publish(store, graph_id) == 1
    assert publish(store, graph_id) == 2

    v1 = store.get(graph_id, 1)
    v2 = store.get(graph_id, 2)
    assert v1 is not None and v2 is not None
    assert v1["releaseVersion"] == 1 and v2["releaseVersion"] == 2
    # 快照与草稿隔离：改草稿不影响已发布版本
    store._graphs[graph_id]["nodes"][0]["name"] = "改过了"
    assert store.get(graph_id, 1)["nodes"][0]["name"] == "触发"
    assert store.get(graph_id)["nodes"][0]["name"] == "改过了"  # latest 已变
    # 已发布版本只读：无改写路径，list_versions 升序
    assert store.list_versions(graph_id) == [1, 2]
    # reset/clear 清版本
    store.clear()
    assert store.list_versions(graph_id) == []


def test_publish_unknown_graph_raises():
    store = GraphStore()
    try:
        publish(store, "graph-999")
    except KeyError:
        pass
    else:
        raise AssertionError("发布不存在的草稿应抛 KeyError")


def test_subgraph_pinning_recursive():
    store = GraphStore()
    sub_id = store.save(_subgraph())
    parent_id = store.save(_parent(sub_id))

    # 发布父图：子图未发布 → 递归发布子图 v1 并钉 graphId@v1
    assert publish(store, parent_id) == 1
    assert store.list_versions(sub_id) == [1]
    parent_v1 = store.get(parent_id, 1)
    assert parent_v1["nodes"][1]["config"]["graphId"] == f"{sub_id}@1"

    # 子图再发布 v2 后，父图已发布版本仍钉 v1（跨版本隔离）
    assert publish(store, sub_id) == 2
    assert store.get(parent_id, 1)["nodes"][1]["config"]["graphId"] == f"{sub_id}@1"
    # 再次发布父图 → 钉子图最新（v2）
    assert publish(store, parent_id) == 2
    assert store.get(parent_id, 2)["nodes"][1]["config"]["graphId"] == f"{sub_id}@2"


def test_publish_and_version_endpoints():
    from tests.test_api_graphs import _sample_graph, client as api

    graph_id = api.post("/api/graphs", json=_sample_graph()).json()["id"]

    published = api.post(f"/api/graphs/{graph_id}/publish")
    assert published.status_code == 200
    assert published.json() == {"id": graph_id, "releaseVersion": 1}

    assert api.get(f"/api/graphs/{graph_id}/versions").json() == {"items": [1]}

    fetched = api.get(f"/api/graphs/{graph_id}", params={"releaseVersion": 1})
    assert fetched.status_code == 200
    assert fetched.json()["releaseVersion"] == 1

    # 未知版本 404；缺省读 latest（草稿，无 releaseVersion）
    assert api.get(f"/api/graphs/{graph_id}", params={"releaseVersion": 99}).status_code == 404
    assert "releaseVersion" not in api.get(f"/api/graphs/{graph_id}").json()

    # compile/run 可选版本（同一 _load_graph_or_404 版本读取路径）
    compiled = api.post(f"/api/graphs/{graph_id}/compile", json={"releaseVersion": 1})
    assert compiled.status_code == 200
    assert len(compiled.json()["nodes"]) == 3

    run = api.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}, "releaseVersion": 1})
    assert run.status_code == 200
    assert run.json()["id"] == graph_id

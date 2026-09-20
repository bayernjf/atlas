# -*- coding: utf-8 -*-
"""docs/28 批 4 ⑪（D21 部分取回）：发布前子图版本升级体检（U202–U210）。

纯函数 subgraph_upgrade_plan：to/from/first_pin、父图首次发布、子图从无发布版、
无变化不列、草稿新增节点、裸 id/@draft 解析、草稿不存在 None、只扫顶层不递归；
REST GET（read、404、{items}、纯只读不产版本）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import services_for
from atlas.iam.principals import Principal, Role
from atlas.storage.memory import GraphStore
from atlas.versioning.publish import publish as publish_version
from atlas.versioning.upgrades import split_graph_ref, subgraph_upgrade_plan

client = TestClient(app)

T1_ADMIN = Principal(
    tenant_id="t1", tenant_name="A", username="admin-a", display_name="管理员", role=Role.ADMIN
)


@pytest.fixture(autouse=True)
def _admin_session():
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield
    client.post("/api/demo/reset")
    client.headers.pop("authorization", None)


def _sub(node_id: str, ref: str) -> dict:
    return {"id": node_id, "type": "subgraph", "config": {"graphId": ref}}


def _tool(node_id: str) -> dict:
    return {"id": node_id, "type": "tool", "config": {"tool": "demo.query_order", "inputs": {}}}


def _publish(store: GraphStore, graph_id: str) -> int:
    return publish_version(store, graph_id)


# ---- 纯函数：U202–U208 ----


def test_u202_parent_first_release_pins_latest_sub_version_as_first_pin():
    store = GraphStore()
    sub = store.save({"nodes": [_tool("s1")]})
    _publish(store, sub)  # v1
    store.update_draft(sub, {"nodes": [_tool("s1b")]})
    _publish(store, sub)  # v2
    parent = store.save({"nodes": [_sub("n1", sub)]})
    plan = subgraph_upgrade_plan(store, parent)
    assert plan == [
        {"node_id": "n1", "sub_id": sub, "from_version": None,
         "to_version": 2, "first_pin": True}
    ]


def test_u203_subgraph_never_published_targets_v1_first_pin():
    store = GraphStore()
    sub = store.save({"nodes": [_tool("s")]})  # 只有草稿、无发布版
    parent = store.save({"nodes": [_sub("n1", sub)]})
    plan = subgraph_upgrade_plan(store, parent)
    assert plan == [
        {"node_id": "n1", "sub_id": sub, "from_version": None,
         "to_version": 1, "first_pin": True}
    ]


def test_u204_upgrade_after_parent_released_shows_from_to_not_first_pin():
    store = GraphStore()
    sub = store.save({"nodes": [_tool("s1")]})
    _publish(store, sub)
    store.update_draft(sub, {"nodes": [_tool("s2")]})
    _publish(store, sub)  # sub v2
    parent = store.save({"nodes": [_sub("n1", sub)]})
    _publish(store, parent)  # parent v1 钉 sub@2（草稿引用仍为裸 sub）
    store.update_draft(sub, {"nodes": [_tool("s3")]})
    _publish(store, sub)  # sub v3
    plan = subgraph_upgrade_plan(store, parent)
    assert plan == [
        {"node_id": "n1", "sub_id": sub, "from_version": 2,
         "to_version": 3, "first_pin": False}
    ]


def test_u205_no_change_item_is_omitted():
    store = GraphStore()
    sub = store.save({"nodes": [_tool("s1")]})
    _publish(store, sub)
    store.update_draft(sub, {"nodes": [_tool("s2")]})
    _publish(store, sub)  # sub v2
    parent = store.save({"nodes": [_sub("n1", sub)]})
    _publish(store, parent)  # parent v1 钉 sub@2；子图此后不再发版
    assert subgraph_upgrade_plan(store, parent) == []


def test_u206_new_subgraph_node_after_parent_release_is_first_pin():
    store = GraphStore()
    sub_a = store.save({"nodes": [_tool("a")]})
    _publish(store, sub_a)
    store.update_draft(sub_a, {"nodes": [_tool("a2")]})
    _publish(store, sub_a)  # A v2
    sub_b = store.save({"nodes": [_tool("b")]})
    _publish(store, sub_b)  # B v1
    parent = store.save({"nodes": [_sub("n1", sub_a)]})
    _publish(store, parent)  # parent v1：仅 n1 钉 A@2
    store.update_draft(parent, {"nodes": [_sub("n1", sub_a), _sub("n2", sub_b)]})
    plan = subgraph_upgrade_plan(store, parent)
    # n1 仍是 A@2 无变化不列；n2 为新增引用 → 首次钉版 B@1
    assert plan == [
        {"node_id": "n2", "sub_id": sub_b, "from_version": None,
         "to_version": 1, "first_pin": True}
    ]


def test_u207_split_graph_ref_handles_bare_pinned_and_draft():
    assert split_graph_ref("graph-7") == ("graph-7", None)
    assert split_graph_ref("graph-7@3") == ("graph-7", 3)
    assert split_graph_ref("graph-7@draft") == ("graph-7", None)
    assert split_graph_ref("") == (None, None)
    assert split_graph_ref(None) == (None, None)
    assert split_graph_ref(123) == (None, None)


def test_u208_missing_draft_returns_none_and_only_top_level_scanned():
    # 草稿不存在 → None
    assert subgraph_upgrade_plan(GraphStore(), "graph-404") is None
    # 嵌套：middle 内引用 inner，体检 parent 只列顶层 n1，不递归出 inner
    store = GraphStore()
    inner = store.save({"nodes": [_tool("i")]})
    middle = store.save({"nodes": [_sub("m_inner", inner)]})
    parent = store.save({"nodes": [_sub("n1", middle)]})
    plan = subgraph_upgrade_plan(store, parent)
    node_ids = {item["node_id"] for item in plan}
    assert node_ids == {"n1"}
    assert plan[0]["sub_id"] == middle
    assert all(item["sub_id"] != inner for item in plan)


def test_u209_plan_is_read_only_and_does_not_create_versions():
    store = GraphStore()
    sub = store.save({"nodes": [_tool("s")]})
    parent = store.save({"nodes": [_sub("n1", sub)]})
    sub_versions_before = store.list_versions(sub)
    parent_versions_before = store.list_versions(parent)
    subgraph_upgrade_plan(store, parent)  # 只读计算
    assert store.list_versions(sub) == sub_versions_before
    assert store.list_versions(parent) == parent_versions_before


# ---- REST：U210 ----


def test_u210_endpoint_read_role_404_and_payload_shape():
    store = services_for(T1_ADMIN).graph_store
    sub = store.save({"nodes": [_tool("s")]})
    _publish(store, sub)  # sub v1
    parent = store.save({"nodes": [_sub("n1", sub), _tool("t1")]})

    resp = client.get(f"/api/graphs/{parent}/subgraph-upgrades")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0] == {
        "node_id": "n1", "sub_id": sub, "from_version": None,
        "to_version": 1, "first_pin": True,
    }
    # 非 subgraph 节点不计入
    assert all(item["node_id"] != "t1" for item in items)

    # 草稿不存在 → 404
    assert client.get("/api/graphs/graph-404/subgraph-upgrades").status_code == 404

    # viewer（read）可访问；未认证 401
    viewer_login = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    )
    viewer = {"Authorization": f"Bearer {viewer_login.json()['token']}"}
    assert client.get(
        f"/api/graphs/{parent}/subgraph-upgrades", headers=viewer
    ).status_code == 200
    assert client.get(
        f"/api/graphs/{parent}/subgraph-upgrades", headers={"Authorization": ""}
    ).status_code == 401

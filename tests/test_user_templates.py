# -*- coding: utf-8 -*-
"""用户自建流程模板库 v1（打包 X，docs/85；U974–U979）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate
from atlas.template.user_store import UserTemplateStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_a_session():
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    client.headers["Authorization"] = f"Bearer {token}"
    try:
        yield
    finally:
        client.headers.pop("authorization", None)
        session_store.revoke(token)



def _sample_graph():
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }


def _auth(username: str) -> dict[str, str]:
    principal = authenticate(username, "admin123" if username.startswith("admin") else f"{username.split('-')[0]}123")
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


# --- U974：内存 store 纯逻辑 ----------------------------------------------

def test_user_template_store_crud_and_clear():
    store = UserTemplateStore()
    first = store.add(name="A", description="", tags=[], graph=_sample_graph())
    assert first.id == "utpl-1"
    assert first.created_at
    second = store.add(name="B", description="d", tags=["t"], graph=_sample_graph())
    assert second.id == "utpl-2"

    assert store.get("utpl-1").name == "A"
    assert store.get("missing") is None
    assert [item.id for item in store.list()] == ["utpl-2", "utpl-1"]

    assert store.delete("utpl-1") is True
    assert store.get("utpl-1") is None
    assert store.delete("utpl-1") is False

    store.clear()
    assert store.list() == []
    again = store.add(name="C", description="", tags=[], graph=_sample_graph())
    assert again.id == "utpl-1"


# --- U975：POST 另存 ------------------------------------------------------

def test_create_user_template_returns_201_and_seq():
    resp = client.post("/api/templates", json={"name": "现场模板", "graph": _sample_graph()})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("utpl-")
    assert body["source"] == "user"
    assert body["deletable"] is True
    assert body["created_at"]
    assert body["graph"]["nodes"][0]["id"] == "trigger-1"

    second = client.post(
        "/api/templates",
        json={"name": "第二个", "description": "备注", "tags": ["现场"], "graph": _sample_graph()},
    )
    assert second.status_code == 201
    assert int(second.json()["id"].split("-")[1]) == int(body["id"].split("-")[1]) + 1


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "graph": _sample_graph()},
        {"name": "x" * 61, "graph": _sample_graph()},
        {"name": "   ", "graph": _sample_graph()},
        {"name": "ok", "description": "y" * 201, "graph": _sample_graph()},
        {"name": "ok", "tags": ["z" * 21], "graph": _sample_graph()},
        {"name": "ok", "tags": [f"t{i}" for i in range(9)], "graph": _sample_graph()},
        {"name": "ok", "graph": {"version": 1, "nodes": [], "edges": []}},
    ],
)
def test_create_user_template_validation_422(payload):
    resp = client.post("/api/templates", json=payload)
    assert resp.status_code == 422


# --- U976：GET 合并 -------------------------------------------------------

def test_list_merges_catalog_and_user_templates():
    client.post("/api/templates", json={"name": "合并验证", "graph": _sample_graph()})
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    items = resp.json()["items"]

    catalog = [item for item in items if item["source"] == "catalog"]
    user = [item for item in items if item["source"] == "user"]
    assert catalog, "内置模板不应为空"
    assert user, "用户模板应已合并"
    assert all(item["deletable"] is False for item in catalog)
    assert all(item["deletable"] is True for item in user)
    assert "graph" not in items[0]
    assert items.index(catalog[0]) < items.index(user[0])

    detail = client.get(f"/api/templates/{user[0]['id']}").json()
    assert detail["source"] == "user"
    assert detail["graph"]


# --- U977：DELETE ---------------------------------------------------------

def test_delete_user_template_and_404_cases():
    created = client.post("/api/templates", json={"name": "待删", "graph": _sample_graph()})
    template_id = created.json()["id"]

    deleted = client.delete(f"/api/templates/{template_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.delete(f"/api/templates/{template_id}").status_code == 404
    assert client.get(f"/api/templates/{template_id}").status_code == 404

    catalog_id = client.get("/api/templates").json()["items"][0]["id"]
    resp = client.delete(f"/api/templates/{catalog_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == f"模板不存在：{catalog_id}"
    assert client.get(f"/api/templates/{catalog_id}").status_code == 200


# --- U978：跨租户分区 ------------------------------------------------------

def test_user_templates_isolated_per_tenant():
    created = client.post("/api/templates", json={"name": "A 租户专属", "graph": _sample_graph()})
    a_id = created.json()["id"]

    admin_b = _auth("admin-b")
    items = client.get("/api/templates", headers=admin_b).json()["items"]
    assert all(item["id"] != a_id for item in items)
    assert client.delete(f"/api/templates/{a_id}", headers=admin_b).status_code == 404

    b_created = client.post(
        "/api/templates", json={"name": "B 租户专属", "graph": _sample_graph()}, headers=admin_b
    )
    assert b_created.json()["id"] == "utpl-1"
    assert client.get(f"/api/templates/{a_id}").status_code == 200


# --- U979：权限与 reset ----------------------------------------------------

def test_viewer_cannot_create_or_delete():
    viewer = _auth("viewer-a")
    assert client.get("/api/templates", headers=viewer).status_code == 200
    resp = client.post(
        "/api/templates", json={"name": "x", "graph": _sample_graph()}, headers=viewer
    )
    assert resp.status_code == 403

    created = client.post("/api/templates", json={"name": "待保护", "graph": _sample_graph()})
    template_id = created.json()["id"]
    assert client.delete(f"/api/templates/{template_id}", headers=viewer).status_code == 403


def test_reset_clears_user_templates():
    created = client.post("/api/templates", json={"name": "reset 前", "graph": _sample_graph()})
    template_id = created.json()["id"]

    from atlas.iam.deps import tenant_registry

    tenant_registry.reset_tenant("t1")
    assert client.get(f"/api/templates/{template_id}").status_code == 404
    items = client.get("/api/templates").json()["items"]
    assert all(item["source"] == "catalog" for item in items)

# -*- coding: utf-8 -*-
"""Shopify 店铺侧 webhook 注册 REST 测试（docs/41 §5，I 类，U380 区段）。

渠道调用经注入的 fake transport 零触网；同进程 Admin mock 端点另行直测。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.channels.base import TransportResponse

client = TestClient(app)

CONN_BODY = {
    "provider": "generic",
    "displayName": "测试店铺 OAuth",
    "authUrl": "https://sso.example.com/auth",
    "tokenUrl": "https://sso.example.com/token",
    "clientId": "cid-remote-wh",
    "clientSecret": "shh",
    "scopes": ["read"],
}


class _AllowEgress:
    def check(self, url: str) -> None:
        return None


class FakeShopifyTransport:
    """照 Shopify webhooks 契约裁剪的进程内假上游（含 422 重复语义）。"""

    def __init__(self) -> None:
        self.webhooks: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append(
            {"method": method, "url": url, "json_body": json_body}
        )
        path = url.split("?", 1)[0]
        if path.endswith("/webhooks.json") and method == "GET":
            rows = [{"id": wid, **record} for wid, record in self.webhooks.items()]
            return self._ok({"webhooks": rows})
        if path.endswith("/webhooks.json") and method == "POST":
            webhook = json_body["webhook"]
            for existing in self.webhooks.values():
                if (
                    existing["topic"] == webhook["topic"]
                    and existing["address"] == webhook["address"]
                ):
                    return self._resp({}, 422)
            remote_id = self.next_id
            self.next_id += 1
            record = {
                "topic": webhook["topic"],
                "address": webhook["address"],
                "format": "json",
            }
            self.webhooks[remote_id] = record
            return self._ok({"webhook": {"id": remote_id, **record}})
        if method == "DELETE" and "/webhooks/" in path:
            remote_id = int(path.rsplit("/webhooks/", 1)[1].split(".", 1)[0])
            if remote_id not in self.webhooks:
                return self._resp({}, 404)
            del self.webhooks[remote_id]
            return TransportResponse(200, {}, text="")
        return self._resp({}, 500)

    @staticmethod
    def _ok(payload: dict[str, Any]) -> TransportResponse:
        return FakeShopifyTransport._resp(payload, 200)

    @staticmethod
    def _resp(payload: dict[str, Any], status: int) -> TransportResponse:
        return TransportResponse(status, {}, json.dumps(payload))


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    import atlas.connections.service as cs
    from atlas.iam.deps import tenant_registry

    cs._egress_guard = _AllowEgress()
    monkeypatch.setenv("ATLAS_PUBLIC_URL", "https://atlas.example.com")
    admin = _login("admin")
    client.post("/api/demo/reset", headers=admin)
    services = tenant_registry.get("t1")
    services.connection_service._egress = _AllowEgress()
    for item in services.connection_service.list():
        services.connection_service.delete(item["id"])
    for item in services.channel_registry.list():
        services.channel_registry.delete(item["id"])

    fake = FakeShopifyTransport()
    services.channel_registry._transport = fake
    services.channel_registry._base_url = "https://admin.test/api"
    yield fake, services
    client.headers.pop("authorization", None)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _connected_binding(headers) -> str:
    cid = client.post("/api/connections", json=CONN_BODY, headers=headers).json()["id"]
    bid = client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": cid, "config": {"shop": "acme"}},
        headers=headers,
    ).json()["id"]
    conn = _setup_services().connection_service._store.get(cid)
    conn.status = "connected"
    conn.access_token_envelope = (
        _setup_services().connection_service._provider.encrypt("shpat-test")
    )
    _setup_services().connection_service._store.save(conn)
    return bid


def _setup_services():
    from atlas.iam.deps import tenant_registry

    return tenant_registry.get("t1")


def test_get_remote_webhooks_lists_items(_setup):
    fake, _services = _setup
    bid = _connected_binding(_login("admin"))
    fake.webhooks[1] = {
        "topic": "orders/create", "address": "https://atlas.example.com/x", "format": "json"
    }
    r = client.get(f"/api/channels/{bid}/remote-webhooks", headers=_login("admin"))
    assert r.status_code == 200
    assert r.json() == {
        "items": [
            {"remoteId": "1", "topic": "orders/create",
             "address": "https://atlas.example.com/x"}
        ]
    }


def test_get_remote_webhooks_unauthorized_folded(_setup):
    _fake, services = _setup
    bid = _connected_binding(_login("admin"))

    class _Raise401:
        def request(self, *a, **k):
            return TransportResponse(401, {}, json.dumps({}))

    services.channel_registry._transport = _Raise401()
    r = client.get(f"/api/channels/{bid}/remote-webhooks", headers=_login("admin"))
    assert r.status_code == 200
    assert r.json() == {"items": [], "error": "CHANNEL_UNAUTHORIZED"}
    assert services.channel_registry.get(bid)["status"] == "error"


def test_register_remote_webhook_201_with_pinned_address(_setup):
    fake, _services = _setup
    bid = _connected_binding(_login("admin"))
    r = client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "orders/create"}, headers=_login("admin"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["remoteId"] == "1" and body["topic"] == "orders/create"
    assert body["address"] == (
        f"https://atlas.example.com/api/channels/hooks/shopify/{bid}"
    )
    sent = fake.calls[-1]["json_body"]["webhook"]
    assert sent["format"] == "json"
    assert sent["address"].endswith(f"/api/channels/hooks/shopify/{bid}")


def test_register_remote_webhook_409(_setup):
    fake, _services = _setup
    bid = _connected_binding(_login("admin"))
    address = f"https://atlas.example.com/api/channels/hooks/shopify/{bid}"
    fake.webhooks[1] = {"topic": "orders/create", "address": address, "format": "json"}
    r = client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "orders/create"}, headers=_login("admin"),
    )
    assert r.status_code == 409


def test_register_remote_webhook_422_bad_topic(_setup):
    bid = _connected_binding(_login("admin"))
    r = client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "products/create"}, headers=_login("admin"),
    )
    assert r.status_code == 422


def test_register_remote_webhook_422_public_url_not_https(_setup, monkeypatch):
    bid = _connected_binding(_login("admin"))
    monkeypatch.setenv("ATLAS_PUBLIC_URL", "http://localhost:5174")
    r = client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "orders/create"}, headers=_login("admin"),
    )
    assert r.status_code == 422
    assert "HTTPS" in r.json()["detail"]


def test_register_remote_webhook_404_unknown_binding(_setup):
    r = client.post(
        "/api/channels/ch-nope/remote-webhooks",
        json={"topic": "orders/create"}, headers=_login("admin"),
    )
    assert r.status_code == 404


def test_role_gates_viewer_403_operator_201(_setup):
    bid = _connected_binding(_login("admin"))
    viewer = _login("viewer")
    assert client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "orders/create"}, headers=viewer,
    ).status_code == 403
    operator = _login("operator")
    assert client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "refunds/create"}, headers=operator,
    ).status_code == 201


def test_unregister_remote_webhook_idempotent(_setup):
    fake, _services = _setup
    bid = _connected_binding(_login("admin"))
    admin = _login("admin")
    r = client.delete(
        f"/api/channels/{bid}/remote-webhooks/orders/create", headers=admin
    )
    assert r.status_code == 200 and r.json() == {"deleted": False}

    client.post(
        f"/api/channels/{bid}/remote-webhooks",
        json={"topic": "orders/create"}, headers=admin,
    )
    r = client.delete(
        f"/api/channels/{bid}/remote-webhooks/orders/create", headers=admin
    )
    assert r.json() == {"deleted": True}
    assert fake.webhooks == {}


def test_demo_mock_shopify_admin_webhooks_lifecycle(_setup):
    r = client.get("/api/demo/mock/shopify-admin/webhooks.json")
    assert r.status_code == 200 and r.json() == {"webhooks": []}

    r = client.post(
        "/api/demo/mock/shopify-admin/webhooks.json",
        json={"webhook": {"topic": "orders/create",
                          "address": "https://atlas.example.com/h", "format": "json"}},
    )
    assert r.status_code == 200
    created = r.json()["webhook"]
    assert created["id"] >= 1

    r = client.post(
        "/api/demo/mock/shopify-admin/webhooks.json",
        json={"webhook": {"topic": "orders/create",
                          "address": "https://atlas.example.com/h", "format": "json"}},
    )
    assert r.status_code == 422

    r = client.post(
        "/api/demo/mock/shopify-admin/webhooks.json",
        json={"webhook": {"topic": "orders/create"}},
    )
    assert r.status_code == 422

    assert client.delete(
        f"/api/demo/mock/shopify-admin/webhooks/{created['id']}.json"
    ).status_code == 200
    assert client.delete(
        f"/api/demo/mock/shopify-admin/webhooks/{created['id']}.json"
    ).status_code == 404


def test_demo_reset_clears_mock_shopify_admin_webhooks(_setup):
    admin = _login("admin")
    client.post(
        "/api/demo/mock/shopify-admin/webhooks.json",
        json={"webhook": {"topic": "orders/create",
                          "address": "https://atlas.example.com/h", "format": "json"}},
    )
    client.post("/api/demo/reset", headers=admin)
    assert client.get(
        "/api/demo/mock/shopify-admin/webhooks.json"
    ).json() == {"webhooks": []}

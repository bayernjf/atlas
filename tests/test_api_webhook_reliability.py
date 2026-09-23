# -*- coding: utf-8 -*-
"""入站可靠性 REST 测试（docs/40 §1C，I 类，U370 区段）。"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)

CLIENT_SECRET = "shpss_reliability-secret"

CONN_BODY = {
    "provider": "generic",
    "displayName": "可靠性测试 OAuth",
    "authUrl": "https://sso.example.com/auth",
    "tokenUrl": "https://sso.example.com/token",
    "clientId": "cid-reliability",
    "clientSecret": CLIENT_SECRET,
    "scopes": ["read"],
}


class _AllowEgress:
    def check(self, url: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _setup():
    import atlas.connections.service as cs
    from atlas.iam.deps import tenant_registry

    cs._egress_guard = _AllowEgress()
    admin = _login("admin")
    client.post("/api/demo/reset", headers=admin)
    services = tenant_registry.get("t1")
    services.connection_service._egress = _AllowEgress()
    for item in services.connection_service.list():
        services.connection_service.delete(item["id"])
    for item in services.channel_registry.list():
        services.channel_registry.delete(item["id"])
    rows = getattr(services.webhook_deliveries, "_rows", None)
    if rows is not None:
        rows.clear()
    other = tenant_registry.peek("t2")
    if other is not None:
        other_rows = getattr(other.webhook_deliveries, "_rows", None)
        if other_rows is not None:
            other_rows.clear()
    yield
    client.headers.pop("authorization", None)


def _login(role: str, tenant: str = "t1") -> dict[str, str]:
    users = {
        ("t1", "admin"): ("admin-a", "admin123"),
        ("t1", "operator"): ("operator-a", "operator123"),
        ("t1", "viewer"): ("viewer-a", "viewer123"),
        ("t2", "admin"): ("admin-b", "admin123"),
    }
    username, password = users[(tenant, role)]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _sample_graph():
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/orders"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "shopify/getOrder"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }


def _create_binding(headers) -> str:
    cid = client.post("/api/connections", json=CONN_BODY, headers=headers).json()["id"]
    return client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": cid, "config": {"shop": "acme"}},
        headers=headers,
    ).json()["id"]


def _sign(body: bytes) -> str:
    digest = hmac.new(CLIENT_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _post_hook(bid: str, body: bytes, webhook_id: str):
    headers = {
        "X-Shopify-Shop-Domain": "acme.myshopify.com",
        "X-Shopify-Topic": "orders/create",
        "X-Shopify-Webhook-Id": webhook_id,
        "X-Shopify-Hmac-SHA256": _sign(body),
    }
    return client.post(
        f"/api/channels/hooks/shopify/{bid}", content=body, headers=headers
    )


def _seed_dead(admin, webhook_id="wh-dead-1"):
    bid = _create_binding(admin)
    graph_id = client.post("/api/graphs", json=_sample_graph(), headers=admin).json()["id"]
    client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "orders/create", "graphId": graph_id, "enabled": True}
        ]},
        headers=admin,
    )
    body = b'{"id": 8801}'
    r = _post_hook(bid, body, webhook_id)
    assert r.status_code == 200 and r.json() == {"received": True}
    return bid, graph_id


def test_unpublished_subscription_produces_dead_letter():
    admin = _login("admin")
    _seed_dead(admin)
    r = client.get("/api/channels/webhooks/dead-letters", headers=admin)
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["webhookId"] == "wh-dead-1"
    assert item["topic"] == "orders/create"
    assert item["reasons"] == [
        {"graphId": item["reasons"][0]["graphId"], "code": "NO_PUBLISHED_VERSION"}
    ]
    assert "data" not in item and "payload" not in item


def test_dead_letter_filters_topic_and_binding():
    admin = _login("admin")
    bid, _ = _seed_dead(admin)
    assert client.get(
        "/api/channels/webhooks/dead-letters?topic=refunds/create", headers=admin
    ).json()["items"] == []
    filtered = client.get(
        f"/api/channels/webhooks/dead-letters?bindingId={bid}", headers=admin
    ).json()["items"]
    assert len(filtered) == 1
    assert client.get(
        "/api/channels/webhooks/dead-letters?bindingId=ch-other", headers=admin
    ).json()["items"] == []


def test_metrics_aggregate_totals_and_duplicates():
    admin = _login("admin")
    _seed_dead(admin)
    _post_hook(_binding_id(admin), b'{"id":8802}', "wh-dead-1")
    metrics = client.get("/api/channels/webhooks/metrics", headers=admin).json()
    assert metrics["totals"] == {"received": 0, "dead": 1, "duplicates": 1}
    assert metrics["byTopic"]["orders/create"] == {
        "received": 0, "dead": 1, "duplicates": 1,
    }


def _binding_id(admin):
    items = client.get("/api/channels/webhooks/dead-letters", headers=admin).json()["items"]
    return items[0]["bindingId"]


def test_replay_after_publish_recovers_dead_letter():
    from atlas.iam.deps import tenant_registry
    from atlas.routing.store import RolloutState

    admin = _login("admin")
    _, graph_id = _seed_dead(admin, webhook_id="wh-recover")
    client.post(f"/api/graphs/{graph_id}/publish", headers=admin)
    routing = tenant_registry.get("t1").routing_store
    routing._states[graph_id] = RolloutState(
        graph_id=graph_id, status="full", candidate=1
    )
    operator = _login("operator")
    r = client.post(
        "/api/channels/webhooks/dead-letters/wh-recover/replay", headers=operator
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received"
    assert client.get(
        "/api/channels/webhooks/dead-letters", headers=admin
    ).json()["items"] == []


def test_replay_unknown_returns_404():
    admin = _login("admin")
    r = client.post(
        "/api/channels/webhooks/dead-letters/missing/replay", headers=admin
    )
    assert r.status_code == 404


def test_replay_requires_operate_permission():
    admin = _login("admin")
    _seed_dead(admin, webhook_id="wh-perm")
    viewer = _login("viewer")
    assert client.post(
        "/api/channels/webhooks/dead-letters/wh-perm/replay", headers=viewer
    ).status_code == 403
    assert client.post(
        "/api/channels/webhooks/dead-letters/wh-perm/replay",
        headers=_login("operator"),
    ).status_code == 200


def test_delete_requires_administer_permission():
    admin = _login("admin")
    _seed_dead(admin, webhook_id="wh-del")
    assert client.delete(
        "/api/channels/webhooks/dead-letters/wh-del", headers=_login("viewer")
    ).status_code == 403
    assert client.delete(
        "/api/channels/webhooks/dead-letters/wh-del", headers=_login("operator")
    ).status_code == 403
    r = client.delete(
        "/api/channels/webhooks/dead-letters/wh-del", headers=admin
    )
    assert r.status_code == 200 and r.json() == {"deleted": True}


def test_dead_letters_are_tenant_scoped():
    admin = _login("admin")
    _seed_dead(admin)
    other = _login("admin", tenant="t2")
    assert client.get(
        "/api/channels/webhooks/dead-letters", headers=other
    ).json()["items"] == []
    metrics = client.get(
        "/api/channels/webhooks/metrics", headers=other
    ).json()
    assert metrics["totals"] == {"received": 0, "dead": 0, "duplicates": 0}

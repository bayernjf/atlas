# -*- coding: utf-8 -*-
"""入站 webhook REST 测试（docs/39 §5，I 类，U370 区段）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)

CLIENT_SECRET = "shpss_a-client-secret"

CONN_BODY = {
    "provider": "generic",
    "displayName": "测试店铺 OAuth",
    "authUrl": "https://sso.example.com/auth",
    "tokenUrl": "https://sso.example.com/token",
    "clientId": "cid-webhook",
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
    yield
    client.headers.pop("authorization", None)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_binding(headers) -> tuple[str, str]:
    cid = client.post("/api/connections", json=CONN_BODY, headers=headers).json()["id"]
    bid = client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": cid, "config": {"shop": "acme"}},
        headers=headers,
    ).json()["id"]
    return cid, bid


def _sign(body: bytes, secret: str = CLIENT_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _wh_headers(topic: str = "orders/create", webhook_id: str = "wh-1",
                signature: str | None = None, body: bytes = b"") -> dict[str, str]:
    headers = {
        "X-Shopify-Shop-Domain": "acme.myshopify.com",
        "X-Shopify-Topic": topic,
        "X-Shopify-Webhook-Id": webhook_id,
    }
    if signature is not None:
        headers["X-Shopify-Hmac-SHA256"] = signature
    return headers


def _post_hook(bid: str, body: bytes, headers: dict):
    return client.post(
        f"/api/channels/hooks/shopify/{bid}", content=body, headers=headers
    )


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


def test_ingress_unknown_binding_returns_404():
    body = b'{"id":1}'
    r = _post_hook("ch-999", body, _wh_headers(signature=_sign(body), body=body))
    assert r.status_code == 404


def test_ingress_missing_or_bad_signature_returns_401():
    body = b'{"id":1}'
    _, bid = _create_binding(_login("admin"))
    assert _post_hook(bid, body, _wh_headers(body=body)).status_code == 401
    bad = _wh_headers(signature=_sign(body, "wrong-secret"), body=body)
    assert _post_hook(bid, body, bad).status_code == 401


def test_ingress_secret_unavailable_returns_503():
    from atlas.iam.deps import tenant_registry

    admin = _login("admin")
    cid, bid = _create_binding(admin)
    tenant_registry.get("t1").connection_service._store.get(cid).client_secret_envelope = None
    body = b'{"id":1}'
    r = _post_hook(bid, body, _wh_headers(signature=_sign(body), body=body))
    assert r.status_code == 503


def test_ingress_valid_signature_without_subscription_ignored():
    _, bid = _create_binding(_login("admin"))
    body = b'{"id":1}'
    r = _post_hook(bid, body, _wh_headers(signature=_sign(body), body=body))
    assert r.status_code == 200 and r.json() == {"ignored": True}


def test_ingress_duplicate_webhook_id_returns_duplicate():
    admin = _login("admin")
    _, bid = _create_binding(admin)
    # docs/40：ignored 投递不留行，重复投递仍判 ignored（不做跨重试计数）
    body = b'{"id":1}'
    first = _post_hook(bid, body, _wh_headers(webhook_id="wh-dup",
                                              signature=_sign(body), body=body))
    assert first.json() == {"ignored": True}
    second = _post_hook(bid, body, _wh_headers(webhook_id="wh-dup",
                                               signature=_sign(body), body=body))
    assert second.status_code == 200 and second.json() == {"ignored": True}


def test_ingress_missing_header_after_verified_signature_returns_400():
    _, bid = _create_binding(_login("admin"))
    body = b'{"id":1}'
    headers = _wh_headers(signature=_sign(body), body=body)
    del headers["X-Shopify-Topic"]
    assert _post_hook(bid, body, headers).status_code == 400


def test_get_subscriptions_allows_read_put_requires_administer():
    admin = _login("admin")
    _, bid = _create_binding(admin)
    assert client.get(f"/api/channels/{bid}/webhooks", headers=_login("viewer")).status_code == 200
    put_body = {"subscriptions": []}
    assert client.put(f"/api/channels/{bid}/webhooks", json=put_body,
                      headers=_login("viewer")).status_code == 403
    assert client.put(f"/api/channels/{bid}/webhooks", json=put_body,
                      headers=_login("operator")).status_code == 403


def test_put_validates_fields_and_aggregates_errors():
    admin = _login("admin")
    _, bid = _create_binding(admin)
    r = client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "products/create", "graphId": "graph-x", "enabled": True},
            {"topic": "orders/create", "graphId": "graph-missing", "enabled": "yes"},
        ]},
        headers=admin,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("products/create" in line for line in detail)
    assert any("graph-missing" in line for line in detail)
    assert any("enabled" in line for line in detail)


def test_put_persists_valid_subscriptions():
    admin = _login("admin")
    _, bid = _create_binding(admin)
    graph_id = client.post("/api/graphs", json=_sample_graph(), headers=admin).json()["id"]
    r = client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "orders/create", "graphId": graph_id, "enabled": True}
        ]},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [
        {"topic": "orders/create", "graphId": graph_id, "enabled": True}
    ]
    got = client.get(f"/api/channels/{bid}/webhooks", headers=admin).json()
    assert got["items"][0]["graphId"] == graph_id


def test_put_rejects_duplicate_pair_and_over_limit():
    admin = _login("admin")
    _, bid = _create_binding(admin)
    graph_id = client.post("/api/graphs", json=_sample_graph(), headers=admin).json()["id"]
    dup = client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "orders/create", "graphId": graph_id},
            {"topic": "orders/create", "graphId": graph_id},
        ]},
        headers=admin,
    )
    assert dup.status_code == 422 and any("重复" in line for line in dup.json()["detail"])
    many = client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "orders/create", "graphId": graph_id, "enabled": i % 2 == 0}
            for i in range(11)
        ]},
        headers=admin,
    )
    assert many.status_code == 422 and any("10" in line for line in many.json()["detail"])


def test_ingress_signed_delivery_triggers_published_graph():
    from atlas.iam.deps import tenant_registry

    admin = _login("admin")
    _, bid = _create_binding(admin)
    saved = client.post("/api/graphs", json=_sample_graph(), headers=admin).json()
    graph_id = saved["id"]
    client.post(f"/api/graphs/{graph_id}/publish", headers=admin)
    routing = tenant_registry.get("t1").routing_store
    # 直接置 full 钉版态（绕过 canary 状态机；它要求两个发布版本）
    from atlas.routing.store import RolloutState

    routing._states[graph_id] = RolloutState(
        graph_id=graph_id, status="full", candidate=1
    )
    client.put(
        f"/api/channels/{bid}/webhooks",
        json={"subscriptions": [
            {"topic": "orders/create", "graphId": graph_id, "enabled": True}
        ]},
        headers=admin,
    )

    body = b'{"id": 5588}'
    r = _post_hook(bid, body, _wh_headers(webhook_id="wh-run-1",
                                          signature=_sign(body), body=body))
    assert r.status_code == 200 and r.json() == {"received": True}

    run_store = tenant_registry.get("t1").run_store
    deadline = time.time() + 3
    while time.time() < deadline:
        runs = [run_store.get(rid) for rid in run_store._order]
        if any(run["graphId"] == graph_id and run["status"] != "running" for run in runs):
            matched = [run for run in runs if run["graphId"] == graph_id]
            assert matched[-1]["status"] in {"completed", "failed"}
            break
        time.sleep(0.03)
    else:
        pytest.fail("webhook-triggered run did not finish in time")

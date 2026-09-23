# -*- coding: utf-8 -*-
"""wait 事件信号 REST 端点测试（docs/47 §4；13 U302）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry

anon = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")


def _register(event_key: str = "order_paid", *, tenant: str = "t1") -> str:
    return tenant_registry.get(tenant).event_wait_broker.request(
        event_key=event_key, node_id="wait-1", graph_id="g1", timeout_seconds=60
    )


def test_get_waits_requires_login():
    assert anon.get("/api/waits").status_code == 401


def test_viewer_can_read_but_not_signal():
    response = anon.get("/api/waits", headers=VIEWER_A)
    assert response.status_code == 200
    assert "items" in response.json()
    assert anon.post(
        "/api/waits/events", headers=VIEWER_A, json={"eventKey": "k"}
    ).status_code == 403


def test_signal_event_releases_pending():
    token = _register("order_paid_a")
    try:
        listing = anon.get("/api/waits", headers=OPERATOR_A).json()["items"]
        assert any(item["token"] == token for item in listing)

        response = anon.post(
            "/api/waits/events",
            headers=OPERATOR_A,
            json={"eventKey": "order_paid_a", "payload": {"paid": True}},
        )
        assert response.status_code == 200
        assert response.json() == {"released": 1}
        broker = tenant_registry.get("t1").event_wait_broker
        assert broker.wait(token) == {
            "paid": True,
            "matchedEventKey": "order_paid_a",
        }  # docs/54
    finally:
        tenant_registry.get("t1").event_wait_broker.reset()


def test_signal_event_without_pending_returns_zero():
    response = anon.post(
        "/api/waits/events", headers=OPERATOR_A, json={"eventKey": "nobody_home"}
    )
    assert response.json() == {"released": 0}


def test_signal_token_success_conflict_and_not_found():
    token = _register("token_case")
    try:
        ok = anon.post(
            f"/api/waits/{token}/signal",
            headers=OPERATOR_A,
            json={"payload": {"n": 1}},
        )
        assert ok.status_code == 200
        assert ok.json() == {"token": token, "released": True}

        conflict = anon.post(
            f"/api/waits/{token}/signal", headers=OPERATOR_A, json={}
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "WAIT_ALREADY_SIGNALED"
    finally:
        tenant_registry.get("t1").event_wait_broker.reset()

    missing = anon.post(
        "/api/waits/wait-deadbeef/signal", headers=OPERATOR_A, json={}
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "WAIT_TOKEN_NOT_FOUND"


def test_invalid_event_key_returns_422():
    for event_key in ("bad key", "", "k" * 129, "k/2"):
        response = anon.post(
            "/api/waits/events",
            headers=OPERATOR_A,
            json={"eventKey": event_key},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "WAIT_EVENT_KEY_INVALID"


def test_invalid_payload_returns_422():
    response = anon.post(
        "/api/waits/events",
        headers=OPERATOR_A,
        json={"eventKey": "k", "payload": "not-object"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WAIT_EVENT_PAYLOAD_INVALID"

    too_many_keys = anon.post(
        "/api/waits/events",
        headers=OPERATOR_A,
        json={"eventKey": "k", "payload": {str(i): 1 for i in range(51)}},
    )
    assert too_many_keys.status_code == 422

    too_large = anon.post(
        "/api/waits/events",
        headers=OPERATOR_A,
        json={"eventKey": "k", "payload": {"blob": "x" * 4100}},
    )
    assert too_large.status_code == 422


def test_empty_payload_defaults_to_object():
    token = _register("empty_payload")
    try:
        response = anon.post(
            f"/api/waits/{token}/signal",
            headers=OPERATOR_A,
            json={"payload": None},
        )
        assert response.status_code == 200
        broker = tenant_registry.get("t1").event_wait_broker
        assert broker.wait(token) == {"matchedEventKey": "empty_payload"}  # docs/54
    finally:
        tenant_registry.get("t1").event_wait_broker.reset()


def test_cross_tenant_signal_is_not_found_and_listing_isolated():
    token = _register("tenant_a_only", tenant="t1")
    try:
        response = anon.post(
            f"/api/waits/{token}/signal", headers=ADMIN_B, json={}
        )
        assert response.status_code == 404
        items = anon.get("/api/waits", headers=ADMIN_B).json()["items"]
        assert all(item["token"] != token for item in items)
    finally:
        tenant_registry.get("t1").event_wait_broker.reset()

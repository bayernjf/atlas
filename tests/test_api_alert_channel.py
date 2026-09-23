from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry

client = TestClient(app)


def _headers(username: str = "admin-a", password: str = "admin123") -> dict:
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _raw(**overrides) -> dict:
    raw = {
        "enabled": True,
        "channel": "dingtalk",
        "to": "https://robot.example.com/send",
        "secret": "",
        "minSeverity": "critical",
    }
    raw.update(overrides)
    return raw


def setup_function():
    tenant_registry.reset_tenant("t1")


def test_get_default_channel_is_disabled_with_null_delivery():
    payload = client.get("/api/monitoring/alert-channel", headers=_headers()).json()
    assert payload["enabled"] is False
    assert payload["channel"] == "dingtalk"
    assert payload["to"] == ""
    assert payload["lastDelivery"] is None


def test_put_valid_channel_echoes_updated_at():
    resp = client.put(
        "/api/monitoring/alert-channel", json=_raw(), headers=_headers()
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled"] is True
    assert payload["updatedAt"]
    assert client.get(
        "/api/monitoring/alert-channel", headers=_headers()
    ).json()["to"] == "https://robot.example.com/send"


def test_put_invalid_returns_chinese_422():
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(to="not-a-url"),
        headers=_headers(),
    )
    assert resp.status_code == 422
    assert "URL" in resp.json()["detail"]


def test_put_disabled_draft_skips_validation():
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(enabled=False, to="", secret=""),
        headers=_headers(),
    )
    assert resp.status_code == 200


def test_viewer_can_read_but_not_update():
    headers = _headers("viewer-a", "viewer123")
    assert client.get(
        "/api/monitoring/alert-channel", headers=headers
    ).status_code == 200
    assert client.put(
        "/api/monitoring/alert-channel", json=_raw(), headers=headers
    ).status_code == 403


def test_operator_cannot_update():
    headers = _headers("operator-a", "operator123")
    assert client.put(
        "/api/monitoring/alert-channel", json=_raw(), headers=headers
    ).status_code == 403


def test_missing_token_is_unauthorized():
    assert client.get("/api/monitoring/alert-channel").status_code == 401
    assert client.put(
        "/api/monitoring/alert-channel", json=_raw()
    ).status_code == 401


def test_get_shows_last_delivery_status():
    monitoring = tenant_registry.get("t1").monitoring
    monitoring.record_alert_channel_delivery(
        monitoring.get_alert_channel_delivery().model_copy(
            update={
                "lastNotifiedAt": "2026-09-23T01:00:00+00:00",
                "errorCode": "EGRESS_DENIED",
                "errorMessage": "blocked",
            }
        )
    )
    payload = client.get("/api/monitoring/alert-channel", headers=_headers()).json()
    assert payload["lastDelivery"] == {
        "lastNotifiedAt": "2026-09-23T01:00:00+00:00",
        "errorCode": "EGRESS_DENIED",
        "errorMessage": "blocked",
    }

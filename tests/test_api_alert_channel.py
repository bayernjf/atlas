import json

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.notify import AlertNotifier

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
    client.cookies.clear()  # 打包 ZQ Q4：httpOnly Cookie 由前序测试残留——未认证用例需干净 jar

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


# --- U1379：加签 secret HTTP 投影脱敏（docs/125 §3.2，审查发现 #3）---


class FakeMessages:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, channel, to, subject, body, secret=None):
        self.sent.append(
            {"channel": channel, "to": to, "subject": subject, "body": body, "secret": secret}
        )


def _configure_channel_with_secret(secret: str) -> None:
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret=secret),
        headers=_headers(),
    )
    assert resp.status_code == 200


def test_viewer_get_masks_secret_and_body_has_no_plaintext():
    _configure_channel_with_secret("SEC1234abcd")
    resp = client.get(
        "/api/monitoring/alert-channel", headers=_headers("viewer-a", "viewer123")
    )
    payload = resp.json()
    assert payload["secret"] == "***abcd"
    assert "secret" in payload
    assert "SEC1234abcd" not in json.dumps(payload, ensure_ascii=False)


def test_put_echoes_masked_secret_and_keeps_key():
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret="SEC1234abcd"),
        headers=_headers(),
    )
    payload = resp.json()
    assert "secret" in payload
    assert payload["secret"] == "***abcd"


def test_put_masked_placeholder_preserves_stored_secret():
    _configure_channel_with_secret("SEC1234abcd")
    masked = client.get(
        "/api/monitoring/alert-channel", headers=_headers()
    ).json()["secret"]
    assert masked == "***abcd"

    # 前端未改密钥直接保存：脱敏占位原样提交，归一化为库内现存明文
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret=masked),
        headers=_headers(),
    )
    assert resp.json()["secret"] == "***abcd"
    assert (
        tenant_registry.get("t1").monitoring.get_alert_channel().secret
        == "SEC1234abcd"
    )


def test_put_empty_string_clears_secret():
    _configure_channel_with_secret("SEC1234abcd")
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret=""),
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["secret"] == ""
    assert (
        tenant_registry.get("t1").monitoring.get_alert_channel().secret == ""
    )


def test_short_secret_mask_uses_whole_secret_as_suffix():
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret="ab"),
        headers=_headers(),
    )
    assert resp.json()["secret"] == "***ab"
    # 占位回显提交同样不改短密钥
    client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret="***ab"),
        headers=_headers(),
    )
    assert tenant_registry.get("t1").monitoring.get_alert_channel().secret == "ab"


def test_bare_mask_token_is_not_treated_as_placeholder():
    _configure_channel_with_secret("SEC1234abcd")
    # 裸 "***" 不等于任何投影（非空投影至少 4 字符），按字面新密钥落库
    resp = client.put(
        "/api/monitoring/alert-channel",
        json=_raw(secret="***"),
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert tenant_registry.get("t1").monitoring.get_alert_channel().secret == "***"
    # 库内 "***"（3 字符）自身的投影为 6 个星
    assert resp.json()["secret"] == "******"


def test_outbound_signing_still_uses_plaintext_secret():
    _configure_channel_with_secret("SEC1234abcd")
    store = tenant_registry.get("t1").monitoring
    messages = FakeMessages()
    store.set_notifier(
        AlertNotifier(messages, clock=lambda: "2026-09-23T01:00:00+00:00")
    )
    store.record_run(
        graph_id="g1",
        mode="sync",
        status="error",
        started_at="2026-09-23T00:00:00+00:00",
        duration_ms=42,
        nodes=[
            NodeResult(
                node_id="n1", node_type="tool_call", status="failed", error="boom"
            )
        ],
        error="run failed",
    )
    assert len(messages.sent) == 1
    assert messages.sent[0]["secret"] == "SEC1234abcd"

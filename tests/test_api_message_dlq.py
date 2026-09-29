# -*- coding: utf-8 -*-
"""打包 U：DLQ 端点 HTTP 映射与鉴权（docs/82；U960、U962）。

失败行直接经 t1 的 MessageService（假 sender）制造，再用显式角色 Bearer 打端点。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate
from atlas.message.service import DeliveryRecord

client = TestClient(app)


def _token(username: str, password: str) -> str:
    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


def _auth(username: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(username, password)}"}


VIEWER_A = _auth("viewer-a", "viewer123")
OPERATOR_A = _auth("operator-a", "operator123")
ADMIN_B = _auth("admin-b", "admin123")


class _FailingWebhook:
    def send(self, url, payload, secret=None):
        raise RuntimeError("connection refused")


class _WorkingWebhook:
    def send(self, url, payload, secret=None):
        pass


@pytest.fixture()
def t1_service():
    service = tenant_registry.get("t1").message_service
    service.reset()
    service._retry_delays = ()
    service._sleep = lambda _seconds: None
    yield service
    service._webhook_sender = None
    service.reset()


def _failed_row(service) -> dict:
    service._webhook_sender = _FailingWebhook()
    with pytest.raises(Exception):
        service.send("webhook", "https://example.com/hook", "subject", "body")
    return service.list_deliveries(100, status="failed")[0]


# ---- U962：鉴权与过滤 ----


def test_viewer_can_list_and_filter(t1_service):
    _failed_row(t1_service)
    resp = client.get("/api/demo/deliveries?status=failed", headers=VIEWER_A)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["status"] == "failed"


def test_viewer_cannot_replay(t1_service):
    row = _failed_row(t1_service)
    resp = client.post(
        f"/api/demo/deliveries/{row['seq']}/replay", headers=VIEWER_A
    )
    assert resp.status_code == 403


def test_invalid_status_422():
    resp = client.get("/api/demo/deliveries?status=bogus", headers=VIEWER_A)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"


def test_operator_replays_successfully(t1_service):
    row = _failed_row(t1_service)
    t1_service._webhook_sender = _WorkingWebhook()
    resp = client.post(
        f"/api/demo/deliveries/{row['seq']}/replay", headers=OPERATOR_A
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["replayOf"] == row["seq"]
    assert body["deliveries"][0]["status"] == "delivered:webhook"


# ---- U960：404/409/422 ----


def test_replay_missing_seq_404():
    resp = client.post("/api/demo/deliveries/999999/replay", headers=OPERATOR_A)
    assert resp.status_code == 404


def test_replay_cross_tenant_404(t1_service):
    row = _failed_row(t1_service)
    resp = client.post(
        f"/api/demo/deliveries/{row['seq']}/replay", headers=ADMIN_B
    )
    assert resp.status_code == 404


def test_replay_non_failed_409(t1_service):
    row = _failed_row(t1_service)
    t1_service._webhook_sender = _WorkingWebhook()
    first = client.post(
        f"/api/demo/deliveries/{row['seq']}/replay", headers=OPERATOR_A
    )
    assert first.status_code == 200
    delivered_seq = first.json()["deliveries"][0]["seq"]
    second = client.post(
        f"/api/demo/deliveries/{delivered_seq}/replay", headers=OPERATOR_A
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "DLQ_NOT_FAILED"


def test_replay_null_body_422(t1_service):
    store = t1_service._delivery_store
    store.record(
        DeliveryRecord(
            id="legacy-id",
            channel="webhook",
            to=["https://example.com/hook"],
            subject="s",
            sentAt=datetime.now(timezone.utc).isoformat(),
            status="failed",
            attempts=3,
            elapsedMs=4,
            body="",
        )
    )
    row = t1_service.list_deliveries(100, status="failed")[0]
    resp = client.post(
        f"/api/demo/deliveries/{row['seq']}/replay", headers=OPERATOR_A
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DLQ_BODY_UNAVAILABLE"

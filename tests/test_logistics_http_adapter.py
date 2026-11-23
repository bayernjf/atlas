"""T3 物流 HTTP 适配器单测：契约、非 2xx、坏 JSON、缺配置。"""

from __future__ import annotations

import httpx
import pytest

from atlas.harness.base import ActionRequest, Permission
from atlas.logistics.http_adapter import HttpLogisticsAdapter


def _adapter(handler) -> HttpLogisticsAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpLogisticsAdapter(
        base_url="http://logistics.local",
        client=client,
        granted_permissions={Permission.READ, Permission.WRITE},
    )


def test_check_and_sign_receipt_over_http():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"order_id": "T3-1", "signed": True})
        return httpx.Response(200, json={"order_id": "T3-1", "signed": False})

    adapter = _adapter(handler)
    check = adapter.execute(ActionRequest("check_receipt", {"order_id": "T3-1"}))
    assert check.status.value == "SUCCESS"
    assert check.output == {"order_id": "T3-1", "signed": False}
    assert calls[-1] == ("GET", "/receipts/T3-1")

    sign = adapter.execute(ActionRequest("sign_receipt", {"order_id": "T3-1"}))
    assert sign.status.value == "SUCCESS"
    assert sign.output == {"order_id": "T3-1", "signed": True}
    assert calls[-1] == ("POST", "/receipts/T3-1/sign")


def test_http_error_and_bad_body_are_structured_failures():
    adapter = _adapter(lambda request: httpx.Response(503, text="down"))
    result = adapter.execute(ActionRequest("check_receipt", {"order_id": "T3-2"}))
    assert result.status.value == "FAILED"
    assert result.error.code == "LOGISTICS_HTTP_ERROR"

    adapter = _adapter(lambda request: httpx.Response(200, json={"order_id": "T3-2"}))
    result = adapter.execute(ActionRequest("check_receipt", {"order_id": "T3-2"}))
    assert result.status.value == "FAILED"
    assert result.error.code == "LOGISTICS_RESPONSE_INVALID"


def test_missing_config_or_order_id_fails_closed(monkeypatch):
    monkeypatch.delenv("ATLAS_LOGISTICS_URL", raising=False)
    adapter = HttpLogisticsAdapter(base_url="", granted_permissions={Permission.READ})
    result = adapter.execute(ActionRequest("check_receipt", {"order_id": "T3-3"}))
    assert result.status.value == "FAILED"
    assert result.error.code == "LOGISTICS_NOT_CONFIGURED"

    adapter = _adapter(lambda request: httpx.Response(200, json={"order_id": "x", "signed": True}))
    result = adapter.execute(ActionRequest("check_receipt", {}))
    assert result.status.value == "FAILED"
    assert result.error.code == "MISSING_PARAMETER"


def test_observe_exposes_base_url_and_last_response():
    adapter = _adapter(lambda request: httpx.Response(200, json={"order_id": "T3-4", "signed": True}))
    adapter.execute(ActionRequest("check_receipt", {"order_id": "T3-4"}))
    obs = adapter.observe()
    assert obs.url == "http://logistics.local"
    assert obs.data["last_response"]["status"] == 200

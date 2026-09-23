# -*- coding: utf-8 -*-
"""渠道绑定注册服务测试（docs/38 §5；fake 连接服务 + fake 传输）。"""

from __future__ import annotations

import json

import pytest

from atlas.channels.adapter import ShopifyHarnessAdapter
from atlas.channels.base import ChannelError, TransportResponse
from atlas.channels.memory import ChannelStore
from atlas.channels.registry import ChannelRegistry
from atlas.connections.service import ConnectionServiceError
from atlas.harness.base import ActionRequest, Permission


class FakeConnectionService:
    def __init__(self, tokens: dict[str, str | None]):
        self._tokens = tokens

    def get(self, conn_id: str):
        if conn_id not in self._tokens:
            raise ConnectionServiceError("CONNECTION_NOT_FOUND", "x", 404)
        return {"id": conn_id, "provider": "generic-oauth2", "status": "connected"}

    def access_token_for(self, conn_id: str) -> str | None:
        return self._tokens.get(conn_id)


def fake_transport(payload, status=200):
    class T:
        def __init__(self):
            self.calls = []

        def request(self, method, url, *, headers, json_body, timeout):
            self.calls.append((method, url, json_body))
            return TransportResponse(status, {}, json.dumps(payload))

    return T()


def make_registry(tokens=None, transport=None):
    store = ChannelStore()
    conns = FakeConnectionService(tokens if tokens is not None else {"conn-1": "shpat-x"})
    return ChannelRegistry(
        store, tenant_id="t1", connection_service=conns, transport=transport
    )


def test_bind_success():
    reg = make_registry()
    view = reg.bind("shopify", "conn-1", {"shop": "Acme"})
    assert view["id"] == "ch-1"
    assert view["provider"] == "shopify"
    assert view["config"] == {"shop": "acme", "apiVersion": "2025-01"}
    assert view["status"] == "connected"


def test_bind_unknown_provider():
    reg = make_registry()
    with pytest.raises(ChannelError) as info:
        reg.bind("amazon", "conn-1", {"shop": "acme"})
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"


def test_bind_missing_connection():
    reg = make_registry(tokens={})
    with pytest.raises(ChannelError) as info:
        reg.bind("shopify", "conn-9", {"shop": "acme"})
    assert info.value.code == "CHANNEL_NOT_BOUND"
    assert info.value.status_code == 404


def test_bind_duplicate_connection_conflict():
    reg = make_registry()
    reg.bind("shopify", "conn-1", {"shop": "acme"})
    with pytest.raises(ChannelError) as info:
        reg.bind("shopify", "conn-1", {"shop": "other"})
    assert info.value.code == "CHANNEL_ALREADY_BOUND"
    assert info.value.status_code == 409


def test_bind_bad_shop_and_api_version():
    reg = make_registry()
    with pytest.raises(ChannelError):
        reg.bind("shopify", "conn-1", {"shop": "a b"})
    with pytest.raises(ChannelError):
        reg.bind("shopify", "conn-1", {"shop": "acme", "apiVersion": "v99"})


def test_client_for_with_token():
    transport = fake_transport({"shop": {"id": 1, "name": "Acme", "domain": "x"}})
    reg = make_registry(transport=transport)
    view = reg.bind("shopify", "conn-1", {"shop": "acme"})
    client = reg.client_for(reg._require(view["id"]))
    assert client.get_shop()["name"] == "Acme"


def test_client_for_without_token_marks_binding_error():
    reg = make_registry(tokens={"conn-1": None})
    view = reg.bind("shopify", "conn-1", {"shop": "acme"})
    binding = reg._require(view["id"])
    with pytest.raises(ChannelError) as info:
        reg.client_for(binding)
    assert info.value.code == "CHANNEL_UNAUTHORIZED"
    assert reg.get(view["id"])["status"] == "error"
    assert reg.get(view["id"])["lastError"]


def test_test_binding_ok_and_failure():
    transport = fake_transport({"shop": {"id": 1, "name": "Acme", "domain": "x"}})
    reg = make_registry(transport=transport)
    view = reg.bind("shopify", "conn-1", {"shop": "acme"})
    out = reg.test(view["id"])
    assert out == {"ok": True, "status": "connected"}

    bad_transport = fake_transport({}, status=401)
    reg2 = make_registry(transport=bad_transport)
    v2 = reg2.bind("shopify", "conn-1", {"shop": "acme"})
    out2 = reg2.test(v2["id"])
    assert out2["ok"] is False
    assert out2["status"] == "error"
    assert reg2.get(v2["id"])["status"] == "error"
    assert reg2.get(v2["id"])["lastError"]


def test_adapter_capabilities_dispatch_gate_and_observe():
    order = {
        "id": 1, "name": "#1", "financial_status": "paid",
        "fulfillment_status": None, "total_price": "10.00", "currency": "USD",
        "created_at": None, "email": None,
    }

    class Transport:
        def request(self, method, url, *, headers, json_body, timeout):
            if url.endswith("/orders.json?status=open&limit=50"):
                return TransportResponse(200, {}, json.dumps({"orders": [order]}))
            if url.endswith("/orders/1.json"):
                return TransportResponse(
                    200, {}, json.dumps({"order": {**order, "line_items": []}})
                )
            if url.endswith("/orders/1/refunds.json"):
                return TransportResponse(
                    200, {}, json.dumps({"refund": {"id": 5, "status": "refunded"}})
                )
            return TransportResponse(404, {}, "{}")

    reg = make_registry(transport=Transport())
    view = reg.bind("shopify", "conn-1", {"shop": "acme"})
    adapter = ShopifyHarnessAdapter(view["id"], reg)

    names = {c.name: c for c in adapter.list_capabilities()}
    assert set(names) == {"list_orders", "get_order", "create_refund"}
    assert names["list_orders"].permission == Permission.READ
    assert names["create_refund"].permission == Permission.FINANCIAL

    result = adapter.execute(ActionRequest("list_orders", {}, {}))
    assert result.error is None
    assert result.output[0]["id"] == 1

    # 默认仅 READ：FINANCIAL 能力被权限门拦截
    denied = adapter.execute(
        ActionRequest("create_refund", {"order_id": "1", "amount": 5}, {})
    )
    assert denied.error is not None
    assert denied.error.code == "PERMISSION_DENIED"

    granted = ShopifyHarnessAdapter(
        view["id"], reg, granted_permissions={Permission.READ, Permission.FINANCIAL}
    )
    ok = granted.execute(
        ActionRequest("create_refund", {"order_id": "1", "amount": 5}, {})
    )
    assert ok.error is None
    assert ok.output == {"refundId": 5, "status": "refunded", "amount": 5.0}

    obs = adapter.observe()
    assert obs.data == {"shop": "acme", "apiVersion": "2025-01", "status": "connected"}


def test_get_list_require_and_delete():
    reg = make_registry()
    view = reg.bind("shopify", "conn-1", {"shop": "acme"})
    assert reg.list() == [view]
    assert reg.get(view["id"]) == view
    assert reg.delete(view["id"]) is True
    assert reg.list() == []
    with pytest.raises(ChannelError) as info:
        reg.get(view["id"])
    assert info.value.code == "CHANNEL_NOT_BOUND"
    assert info.value.status_code == 404

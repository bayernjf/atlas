# -*- coding: utf-8 -*-
"""Shopify 渠道客户端测试（docs/38 §5；fake 传输零触网）。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atlas.channels.base import TransportResponse
from atlas.channels.shopify import ShopifyChannelClient, normalize_shop
from atlas.channels.base import ChannelError


class FakeTransport:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": headers,
             "json_body": json_body, "timeout": timeout}
        )
        return self._handler(method, url, headers, json_body)

    @property
    def last(self):
        return self.calls[-1]


def resp(payload, status=200):
    return TransportResponse(status=status, headers={}, text=json.dumps(payload))


ORDER = {
    "id": 1, "name": "#1001", "email": "a@example.com",
    "financial_status": "paid", "fulfillment_status": None,
    "total_price": "100.00", "currency": "USD",
    "created_at": "2026-09-01T00:00:00Z",
    "line_items": [{"id": 11, "quantity": 2}],
}


def make_client(handler):
    transport = FakeTransport(handler)
    client = ShopifyChannelClient("acme", "shpat-secret", transport=transport)
    return client, transport


def test_normalize_shop_variants():
    assert normalize_shop("Acme") == "acme"
    assert normalize_shop("https://Acme.myshopify.com/") == "acme"
    assert normalize_shop("acme.myshopify.com/admin") == "acme"
    assert normalize_shop("cool-shop") == "cool-shop"


def test_normalize_shop_invalid():
    for bad in ("", "  ", "a b", "https://", "a_b", "-abc"):
        with pytest.raises(ChannelError) as info:
            normalize_shop(bad)
        assert info.value.code == "CHANNEL_INVALID_PARAMETER"


def test_invalid_api_version_and_token():
    with pytest.raises(ChannelError) as info:
        ShopifyChannelClient("acme", "t", api_version="2025/01")
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"
    with pytest.raises(ChannelError) as info:
        ShopifyChannelClient("acme", "")
    assert info.value.code == "CHANNEL_UNAUTHORIZED"


def test_list_orders_request_shape_and_projection():
    def handler(method, url, headers, json_body):
        return resp({"orders": [ORDER]})

    client, transport = make_client(handler)
    out = client.list_orders()
    assert out == [{
        "id": 1, "name": "#1001", "email": "a@example.com",
        "financialStatus": "paid", "fulfillStatus": None,
        "totalPrice": "100.00", "currency": "USD",
        "createdAt": "2026-09-01T00:00:00Z",
    }]
    call = transport.last
    assert call["method"] == "GET"
    assert call["url"] == (
        "https://acme.myshopify.com/admin/api/2025-01/orders.json?status=open&limit=50"
    )
    assert call["headers"]["X-Shopify-Access-Token"] == "shpat-secret"


def test_list_orders_clamps_limit_and_validates_status():
    client, transport = make_client(lambda *a: resp({"orders": []}))
    client.list_orders(limit=1000)
    assert "limit=250" in transport.last["url"]
    client.list_orders(limit=0)
    assert "limit=1" in transport.last["url"]
    with pytest.raises(ChannelError) as info:
        client.list_orders(status="bogus")
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"


def test_get_order():
    client, transport = make_client(
        lambda *a: resp({"order": ORDER})
    )
    out = client.get_order("1")
    assert out["id"] == 1 and out["financialStatus"] == "paid"
    assert transport.last["url"].endswith("/orders/1.json")


def test_missing_order_structure():
    client, _ = make_client(lambda *a: resp({}))
    with pytest.raises(ChannelError) as info:
        client.get_order("1")
    assert info.value.code == "CHANNEL_INVALID_RESPONSE"


def test_status_error_mapping():
    client, _ = make_client(lambda *a: resp({}, status=401))
    with pytest.raises(ChannelError) as info:
        client.list_orders()
    assert info.value.code == "CHANNEL_UNAUTHORIZED"

    client, _ = make_client(lambda *a: resp({}, status=403))
    with pytest.raises(ChannelError):
        client.list_orders()

    client, _ = make_client(lambda *a: resp({}, status=500))
    with pytest.raises(ChannelError) as info:
        client.list_orders()
    assert info.value.code == "CHANNEL_UPSTREAM_FAILED"


def test_bad_json_response():
    transport = FakeTransport(
        lambda *a: TransportResponse(200, {}, text="<html>")
    )
    client = ShopifyChannelClient("acme", "t", transport=transport)
    with pytest.raises(ChannelError) as info:
        client.list_orders()
    assert info.value.code == "CHANNEL_INVALID_RESPONSE"


def test_create_refund_full_payload():
    def handler(method, url, headers, json_body):
        if method == "GET":
            return resp({"order": ORDER})
        return resp({"refund": {"id": 99, "status": "refunded"}})

    client, transport = make_client(handler)
    out = client.create_refund("1", 100, reason="破损", full_refund=True)
    assert out == {"refundId": 99, "status": "refunded", "amount": 100.0}
    body = transport.last["json_body"]["refund"]
    assert body["note"] == "破损"
    assert body["notify"] is False
    assert body["refund_line_items"] == [
        {"line_item_id": 11, "quantity": 2, "restock_type": 0}
    ]
    assert body["transactions"] == [{"kind": "refund", "amount": "100.00"}]


def test_create_refund_partial_quantity_zero():
    def handler(method, url, headers, json_body):
        if method == "GET":
            return resp({"order": ORDER})
        return resp({"refund": {"id": 99, "status": "processing"}})

    client, transport = make_client(handler)
    client.create_refund("1", 30.5)
    body = transport.last["json_body"]["refund"]
    assert body["refund_line_items"] == [
        {"line_item_id": 11, "quantity": 0, "restock_type": 0}
    ]
    assert body["transactions"][0]["amount"] == "30.50"


def test_create_refund_amount_validation():
    client, _ = make_client(lambda *a: resp({"order": ORDER}))
    for bad in (0, -5):
        with pytest.raises(ChannelError) as info:
            client.create_refund("1", bad)
        assert info.value.code == "CHANNEL_INVALID_PARAMETER"
    with pytest.raises(ChannelError) as info:
        client.create_refund("1", 200)
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"
    with pytest.raises(ChannelError) as info:
        client.create_refund("1", "abc")
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"


def test_create_refund_note_truncated():
    def handler(method, url, headers, json_body):
        if method == "GET":
            return resp({"order": ORDER})
        return resp({"refund": {"id": 1, "status": "ok"}})

    client, transport = make_client(handler)
    client.create_refund("1", 10, reason="x" * 250)
    assert len(transport.last["json_body"]["refund"]["note"]) == 200


def test_get_shop():
    client, transport = make_client(
        lambda *a: resp({"shop": {"id": 7, "name": "Acme", "domain": "acme.myshopify.com"}})
    )
    assert client.get_shop() == {"id": 7, "name": "Acme", "domain": "acme.myshopify.com"}
    assert transport.last["url"].endswith("/shop.json")


def test_list_registered_webhooks_projection():
    rows = [
        {"id": 51, "topic": "orders/create", "address": "https://x.io/hook"},
        {"id": 52, "topic": "refunds/create", "address": "https://x.io/hook2"},
    ]
    client, transport = make_client(lambda *a: resp({"webhooks": rows}))
    assert client.list_registered_webhooks() == [
        {"remoteId": "51", "topic": "orders/create", "address": "https://x.io/hook"},
        {"remoteId": "52", "topic": "refunds/create", "address": "https://x.io/hook2"},
    ]
    assert transport.last["url"].endswith("/webhooks.json?limit=250")


def test_list_registered_webhooks_missing_structure():
    client, _ = make_client(lambda *a: resp({}))
    with pytest.raises(ChannelError) as info:
        client.list_registered_webhooks()
    assert info.value.code == "CHANNEL_INVALID_RESPONSE"


def test_register_webhook_body_and_projection():
    def handler(method, url, headers, json_body):
        return resp({"webhook": {"id": 77, "topic": "orders/create",
                                 "address": "https://x.io/hook"}})

    client, transport = make_client(handler)
    out = client.register_webhook(topic="orders/create", address="https://x.io/hook")
    assert out == {"remoteId": "77", "topic": "orders/create", "address": "https://x.io/hook"}
    call = transport.last
    assert call["method"] == "POST"
    assert call["url"].endswith("/webhooks.json")
    assert call["json_body"] == {
        "webhook": {"topic": "orders/create", "address": "https://x.io/hook", "format": "json"}
    }


def test_register_webhook_validation():
    client, _ = make_client(lambda *a: resp({"webhook": {}}))
    with pytest.raises(ChannelError) as info:
        client.register_webhook(topic="products/create", address="https://x.io/hook")
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"
    with pytest.raises(ChannelError) as info:
        client.register_webhook(topic="orders/create", address="http://x.io/hook")
    assert info.value.code == "CHANNEL_INVALID_PARAMETER"


def test_register_webhook_422_maps_to_already_registered():
    client, _ = make_client(lambda *a: resp({}, status=422))
    with pytest.raises(ChannelError) as info:
        client.register_webhook(topic="orders/create", address="https://x.io/hook")
    assert info.value.code == "CHANNEL_ALREADY_REGISTERED"


def test_delete_registered_webhook():
    client, transport = make_client(lambda *a: TransportResponse(200, {}, text=""))
    assert client.delete_registered_webhook("77") is True
    assert transport.last["method"] == "DELETE"
    assert transport.last["url"].endswith("/webhooks/77.json")

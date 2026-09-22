# -*- coding: utf-8 -*-
"""入站 webhook 内核测试（docs/39 §5，候选 U370 区段）。"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from atlas.channels.base import ChannelError
from atlas.channels.webhooks import (
    WebhookDeliverer,
    WebhookEnvelope,
    build_envelope,
    build_trigger_event,
    verify_shopify_hmac,
)

CLIENT_SECRET = "shpss_this-is-a-client-secret"


def _sign(body: bytes, secret: str = CLIENT_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _headers(topic: str = "orders/create", shop: str = "demo.myshopify.com",
             webhook_id: str = "wh-1", include: tuple[str, ...] = ("shop", "topic", "id")) -> dict:
    full = {
        "X-Shopify-Shop-Domain": shop,
        "X-Shopify-Topic": topic,
        "X-Shopify-Webhook-Id": webhook_id,
    }
    wanted = {"shop": "X-Shopify-Shop-Domain", "topic": "X-Shopify-Topic", "id": "X-Shopify-Webhook-Id"}
    return {key: value for key, value in full.items() if any(key == wanted[name] for name in include)}


def _envelope(topic: str = "orders/create", webhook_id: str = "wh-1") -> WebhookEnvelope:
    return WebhookEnvelope(
        shop_domain="demo.myshopify.com",
        topic=topic,
        webhook_id=webhook_id,
        triggered_at=None,
        data={"id": 1001},
    )


def _binding(*subs: dict) -> dict:
    return {"id": "ch-1", "webhook_subscriptions": list(subs)}


def _sub(topic: str = "orders/create", graph_id: str = "graph-1", enabled: bool = True) -> dict:
    return {"topic": topic, "graph_id": graph_id, "enabled": enabled}


def test_verify_hmac_accepts_valid_signature():
    body = b'{"id":1}'
    assert verify_shopify_hmac(body, _sign(body), CLIENT_SECRET) is True


def test_verify_hmac_rejects_missing_signature():
    assert verify_shopify_hmac(b"{}", None, CLIENT_SECRET) is False
    assert verify_shopify_hmac(b"{}", "", CLIENT_SECRET) is False


def test_verify_hmac_rejects_wrong_secret():
    body = b'{"id":1}'
    assert verify_shopify_hmac(body, _sign(body, "other-secret"), CLIENT_SECRET) is False


def test_verify_hmac_rejects_non_base64_signature():
    assert verify_shopify_hmac(b"{}", "not@@base64!!", CLIENT_SECRET) is False


@pytest.mark.parametrize("missing", ["shop", "topic", "id"])
def test_build_envelope_missing_required_header_raises(missing):
    include = tuple(name for name in ("shop", "topic", "id") if name != missing)
    with pytest.raises(ChannelError) as exc:
        build_envelope(_headers(include=include), {"id": 1})
    assert exc.value.code == "WEBHOOK_MALFORMED"
    assert exc.value.status_code == 400


def test_build_envelope_reads_headers_case_insensitively():
    envelope = build_envelope(
        {"x-shopify-shop-domain": "s.myshopify.com", "x-shopify-topic": "orders/create",
         "x-shopify-webhook-id": "wh-9", "x-shopify-triggered-at": "2026-09-23T00:00:00Z"},
        {"id": 1},
    )
    assert envelope.shop_domain == "s.myshopify.com"
    assert envelope.webhook_id == "wh-9"
    assert envelope.triggered_at == "2026-09-23T00:00:00Z"


@pytest.mark.parametrize("topic", ["orders/create", "orders/updated", "refunds/create"])
def test_build_trigger_event_shape(topic):
    event = build_trigger_event(_envelope(topic=topic))
    assert event.channel == "webhook"
    assert event.payload == {"topic": topic, "shop": "demo.myshopify.com", "data": {"id": 1001}}


def test_deliver_duplicate_webhook_id():
    auditor_calls: list = []
    deliverer = WebhookDeliverer(auditor=lambda *args: auditor_calls.append(args))
    first = deliverer.deliver("t1", _binding(_sub(topic="orders/updated")), _envelope(topic="orders/updated"))
    second = deliverer.deliver("t1", _binding(_sub(topic="orders/updated")), _envelope(topic="orders/updated"))
    assert first == {"received": True}
    assert second == {"duplicate": True}
    assert len(auditor_calls) == 1


def test_deliver_ignored_without_subscription():
    deliverer = WebhookDeliverer()
    assert deliverer.deliver("t1", _binding(), _envelope()) == {"ignored": True}


def test_deliver_ignored_for_unsupported_topic():
    deliverer = WebhookDeliverer()
    envelope = WebhookEnvelope(shop_domain="s", topic="products/create", webhook_id="wh-x", data={})
    assert deliverer.deliver("t1", _binding(_sub(topic="products/create")), envelope) == {"ignored": True}


def test_deliver_ignored_for_disabled_subscription():
    deliverer = WebhookDeliverer()
    assert deliverer.deliver("t1", _binding(_sub(enabled=False)), _envelope()) == {"ignored": True}


def test_deliver_skips_unpublished_graph_but_returns_received():
    triggers: list = []
    deliverer = WebhookDeliverer(
        resolver=lambda graph_id, tenant, event: None,
        trigger=lambda *args: triggers.append(args),
    )
    assert deliverer.deliver("t1", _binding(_sub()), _envelope()) == {"received": True}
    assert triggers == []


def test_deliver_triggers_graph_with_resolved_version():
    triggers: list = []
    event_seen: dict = {}

    def resolver(graph_id, tenant, event):
        event_seen["event"] = event
        return 3

    deliverer = WebhookDeliverer(
        resolver=resolver,
        trigger=lambda graph_id, version, event, tenant: triggers.append((graph_id, version, tenant)),
    )
    result = deliverer.deliver("t1", _binding(_sub()), _envelope())
    assert result == {"received": True}
    assert triggers == [("graph-1", 3, "t1")]
    assert event_seen["event"].channel == "webhook"


def test_deliver_resolver_exception_is_swallowed():
    def resolver(graph_id, tenant, event):
        raise RuntimeError("boom")

    deliverer = WebhookDeliverer(resolver=resolver, trigger=lambda *args: pytest.fail("must not trigger"))
    assert deliverer.deliver("t1", _binding(_sub()), _envelope(webhook_id="wh-2")) == {"received": True}


def test_deliver_trigger_exception_is_swallowed():
    def trigger(*args):
        raise RuntimeError("boom")

    deliverer = WebhookDeliverer(resolver=lambda *a: 1, trigger=trigger)
    assert deliverer.deliver("t1", _binding(_sub()), _envelope(webhook_id="wh-3")) == {"received": True}


def test_deliver_rings_isolate_per_tenant():
    deliverer = WebhookDeliverer()
    assert deliverer.deliver("t1", _binding(), _envelope()) == {"ignored": True}
    assert deliverer.deliver("t2", _binding(), _envelope()) == {"ignored": True}


def test_idempotency_ring_expires_entries():
    now = [1000.0]
    deliverer = WebhookDeliverer(clock=lambda: now[0], ring_ttl=3600)
    assert deliverer.deliver("t1", _binding(_sub(topic="orders/updated")),
                             _envelope(topic="orders/updated")) == {"received": True}
    now[0] += 3601
    assert deliverer.deliver("t1", _binding(_sub(topic="orders/updated")),
                             _envelope(topic="orders/updated")) == {"received": True}

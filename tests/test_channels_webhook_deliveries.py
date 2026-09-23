# -*- coding: utf-8 -*-
""" Deliverer 投递存储/死信/重放内核测试（docs/40 §1A/§1B，候选 U370 起）。"""

from __future__ import annotations

from typing import Any

import pytest

from atlas.channels.deliveries import InMemoryDeliveryStore
from atlas.channels.webhooks import WebhookDeliverer, WebhookEnvelope


def _envelope(wh_id: str = "wh-1", topic: str = "orders/create") -> WebhookEnvelope:
    return WebhookEnvelope(
        shop_domain="acme.myshopify.com",
        topic=topic,
        webhook_id=wh_id,
        triggered_at=None,
        data={"id": 9001, "note": "原始订单"},
    )


def _binding(graph_ids: list[str], topic: str = "orders/create") -> dict[str, Any]:
    return {
        "id": "ch-1",
        "webhook_subscriptions": [
            {"topic": topic, "graph_id": gid, "enabled": True} for gid in graph_ids
        ],
    }


def _make_deliverer(
    store: InMemoryDeliveryStore,
    *,
    versions: dict[str, int | None] | None = None,
    fail_resolve: set[str] | None = None,
    fail_trigger: set[str] | None = None,
):
    triggered: list[tuple[str, int]] = []
    audits: list[str] = []

    def resolve(graph_id: str, tenant: str, event: Any) -> int | None:
        if graph_id in (fail_resolve or set()):
            raise RuntimeError("boom-resolve")
        return (versions or {}).get(graph_id)

    def trigger(graph_id: str, version: int, event: Any, tenant: str) -> None:
        if graph_id in (fail_trigger or set()):
            raise RuntimeError("boom-trigger")
        triggered.append((graph_id, version))

    deliverer = WebhookDeliverer(
        resolver=resolve,
        trigger=trigger,
        auditor=lambda action, tenant, meta: audits.append(action),
        store_provider=lambda tenant: store,
    )
    return deliverer, triggered, audits


def test_no_published_version_becomes_dead_with_payload() -> None:
    store = InMemoryDeliveryStore()
    deliverer, triggered, _ = _make_deliverer(store, versions={"graph-1": None})
    result = deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    assert result == {"received": True}
    assert triggered == []
    dead = store.list_dead("t1")
    assert len(dead) == 1
    assert dead[0]["reasons"] == [{"graphId": "graph-1", "code": "NO_PUBLISHED_VERSION"}]
    assert "data" not in dead[0]
    assert store.get_dead("t1", "wh-1")["data"] == {"id": 9001, "note": "原始订单"}


def test_duplicate_delivery_increments_counter_and_skips_audit() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, audits = _make_deliverer(store, versions={"graph-1": 3})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    result = deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    assert result == {"duplicate": True}
    assert audits == ["channel.webhook_received:orders/create"]
    metrics = store.metrics("t1")
    assert metrics["totals"] == {"received": 1, "dead": 0, "duplicates": 1}


def test_ignored_delivery_leaves_no_row() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store)
    result = deliverer.deliver("t1", _binding([]), _envelope())
    assert result == {"ignored": True}
    assert store.list_dead("t1") == []
    assert store.metrics("t1")["totals"] == {"received": 0, "dead": 0, "duplicates": 0}


def test_successful_trigger_records_received_without_payload() -> None:
    store = InMemoryDeliveryStore()
    deliverer, triggered, _ = _make_deliverer(store, versions={"graph-1": 2})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    assert triggered == [("graph-1", 2)]
    assert store.list_dead("t1") == []
    assert store.metrics("t1")["byTopic"]["orders/create"] == {
        "received": 1, "dead": 0, "duplicates": 0,
    }


def test_partial_failure_is_received_not_dead() -> None:
    store = InMemoryDeliveryStore()
    deliverer, triggered, _ = _make_deliverer(
        store, versions={"graph-1": None, "graph-2": 1}
    )
    deliverer.deliver("t1", _binding(["graph-1", "graph-2"]), _envelope())
    assert triggered == [("graph-2", 1)]
    assert store.list_dead("t1") == []
    assert store.metrics("t1")["totals"]["received"] == 1


def test_resolve_and_trigger_failures_record_codes() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(
        store,
        versions={"graph-3": 1},
        fail_resolve={"graph-2"},
        fail_trigger={"graph-3"},
    )
    deliverer.deliver(
        "t1", _binding(["graph-2", "graph-3"]), _envelope()
    )
    reasons = store.list_dead("t1")[0]["reasons"]
    assert reasons == [
        {"graphId": "graph-2", "code": "RESOLVE_FAILED"},
        {"graphId": "graph-3", "code": "TRIGGER_FAILED"},
    ]


def test_missing_graph_id_records_code() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store)
    binding = {
        "id": "ch-1",
        "webhook_subscriptions": [{"topic": "orders/create", "graph_id": "", "enabled": True}],
    }
    deliverer.deliver("t1", binding, _envelope())
    assert store.list_dead("t1")[0]["reasons"] == [
        {"graphId": "", "code": "MISSING_GRAPH_ID"}
    ]


def test_replay_success_clears_payload_and_marks_replayed() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store, versions={"graph-1": None})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())

    deliverer2, triggered, _ = _make_deliverer(store, versions={"graph-1": 5})
    result = deliverer2.replay("t1", _binding(["graph-1"]), "wh-1")
    assert result["status"] == "received"
    assert triggered == [("graph-1", 5)]
    assert store.list_dead("t1") == []
    assert store.get_dead("t1", "wh-1") is None


def test_replay_still_dead_keeps_payload() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store, versions={"graph-1": None})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    result = deliverer.replay("t1", _binding(["graph-1"]), "wh-1")
    assert result["status"] == "dead"
    assert result["reasons"][0]["code"] == "NO_PUBLISHED_VERSION"
    assert store.get_dead("t1", "wh-1")["data"]["id"] == 9001
    assert store.list_dead("t1")[0]["replayedAt"] is not None


def test_replay_without_subscriptions_returns_ignored_unchanged() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store, versions={"graph-1": None})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    result = deliverer.replay("t1", _binding([]), "wh-1")
    assert result["status"] == "ignored"
    assert store.list_dead("t1")[0]["replayedAt"] is None


def test_replay_unknown_or_received_returns_none() -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store, versions={"graph-1": 1})
    assert deliverer.replay("t1", _binding(["graph-1"]), "missing") is None
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope(wh_id="wh-9"))
    assert deliverer.replay("t1", _binding(["graph-1"]), "wh-9") is None


def test_store_failure_falls_back_to_inprocess_ring() -> None:
    class BrokenStore(InMemoryDeliveryStore):
        def note_duplicate_if_seen(self, tenant: str, webhook_id: str) -> bool:
            raise RuntimeError("store down")

    deliverer = WebhookDeliverer(
        resolver=lambda *a: 1,
        trigger=lambda *a: None,
        store_provider=lambda tenant: BrokenStore(),
    )
    assert deliverer.deliver("t1", _binding(["graph-1"]), _envelope()) == {"received": True}
    assert deliverer.deliver("t1", _binding(["graph-1"]), _envelope()) == {"duplicate": True}


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"topic": "orders/create"}, 1),
        ({"topic": "refunds/create"}, 0),
        ({"binding_id": "ch-1"}, 1),
        ({"binding_id": "ch-other"}, 0),
    ],
)
def test_list_dead_filters(
    filters: dict[str, str], expected: int
) -> None:
    store = InMemoryDeliveryStore()
    deliverer, _, _ = _make_deliverer(store, versions={"graph-1": None})
    deliverer.deliver("t1", _binding(["graph-1"]), _envelope())
    assert len(store.list_dead("t1", **filters)) == expected

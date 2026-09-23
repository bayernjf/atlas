"""EventWaitBroker 纯逻辑/并发语义（docs/47 §3；13 U302）。"""

from __future__ import annotations

import threading

import pytest

from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import (
    EventWaitBroker,
    WaitAlreadySignaled,
    WaitTokenNotFound,
)


@pytest.fixture
def broker():
    return EventWaitBroker()


def test_request_and_signal_token_releases_wait(broker):
    token = broker.request(
        event_key="order_paid", node_id="wait-1", graph_id="g1", timeout_seconds=5
    )
    assert token.startswith("wait-")

    def signal():
        broker.signal_token(token, {"paid": True})

    t = threading.Timer(0.05, signal)
    t.start()
    assert broker.wait(token) == {"paid": True, "matchedEventKey": "order_paid"}
    t.join()
    assert broker.list_pending() == []


def test_signal_before_wait_is_retained_until_consumed(broker):
    token = broker.request(
        event_key="order_paid", node_id="wait-1", graph_id="g1", timeout_seconds=5
    )
    broker.signal_token(token, {"n": 1})
    assert broker.wait(token) == {"n": 1, "matchedEventKey": "order_paid"}
    assert not broker.list_pending()


def test_signal_key_broadcasts_to_all_pending(broker):
    t1 = broker.request(event_key="k", node_id="w1", graph_id="g", timeout_seconds=5)
    t2 = broker.request(event_key="k", node_id="w2", graph_id="g", timeout_seconds=5)
    broker.request(event_key="other", node_id="w3", graph_id="g", timeout_seconds=5)
    assert broker.signal_key("k", {"x": 1}) == 2
    assert broker.wait(t1) == {"x": 1, "matchedEventKey": "k"}
    assert broker.wait(t2) == {"x": 1, "matchedEventKey": "k"}
    assert [p["eventKey"] for p in broker.list_pending()] == ["other"]


def test_signal_key_without_pending_returns_zero_and_does_not_retain(broker):
    assert broker.signal_key("nope", {}) == 0
    token = broker.request(event_key="nope", node_id="w", graph_id="g", timeout_seconds=1)
    assert broker.wait(token) is None


def test_timeout_returns_none(broker):
    token = broker.request(event_key="k", node_id="w", graph_id="g", timeout_seconds=1)
    assert broker.wait(token) is None
    assert broker.list_pending() == []


def test_signal_token_unknown_raises_not_found(broker):
    with pytest.raises(WaitTokenNotFound):
        broker.signal_token("wait-dead", {})


def test_duplicate_signal_raises_already_signaled(broker):
    token = broker.request(event_key="k", node_id="w", graph_id="g", timeout_seconds=5)
    broker.signal_token(token, {})
    with pytest.raises(WaitAlreadySignaled):
        broker.signal_token(token, {})


def test_cancel_callback_raises_and_removes(broker):
    token = broker.request(event_key="k", node_id="w", graph_id="g", timeout_seconds=5)
    cancel = threading.Event()

    def raise_cancel():
        t = threading.Timer(0.05, cancel.set)
        t.start()
        broker.wait(token, is_cancelled=cancel.is_set)

    with pytest.raises(RunCancelled):
        raise_cancel()
    assert broker.list_pending() == []


def test_list_pending_shape(broker):
    token = broker.request(
        event_key="evt:x", node_id="wait-x", graph_id="graph-1", timeout_seconds=30
    )
    items = broker.list_pending()
    assert items == [
        {
            "token": token,
            "eventKey": "evt:x",
            "nodeId": "wait-x",
            "graphId": "graph-1",
            "timeoutSeconds": 30,
            "deadlineAt": items[0]["deadlineAt"],
        }
    ]
    assert items[0]["deadlineAt"].endswith("Z")


def test_reset_clears_all(broker):
    broker.request(event_key="k", node_id="w", graph_id="g", timeout_seconds=5)
    broker.reset()
    assert broker.list_pending() == []

def test_restore_reregisters_pending_with_remaining_timeout(broker):
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=2,
    )
    items = broker.list_pending()
    assert len(items) == 1
    assert items[0]["token"] == "wait-deadbeef"
    assert items[0]["eventKey"] == "order_paid"
    assert items[0]["timeoutSeconds"] == 2


def test_restore_signal_key_releases_wait(broker):
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=30,
    )

    released = broker.signal_key("order_paid", {"paid": True})
    assert released == 1
    assert broker.wait("wait-deadbeef") == {"paid": True, "matchedEventKey": "order_paid"}


def test_restore_signal_token_releases_wait(broker):
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=30,
    )
    broker.signal_token("wait-deadbeef", {"n": 1})
    assert broker.wait("wait-deadbeef") == {"n": 1, "matchedEventKey": "order_paid"}


def test_restore_expired_deadline_times_out_immediately(broker):
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=0,
    )
    assert broker.wait("wait-deadbeef") is None


def test_restore_is_idempotent_and_keeps_signaled_state(broker):
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=30,
    )
    broker.signal_token("wait-deadbeef", {"paid": True})
    broker.restore(
        token="wait-deadbeef",
        event_key="order_paid",
        node_id="wait-1",
        graph_id="g1",
        timeout_seconds=30,
    )
    assert broker.wait("wait-deadbeef") == {"paid": True, "matchedEventKey": "order_paid"}


# --- docs/54 多事件 OR 竞速（request_any / eventKeys）---
def test_U520_request_any_signals_on_any_key_and_cleans_up_others(broker):
    token = broker.request_any(
        event_keys=["order_paid", "order_cancelled", "review_needed"],
        node_id="wait-1", graph_id="g1", timeout_seconds=30,
    )
    pending = broker.list_pending()
    assert len(pending) == 1
    assert pending[0]["eventKey"] == "order_paid"  # 首键
    assert pending[0]["eventKeys"] == ["order_paid", "order_cancelled", "review_needed"]

    assert broker.signal_key("order_cancelled", {"reason": "user"}) == 1
    out = broker.wait(token)
    assert out["matchedEventKey"] == "order_cancelled"
    assert out["reason"] == "user"
    # 唤醒取走后余键订阅全部清理
    assert broker.list_pending() == []
    assert broker.signal_key("order_paid", {}) == 0


def test_U521_first_signal_wins_other_keys_do_not_overwrite(broker):
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g", timeout_seconds=30
    )
    assert broker.signal_key("a", {"v": 1}) == 1
    # 首决后另一键再信号：pending 已 signaled，不释放也不覆盖
    assert broker.signal_key("b", {"v": 2}) == 0
    assert broker.wait(token) == {"v": 1, "matchedEventKey": "a"}


def test_U522_restore_multiple_keys_signals_non_primary(broker):
    broker.restore(
        token="wait-multi",
        event_keys=["k1", "k2", "k3"],
        node_id="w", graph_id="g", timeout_seconds=30,
    )
    pending = broker.list_pending()[0]
    assert pending["eventKey"] == "k1"
    assert pending["eventKeys"] == ["k1", "k2", "k3"]
    assert broker.signal_key("k3", {"hit": True}) == 1
    assert broker.wait("wait-multi") == {"hit": True, "matchedEventKey": "k3"}


def test_U523_request_any_dedupes_and_rejects_empty(broker):
    token = broker.request_any(
        event_keys=["dup", "dup", "other"],
        node_id="w", graph_id="g", timeout_seconds=5,
    )
    pending = broker.list_pending()[0]
    assert pending["eventKeys"] == ["dup", "other"]  # 保序去重
    assert broker.wait(token) is None  # 超时清理
    with pytest.raises(ValueError):
        broker.request_any(event_keys=[], node_id="w", graph_id="g", timeout_seconds=1)
    with pytest.raises(ValueError):
        broker.restore(token="wait-x", node_id="w", graph_id="g", timeout_seconds=1)

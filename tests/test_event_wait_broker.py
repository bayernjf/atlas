"""EventWaitBroker 纯逻辑/并发语义（docs/47 §3；13 U302；docs/54 OR 竞速；docs/55 AND/排队）。"""

from __future__ import annotations

import threading

import pytest

from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import (
    QUEUE_PER_KEY,
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
    assert broker.signal_key("k", {"x": 1}) == {"released": 2, "queued": False}
    assert broker.wait(t1) == {"x": 1, "matchedEventKey": "k"}
    assert broker.wait(t2) == {"x": 1, "matchedEventKey": "k"}
    assert [p["eventKey"] for p in broker.list_pending()] == ["other"]


def test_signal_key_without_pending_queues_and_later_request_consumes(broker):
    # docs/55：无消费者时信号进入 per-key ring；随后登记的 any pending 立即消费。
    assert broker.signal_key("nope", {}) == {"released": 0, "queued": True}
    token = broker.request(event_key="nope", node_id="w", graph_id="g", timeout_seconds=1)
    assert broker.wait(token) == {"matchedEventKey": "nope"}


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
    broker.signal_key("early", {})  # 排队项也应清掉
    broker.reset()
    assert broker.list_pending() == []
    token = broker.request(event_key="early", node_id="w", graph_id="g", timeout_seconds=1)
    assert broker.wait(token) is None  # reset 后排队已清空，超时


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

    result = broker.signal_key("order_paid", {"paid": True})
    assert result["released"] == 1 and result["queued"] is False
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

    assert broker.signal_key("order_cancelled", {"reason": "user"}) == {
        "released": 1, "queued": False
    }
    out = broker.wait(token)
    assert out["matchedEventKey"] == "order_cancelled"
    assert out["reason"] == "user"
    # 唤醒取走后余键订阅全部清理
    assert broker.list_pending() == []
    # docs/55：清理后再信号无消费者 → 进入排队（不再静默丢弃）
    assert broker.signal_key("order_paid", {}) == {"released": 0, "queued": True}


def test_U521_first_signal_wins_other_keys_do_not_overwrite(broker):
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g", timeout_seconds=30
    )
    assert broker.signal_key("a", {"v": 1}) == {"released": 1, "queued": False}
    # 首决后另一键再信号：pending 已 signaled，不释放也不覆盖（信号入排队 ring）
    assert broker.signal_key("b", {"v": 2}) == {"released": 0, "queued": True}
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
    assert broker.signal_key("k3", {"hit": True}) == {"released": 1, "queued": False}
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
    with pytest.raises(ValueError):
        broker.request_any(
            event_keys=["a"], node_id="w", graph_id="g", timeout_seconds=1, mode="xor"
        )


# --- docs/55 AND 竞速（eventWaitMode="all"）---
def test_U540_all_mode_releases_only_after_every_key_signaled(broker):
    token = broker.request_any(
        event_keys=["a", "b", "c"], node_id="w", graph_id="g",
        timeout_seconds=30, mode="all",
    )
    # 仅命中部分键：不放行
    assert broker.signal_key("a", {"v": 1}) == {"released": 0, "queued": False}
    assert broker.signal_key("b", {"v": 2}) == {"released": 0, "queued": False}
    assert not broker.list_pending()[0].get("deadlineAt") is None
    out_later = broker.list_pending()[0]
    assert out_later["eventWaitMode"] == "all"
    assert out_later["receivedKeys"] == ["a", "b"]
    # 末键集齐：放行
    assert broker.signal_key("c", {"v": 3}) == {"released": 1, "queued": False}
    out = broker.wait(token)
    assert out["matchedEventKey"] == "c"  # 末集齐键
    assert out["matchedEventKeys"] == ["a", "b", "c"]  # 按登记序
    assert out["matchedPayloads"] == {
        "a": {"v": 1, "matchedEventKey": "a"},
        "b": {"v": 2, "matchedEventKey": "b"},
        "c": {"v": 3, "matchedEventKey": "c"},
    }
    assert broker.list_pending() == []


def test_U541_all_mode_partial_hit_times_out_with_received_keys(broker):
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g",
        timeout_seconds=1, mode="all",
    )
    broker.signal_key("a", {})
    assert broker.wait(token) is None  # 超时
    assert broker.take_last_received(token) == ["a"]
    assert broker.take_last_received(token) == []  # 取后即删


def test_U542_all_mode_duplicate_same_key_does_not_count_twice(broker):
    broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g",
        timeout_seconds=30, mode="all",
    )
    broker.signal_key("a", {"n": 1})
    # 同键重复信号：不覆盖首条、不计数第二次，无新消费者 → 排队
    assert broker.signal_key("a", {"n": 2}) == {"released": 0, "queued": True}
    pending = broker.list_pending()[0]
    assert pending["receivedKeys"] == ["a"]  # 仍只算一次


def test_U543_all_mode_signal_token_releases_immediately(broker):
    # 人工直投不区分 mode，立即放行
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g",
        timeout_seconds=30, mode="all",
    )
    broker.signal_token(token, {"manual": True})
    out = broker.wait(token)
    assert out["manual"] is True
    assert out["matchedEventKey"] == "a"  # 补首键


def test_U544_queued_signals_drain_into_later_any_request(broker):
    # 先到两个不同键的信号（无消费者，排队）
    broker.signal_key("a", {"x": 1})
    broker.signal_key("b", {"y": 2})
    # any 登记：按登记序取最早有队列的键（a）即命中
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g", timeout_seconds=30
    )
    out = broker.wait(token)
    assert out["matchedEventKey"] == "a" and out["x"] == 1


def test_U545_queued_signals_drain_into_all_request_per_key(broker):
    broker.signal_key("a", {"x": 1})
    broker.signal_key("b", {"y": 2})
    token = broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g",
        timeout_seconds=30, mode="all",
    )
    # 登记时逐键消费排队，立即集齐放行
    out = broker.wait(token)
    assert out["matchedEventKeys"] == ["a", "b"]
    assert out["matchedPayloads"]["a"]["x"] == 1
    assert out["matchedPayloads"]["b"]["y"] == 2


def test_U546_queue_ring_caps_per_key_evicting_oldest(broker):
    for i in range(QUEUE_PER_KEY + 2):
        broker.signal_key("k", {"i": i})
    token = broker.request(event_key="k", node_id="w", graph_id="g", timeout_seconds=1)
    # ring 容量 4，最旧两条被淘汰；登记消费到的是第 3 条（i=2）
    out = broker.wait(token)
    assert out["i"] == 2


def test_U547_restore_all_mode_requires_recollect(broker):
    # docs/55：restore 透传 mode=all，但已命中集合不跨重启，须重新集齐
    broker.restore(
        token="wait-all", event_keys=["a", "b"], node_id="w", graph_id="g",
        timeout_seconds=30, mode="all",
    )
    pending = broker.list_pending()[0]
    assert pending["eventWaitMode"] == "all"
    assert pending["receivedKeys"] == []
    broker.signal_key("a", {})
    broker.signal_key("b", {})
    out = broker.wait("wait-all")
    assert out["matchedEventKeys"] == ["a", "b"]


def test_U548_any_mode_list_pending_has_no_mode_field(broker):
    broker.request_any(
        event_keys=["a", "b"], node_id="w", graph_id="g", timeout_seconds=30
    )
    row = broker.list_pending()[0]
    assert "eventWaitMode" not in row  # any 为默认，不落到投影

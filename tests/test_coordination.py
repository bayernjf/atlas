"""M7 批 1：任务信封状态机 + TaskStore 幂等去重（08 M7 立项条，U47/U48 部分）。"""

from __future__ import annotations

import pytest

from atlas.coordination import Envelope, TaskStore, TERMINAL_STATES, can_transition


def _dispatch(store: TaskStore, idempotency_key: str = "refund-1|verify|v1"):
    return store.dispatch(
        run_id="run-1",
        idempotency_key=idempotency_key,
        trace_id="run-1",
        graph_version="refund-flow@1",
        type="refund.verify_order",
        assignee="bot.customer",
        payload={"orderId": "12345", "amount": 128},
        deadline_ms=30000,
    )


def test_envelope_state_machine_legal_and_illegal_transitions():
    assert can_transition("pending", "accepted") is True
    assert can_transition("accepted", "running") is True
    assert can_transition("running", "done") is True
    assert can_transition("running", "failed") is True
    assert can_transition("running", "timeout") is True
    # 非法迁移：跳步 / 终态出边 / 回退
    assert can_transition("pending", "running") is False
    assert can_transition("done", "running") is False
    assert can_transition("running", "accepted") is False

    envelope = Envelope(
        taskId="t", runId="r", idempotencyKey="k", traceId="r",
        graphVersion="g@1", type="x", assignee="bot", deadlineMs=1,
    )
    with pytest.raises(ValueError, match="非法任务状态迁移"):
        envelope.transition("running")


def test_dispatch_lifecycle_to_done_and_idempotent_replay():
    store = TaskStore()
    envelope, created = _dispatch(store)
    assert created is True
    assert envelope.state == "pending"

    # 幂等重放：同 idempotencyKey 返首结果、不新建任务（19 §2.4 L1）
    replay, replay_created = _dispatch(store)
    assert replay_created is False
    assert replay.taskId == envelope.taskId
    assert len(store.list()) == 1

    # 完整生命周期 pending→accepted→running→done
    assert store.accept(envelope.taskId).state == "accepted"
    assert store.start(envelope.taskId).state == "running"
    done = store.complete(envelope.taskId, {"ok": True})
    assert done.state == "done"
    assert done.result == {"ok": True}
    assert done.state in TERMINAL_STATES

    # 终态不可再迁移
    with pytest.raises(ValueError):
        store.complete(envelope.taskId, {})


def test_fail_and_timeout_terminal_paths():
    store = TaskStore()
    e1, _ = _dispatch(store, "k1")
    store.accept(e1.taskId)
    store.start(e1.taskId)
    assert store.fail(e1.taskId, "boom").state == "failed"
    assert store.get(e1.taskId).result == {"error": "boom"}

    e2, _ = _dispatch(store, "k2")
    store.accept(e2.taskId)
    store.start(e2.taskId)
    assert store.timeout(e2.taskId).state == "timeout"


def test_list_filter_get_and_reset():
    store = TaskStore()
    e1, _ = _dispatch(store, "k1")
    e2, _ = _dispatch(store, "k2")
    store.accept(e1.taskId)
    store.start(e1.taskId)
    store.complete(e1.taskId, {})

    assert [e.taskId for e in store.list()] == [e2.taskId, e1.taskId]  # 新→旧
    assert [e.taskId for e in store.list("done")] == [e1.taskId]
    assert store.get("nope") is None
    assert store.get(e2.taskId).state == "pending"

    store.reset()
    assert store.list() == []
    # reset 后同幂等键可重新投递
    again, created = _dispatch(store, "k1")
    assert created is True

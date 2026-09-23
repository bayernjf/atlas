"""wait 节点事件等待图级语义（docs/47 §3；13 U302）。"""

from __future__ import annotations

import threading

import pytest

from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import WaitNodeFailure, run_graph


def _event_wait_graph(**config_overrides):
    config = {
        "waitType": "event",
        "eventKey": "order_paid",
        "timeoutSeconds": 30,
        "onTimeout": "continue",
    }
    config.update(config_overrides)
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "position": {"x": 2, "y": 0}, "config": config},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )


def _collect_wait_token():
    events: list[dict] = []

    def emit(event: dict) -> None:
        events.append(event)

    return events, emit


def test_signal_releases_wait_with_payload():
    broker = EventWaitBroker()
    events, emit = _collect_wait_token()

    def signal_on_register(event: dict) -> None:
        emit(event)
        if event.get("type") == "node_start" and event.get("wait"):
            token = event["wait"]["token"]
            threading.Timer(
                0.05, lambda: broker.signal_token(token, {"paidAt": "2026-09-23"})
            ).start()

    result = run_graph(
        _event_wait_graph(),
        event_wait_broker=broker,
        emit=signal_on_register,
    )
    assert result["status"] == "completed"
    output = result["outputs"]["wait-1"]
    assert output["waitType"] == "event"
    assert output["signaled"] is True
    assert output["resolvedBy"] == "signal"
    assert output["payload"] == {"paidAt": "2026-09-23"}
    assert output["token"].startswith("wait-")
    assert "tool-after" in result["outputs"]
    assert broker.list_pending() == []


def test_signal_key_broadcast_releases_wait():
    broker = EventWaitBroker()

    def signal_on_register(event: dict) -> None:
        if event.get("type") == "node_start" and event.get("wait"):
            threading.Timer(
                0.05, lambda: broker.signal_key("order_paid", {"x": 1})
            ).start()

    result = run_graph(_event_wait_graph(), event_wait_broker=broker, emit=signal_on_register)
    assert result["outputs"]["wait-1"]["payload"] == {"x": 1}


def test_waitevents_preset_resolves_immediately():
    result = run_graph(
        _event_wait_graph(),
        inputs={"waitEvents": {"wait-1": {"preset": True}}},
        event_wait_broker=EventWaitBroker(),
    )
    output = result["outputs"]["wait-1"]
    assert output["resolvedBy"] == "input"
    assert output["signaled"] is True
    assert output["payload"] == {"preset": True}
    assert output["waitedSeconds"] == 0


def test_waitevents_preset_non_object_payload_normalizes():
    result = run_graph(
        _event_wait_graph(),
        inputs={"waitEvents": {"wait-1": "done"}},
        event_wait_broker=EventWaitBroker(),
    )
    assert result["outputs"]["wait-1"]["payload"] == {}


def test_timeout_continue_keeps_going():
    result = run_graph(
        _event_wait_graph(timeoutSeconds=1, onTimeout="continue"),
        event_wait_broker=EventWaitBroker(),
    )
    output = result["outputs"]["wait-1"]
    assert output["resolvedBy"] == "timeout"
    assert output["signaled"] is False
    assert output["payload"] == {}
    assert "tool-after" in result["outputs"]


def test_timeout_fail_marks_run_failed():
    with pytest.raises(WaitNodeFailure) as excinfo:
        run_graph(
            _event_wait_graph(timeoutSeconds=1, onTimeout="fail"),
            event_wait_broker=EventWaitBroker(),
        )
    assert excinfo.value.code == "WAIT_TIMEOUT_FAILED"
    assert excinfo.value.node_id == "wait-1"


def test_invalid_rendered_event_key_fails_without_registering():
    broker = EventWaitBroker()
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "position": {"x": 2, "y": 0},
                 "config": {
                     "waitType": "event",
                     "eventKey": "k_{{trigger-1.context.payload.bad}}",
                     "timeoutSeconds": 30,
                     "onTimeout": "continue",
                 }},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )
    with pytest.raises(WaitNodeFailure) as excinfo:
        run_graph(
            graph,
            inputs={"bad": "has space"},
            event_wait_broker=broker,
        )
    assert excinfo.value.code == "WAIT_EVENT_KEY_INVALID"
    assert broker.list_pending() == []


def test_cancel_while_waiting_raises_runcancelled():
    broker = EventWaitBroker()
    cancel = threading.Event()

    def arm_cancel(event: dict) -> None:
        if event.get("type") == "node_start" and event.get("wait"):
            threading.Timer(0.05, cancel.set).start()

    with pytest.raises(RunCancelled):
        run_graph(
            _event_wait_graph(),
            event_wait_broker=broker,
            emit=arm_cancel,
            is_cancelled=cancel.is_set,
        )
    assert broker.list_pending() == []

# --- U401: event wait 中断帧与跨重启续跑（docs/53 §5）---

import time as _time

from atlas.storage.frame import build_frame, deadline_iso, remaining_seconds


def _wait_for_event_frame(holder: list, timeout: float = 2.0) -> dict:
    deadline = _time.monotonic() + timeout
    while not holder and _time.monotonic() < deadline:
        _time.sleep(0.005)
    assert holder, "应在超时前产生 event wait 中断帧"
    return holder[0]


def _event_frame(graph, token, *, on_timeout="continue", event_key="order_paid"):
    return build_frame(
        token=token,
        run_id="",
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(0),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {}, "outputs": {}},
        wait={
            "waitType": "event",
            "eventKey": event_key,
            "onTimeout": on_timeout,
            "timeoutSeconds": 30,
        },
    )


def test_event_wait_frame_sink_captures_wait_frame():
    broker = EventWaitBroker()
    frames: list[dict] = []

    def run_original() -> None:
        run_graph(_event_wait_graph(), event_wait_broker=broker, frame_sink=frames.append)

    worker = threading.Thread(target=run_original)
    worker.start()
    frame = _wait_for_event_frame(frames)
    try:
        assert frame["kind"] == "wait"
        assert frame["resume_token"].startswith("wait-")
        assert frame["deadline_at"]
        assert frame["graph_snapshot"]["nodes"]
        wait = frame["wait"]
        assert wait["waitType"] == "event"
        assert wait["eventKey"] == "order_paid"
        assert wait["onTimeout"] == "continue"
        assert wait["timeoutSeconds"] == 30
        pending_tokens = [item["token"] for item in broker.list_pending()]
        assert frame["resume_token"] in pending_tokens
    finally:
        broker.signal_token(frame["resume_token"], {"paidAt": "2026-09-23"})
        worker.join(timeout=2)


def test_resume_event_wait_uses_same_token_and_signal_releases():
    broker_a = EventWaitBroker()
    frames: list[dict] = []

    def run_original() -> None:
        run_graph(_event_wait_graph(), event_wait_broker=broker_a, frame_sink=frames.append)

    first = threading.Thread(target=run_original)
    first.start()
    frame = _wait_for_event_frame(frames)
    token = frame["resume_token"]

    # 重启：新空 broker，restore 同 token/event_key/剩余超时。
    broker_b = EventWaitBroker()
    broker_b.restore(
        token=token,
        event_key=frame["wait"]["eventKey"],
        node_id=frame["node_id"],
        graph_id="adhoc",
        timeout_seconds=remaining_seconds(frame["deadline_at"]),
    )
    resumed: dict = {}

    def run_resume() -> None:
        resumed["result"] = run_graph(
            _event_wait_graph(), event_wait_broker=broker_b, resume=frame
        )

    second = threading.Thread(target=run_resume)
    second.start()
    _time.sleep(0.05)
    broker_b.signal_token(token, {"paidAt": "2026-09-23"})
    second.join(timeout=2)

    result = resumed["result"]
    assert result["status"] == "completed"
    output = result["outputs"]["wait-1"]
    assert output["signaled"] is True
    assert output["resolvedBy"] == "signal"
    assert output["payload"] == {"paidAt": "2026-09-23"}
    assert "tool-after" in result["outputs"]
    assert broker_b.list_pending() == []

    # 清理第一进程挂起线程。
    broker_a.signal_token(token, {"paidAt": "2026-09-23"})
    first.join(timeout=2)


def test_resume_event_wait_missing_wait_field_raises_frame_invalid():
    graph = _event_wait_graph()
    frame = build_frame(
        token="wait-missing",
        run_id="",
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(30),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {}, "outputs": {}},
    )
    with pytest.raises(WaitNodeFailure) as excinfo:
        run_graph(graph, event_wait_broker=EventWaitBroker(), resume=frame)
    assert excinfo.value.code == "WAIT_EVENT_FRAME_INVALID"


def test_resume_event_wait_deadline_passed_continue_resolves_timeout():
    graph = _event_wait_graph(onTimeout="continue")
    frame = _event_frame(graph, "wait-timeout", on_timeout="continue")
    broker = EventWaitBroker()
    broker.restore(
        token="wait-timeout", event_key="order_paid", node_id="wait-1",
        graph_id="adhoc", timeout_seconds=0,
    )
    result = run_graph(graph, event_wait_broker=broker, resume=frame)
    output = result["outputs"]["wait-1"]
    assert result["status"] == "completed"
    assert output["resolvedBy"] == "timeout"
    assert output["signaled"] is False
    assert "tool-after" in result["outputs"]


def test_resume_event_wait_deadline_passed_fail_raises():
    graph = _event_wait_graph(onTimeout="fail")
    frame = _event_frame(graph, "wait-fail", on_timeout="fail")
    broker = EventWaitBroker()
    broker.restore(
        token="wait-fail", event_key="order_paid", node_id="wait-1",
        graph_id="adhoc", timeout_seconds=0,
    )
    with pytest.raises(WaitNodeFailure) as excinfo:
        run_graph(graph, event_wait_broker=broker, resume=frame)
    assert excinfo.value.code == "WAIT_TIMEOUT_FAILED"

def test_event_wait_timeout_expression_used_for_broker():
    broker = EventWaitBroker()
    requested: list[int] = []
    orig_request = broker.request

    def spy_request(*, event_key, node_id, graph_id, timeout_seconds):
        requested.append(timeout_seconds)
        return orig_request(
            event_key=event_key,
            node_id=node_id,
            graph_id=graph_id,
            timeout_seconds=timeout_seconds,
        )

    broker.request = spy_request

    def signal_on_register(event: dict) -> None:
        if event.get("type") == "node_start" and event.get("wait"):
            token = event["wait"]["token"]
            threading.Timer(
                0.03, lambda: broker.signal_token(token, {"ok": True})
            ).start()

    graph = _event_wait_graph(
        timeoutMode="expression",
        timeoutSeconds=3600,
        timeoutExpression="{{global.waitSecs}} * 2 + 1",
    )
    result = run_graph(
        graph,
        event_wait_broker=broker,
        inputs={"waitSecs": 2},
        emit=signal_on_register,
    )
    assert result["status"] == "completed"
    assert requested == [5]
    output = result["outputs"]["wait-1"]
    assert output["signaled"] is True
    assert output["payload"] == {"ok": True}
    assert broker.list_pending() == []


def test_event_wait_timeout_expression_out_of_range_fails():
    broker = EventWaitBroker()
    graph = _event_wait_graph(
        timeoutMode="expression",
        timeoutSeconds=3600,
        timeoutExpression="{{global.waitSecs}}",
    )
    with pytest.raises(WaitNodeFailure) as exc:
        run_graph(
            graph, event_wait_broker=broker, inputs={"waitSecs": 9999}
        )
    assert exc.value.code == "WAIT_DURATION_INVALID"
    assert broker.list_pending() == []


def test_event_wait_static_default_uses_timeout_seconds():
    broker = EventWaitBroker()
    requested: list[int] = []
    orig_request = broker.request

    def spy_request(*, event_key, node_id, graph_id, timeout_seconds):
        requested.append(timeout_seconds)
        return orig_request(
            event_key=event_key,
            node_id=node_id,
            graph_id=graph_id,
            timeout_seconds=timeout_seconds,
        )

    broker.request = spy_request

    def signal_on_register(event: dict) -> None:
        if event.get("type") == "node_start" and event.get("wait"):
            token = event["wait"]["token"]
            threading.Timer(
                0.03, lambda: broker.signal_token(token, {})
            ).start()

    result = run_graph(
        _event_wait_graph(), event_wait_broker=broker, emit=signal_on_register
    )
    assert result["status"] == "completed"
    assert requested == [30]


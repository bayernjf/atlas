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

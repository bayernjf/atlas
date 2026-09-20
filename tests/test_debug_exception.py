"""docs/28 批 2⑥：异常断点（U157–U161，契约 docs/28 §3.2，04 §5.12）。

节点逻辑抛异常时，若该节点配置 onException 则先暂停（reason=exception、帧带 error）；
resume(step/continue) 后原样重抛（v1 不提供忽略继续），resume(stop) 走 DebugStopped。
未配置时行为与现状逐字节一致：异常照常冒泡，不产生 exception 暂停帧。
"""

from __future__ import annotations

import threading
import time

import pytest

from atlas.debug.controller import DebugController
from atlas.debug.sessions import DebugStopped, DebuggerBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph

TIMEOUT = 5


def _error_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "webhook", "webhookUrl": "/h"}},
                {"id": "ai-1", "type": "ai_decision", "name": "决策",
                 "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}}"}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "ai-1"}],
        }
    )


class _ErrorRun:
    def __init__(self, *, breakpoints=None, inputs=None):
        self.events: list[dict] = []
        self.errors: list[Exception] = []
        broker = DebuggerBroker()
        self.session = broker.create(graph_id="g-exc", breakpoints=breakpoints)
        controller = DebugController(self.session, self.events.append)
        graph = _error_graph()

        def work():
            try:
                run_graph(
                    graph,
                    inputs=inputs,
                    graph_id="g-exc",
                    emit=self.events.append,
                    debug_controller=controller,
                )
            except BaseException as exc:  # 记录 DebugStopped 与业务异常两类
                self.errors.append(exc)

        self.thread = threading.Thread(target=work, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def wait_paused(self, node_id):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            for event in reversed(self.events):
                if event.get("type") == "paused" and event.get("node_id") == node_id:
                    return event
            if self.errors:
                raise AssertionError(f"run stopped before pause at {node_id}: {self.errors[0]!r}")
            if not self.thread.is_alive():
                raise AssertionError(f"run ended without pause at {node_id}: {self.events}")
            time.sleep(0.01)
        raise AssertionError(f"timeout waiting paused at {node_id}: {self.events}")

    def resume(self, frame, action):
        assert self.session.resolve(frame["token"], action)

    def join(self):
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive(), "debug run thread hung"

    @property
    def paused_nodes(self):
        return [e["node_id"] for e in self.events if e.get("type") == "paused"]


def test_u157_on_exception_without_spec_returns_immediately():
    broker = DebuggerBroker()
    session = broker.create(graph_id="g", breakpoints=None)
    emitted: list[dict] = []
    controller = DebugController(session, emitted.append)

    class _Node:
        id = "tool-x"
        type = "tool_call"

    state = {"variables": {"global": {}}, "outputs": {}}
    # 无断点配置：必须立即返回、不发帧、不阻塞。
    assert controller.on_exception(_Node(), ValueError("boom"), state) is None
    assert emitted == []


def test_u158_exception_breakpoint_pauses_then_reraises_on_continue():
    run = _ErrorRun(
        breakpoints=[{"node_id": "ai-1", "onException": True}],
        inputs={"amount": "abc", "reason": "破损"},
    ).start()

    run.resume(run.wait_paused("trigger-1"), "continue")

    frame = run.wait_paused("ai-1")
    assert frame["reason"] == "exception"
    assert frame["error"]["type"] == "ValueError"
    assert "abc" in frame["error"]["message"]
    # 异常暂停同样沉淀变量历史。
    assert "history" in frame
    assert any(h["reason"] == "exception" for h in frame["history"])

    run.resume(frame, "continue")
    run.join()

    # resume(continue) 后原样重抛业务异常（不吞、不忽略）。
    assert len(run.errors) == 1
    assert isinstance(run.errors[0], ValueError)
    assert not any(e.get("type") == "run_end" for e in run.events)


def test_u159_exception_breakpoint_stop_raises_debug_stopped():
    run = _ErrorRun(
        breakpoints=[{"node_id": "ai-1", "onException": True}],
        inputs={"amount": "abc", "reason": "破损"},
    ).start()

    run.resume(run.wait_paused("trigger-1"), "continue")
    frame = run.wait_paused("ai-1")
    run.resume(frame, "stop")
    run.join()

    # stop 折叠为 DebugStopped（stopped SSE 帧由 API 层在捕获后下发，库直跑不产生），
    # 不重抛业务异常。
    assert len(run.errors) == 1
    assert isinstance(run.errors[0], DebugStopped)
    assert run.errors[0].node_id == "ai-1"


def test_u160_without_exception_breakpoint_error_propagates_as_before():
    run = _ErrorRun(
        breakpoints=None,  # 完全无异常断点：continue 后 ai-1 直接抛错
        inputs={"amount": "abc", "reason": "破损"},
    ).start()
    run.resume(run.wait_paused("trigger-1"), "continue")
    run.join()

    # 未配 onException：ai-1 不产生 exception 暂停，ValueError 照常冒泡。
    assert run.paused_nodes == ["trigger-1"]
    assert len(run.errors) == 1
    assert isinstance(run.errors[0], ValueError)


def test_u161_validate_debug_rejects_non_boolean_on_exception():
    from atlas.api import main as api_main

    graph = _error_graph()
    normalized = api_main._validate_debug(
        graph, {"breakpoints": [{"node_id": "ai-1", "onException": True}]}
    )
    assert normalized[0]["onException"] is True

    defaulted = api_main._validate_debug(graph, {"breakpoints": [{"node_id": "ai-1"}]})
    assert defaulted[0]["onException"] is False

    with pytest.raises(api_main.HTTPException) as exc:
        api_main._validate_debug(
            graph, {"breakpoints": [{"node_id": "ai-1", "onException": "yes"}]}
        )
    assert exc.value.status_code == 422
    assert "onException" in exc.value.detail

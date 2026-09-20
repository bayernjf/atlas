"""单步调试与断点 v1：会话/暂停状态机（U29，契约 04 §5.12，06 §6.10）。"""

from __future__ import annotations

import threading
import time

from atlas.debug.controller import DebugController
from atlas.debug.sessions import DebugStopped, DebuggerBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph

TIMEOUT = 5


def _linear_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "tool-1", "type": "tool_call", "name": "工具",
                 "config": {"tool": "op-a"}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
    )


class _DebugRun:
    """驱动一次调试运行：后台线程跑 run_graph，主线程按 paused 帧 resume。"""

    def __init__(self, graph, *, breakpoints=None, inputs=None, graph_resolver=None):
        self.graph = graph
        self.events: list[dict] = []
        self.errors: list[Exception] = []
        self.broker = DebuggerBroker()
        self.session = self.broker.create(graph_id="graph-debug", breakpoints=breakpoints)
        controller = DebugController(self.session, self.events.append)

        def work():
            try:
                run_graph(
                    graph,
                    inputs=inputs,
                    graph_id="graph-debug",
                    graph_resolver=graph_resolver,
                    emit=self.events.append,
                    debug_controller=controller,
                )
            except DebugStopped as exc:
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
                raise AssertionError(f"run stopped: {self.errors[0]!r}")
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


def test_step_mode_pauses_before_every_node_with_snapshots():
    run = _DebugRun(_linear_graph(), inputs={"amount": 1500}).start()

    first = run.wait_paused("trigger-1")
    assert first["reason"] == "step" and first["node_type"] == "trigger"
    assert first["token"].startswith("dbg-")
    assert first["outputs"] == {}
    start_idx = run.events.index(first) - 1
    assert run.events[start_idx]["type"] == "node_start"
    assert not any(e.get("type") == "node_end" and e["node_id"] == "trigger-1"
                   for e in run.events[: start_idx + 1])
    run.resume(first, "step")

    second = run.wait_paused("tool-1")
    assert second["reason"] == "step" and second["node_type"] == "tool_call"
    # 快照含已完成的 trigger 终态产出
    assert "trigger-1" in second["outputs"]
    run.resume(second, "step")
    run.join()

    assert run.paused_nodes == ["trigger-1", "tool-1"]
    assert any(e.get("type") == "run_end" for e in run.events)
    assert run.errors == []


def test_continue_runs_to_completion_without_further_pause():
    run = _DebugRun(_linear_graph()).start()
    first = run.wait_paused("trigger-1")
    run.resume(first, "continue")
    run.join()
    assert run.paused_nodes == ["trigger-1"]
    assert run.session.step_mode is False


def test_unconditional_breakpoint_pauses_after_continue():
    run = _DebugRun(_linear_graph(), breakpoints=[{"node_id": "tool-1"}]).start()
    first = run.wait_paused("trigger-1")
    run.resume(first, "continue")

    hit = run.wait_paused("tool-1")
    assert hit["reason"] == "breakpoint"
    run.resume(hit, "continue")
    run.join()
    assert run.paused_nodes == ["trigger-1", "tool-1"]


def test_conditional_breakpoint_true_pauses_false_runs_through():
    expression = "{{trigger-1.context.payload.amount}} > 1000"

    run = _DebugRun(
        _linear_graph(),
        breakpoints=[{"node_id": "tool-1", "expression": expression}],
        inputs={"amount": 1500},
    ).start()
    run.resume(run.wait_paused("trigger-1"), "continue")
    hit = run.wait_paused("tool-1")
    assert hit["reason"] == "condition"
    run.resume(hit, "continue")
    run.join()

    run2 = _DebugRun(
        _linear_graph(),
        breakpoints=[{"node_id": "tool-1", "expression": expression}],
        inputs={"amount": 1},
    ).start()
    run2.resume(run2.wait_paused("trigger-1"), "continue")
    run2.join()
    assert run2.paused_nodes == ["trigger-1"]
    assert run2.errors == []


def test_condition_expression_error_fails_safe_without_pausing():
    run = _DebugRun(
        _linear_graph(),
        breakpoints=[{"node_id": "tool-1",
                      "expression": "{{trigger-1.context.payload.missing.deep}} > 1"}],
    ).start()
    run.resume(run.wait_paused("trigger-1"), "continue")
    run.join()
    assert run.paused_nodes == ["trigger-1"]
    assert run.session.last_condition_error  # 异常被记录而非抛出


def test_stop_resume_raises_debug_stopped_and_emits_no_run_end():
    run = _DebugRun(_linear_graph()).start()
    frame = run.wait_paused("trigger-1")
    run.resume(frame, "stop")
    run.join()
    assert len(run.errors) == 1
    assert isinstance(run.errors[0], DebugStopped)
    assert run.errors[0].node_id == "trigger-1"
    assert not any(e.get("type") == "run_end" for e in run.events)


def test_duplicate_resolve_is_rejected_first_wins():
    run = _DebugRun(_linear_graph()).start()
    frame = run.wait_paused("trigger-1")
    assert run.session.resolve(frame["token"], "continue") is True
    assert run.session.resolve(frame["token"], "stop") is False
    run.join()
    # 首决 continue 生效：运行正常走完
    assert run.errors == []
    assert run.session.step_mode is False


def test_reset_releases_paused_thread_as_stop():
    broker = DebuggerBroker()
    events: list[dict] = []
    session = broker.create(graph_id="g", breakpoints=None)
    controller = DebugController(session, events.append)
    errors: list[Exception] = []

    def work():
        try:
            run_graph(_linear_graph(), debug_controller=controller)
        except DebugStopped as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while not any(e.get("type") == "paused" for e in events):
        time.sleep(0.01)
        assert time.monotonic() < deadline
    broker.reset()
    thread.join(TIMEOUT)
    assert not thread.is_alive()
    assert isinstance(errors[0], DebugStopped)


def test_parallel_branch_pauses_never_overlap():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "parallel-1", "type": "parallel", "name": "p",
                 "config": {"joinStrategy": "all_success",
                            "branches": [{"label": "A", "target": "tool-a"},
                                         {"label": "B", "target": "tool-b"}],
                            "joinTarget": "tool-join"}},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": "op-a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": "op-b"}},
                {"id": "tool-join", "type": "tool_call", "name": "join",
                 "config": {"tool": "op-join"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "parallel-1"},
                {"id": "e2", "source": "parallel-1", "target": "tool-a"},
                {"id": "e3", "source": "parallel-1", "target": "tool-b"},
                {"id": "e4", "source": "tool-a", "target": "tool-join"},
                {"id": "e5", "source": "tool-b", "target": "tool-join"},
            ],
        }
    )
    run = _DebugRun(graph).start()
    seen: list[str] = []
    # 每个暂停被恢复后才可能出现下一个——同会话同时刻只暴露一个活动暂停。
    for _ in range(5):
        frame = None
        deadline = time.monotonic() + TIMEOUT
        pending_ids = {f["node_id"] for f in run.broker.list_pending()}
        assert len(pending_ids) <= 1
        while frame is None:
            for event in reversed(run.events):
                if event.get("type") == "paused" and event["node_id"] not in seen:
                    frame = event
                    break
            else:
                if not run.thread.is_alive():
                    frame = None
                    break
                time.sleep(0.01)
                assert time.monotonic() < deadline
                continue
            break
        if frame is None:
            break
        seen.append(frame["node_id"])
        assert len(run.broker.list_pending()) == 1
        run.resume(frame, "step")
    run.join()
    assert run.paused_nodes[:2] == ["trigger-1", "parallel-1"]
    assert set(run.paused_nodes[2:4]) == {"tool-a", "tool-b"}
    assert run.paused_nodes[4] == "tool-join"
    assert len(run.paused_nodes) == 5
    assert not any(n.startswith("__join__") for n in run.paused_nodes)


def test_paused_frame_snapshots_are_deep_copied():
    session = DebuggerBroker().create(graph_id="g", breakpoints=None)
    original_globals = {"amount": 100}
    original_outputs = {"n": {"value": [1, 2]}}
    token = session.request_pause(
        node_id="n-1", node_type="tool_call", reason="step",
        globals=original_globals, outputs=original_outputs,
    )
    original_globals["amount"] = 999
    original_outputs["n"]["value"].append(3)
    frame = session.frame(token)
    assert frame["globals"] == {"amount": 100}
    assert frame["outputs"] == {"n": {"value": [1, 2]}}
    session.end_pause(token)


def test_subgraph_interior_is_paused_with_path():
    # docs/28 §3.3（契约反转，旧名为 test_subgraph_interior_is_not_paused）：
    # 子图重入透传同一调试会话，step 在子层节点同样暂停，paused 帧附 subgraphPath。
    child = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "child-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "child-tool", "type": "tool_call", "name": "cw",
                 "config": {"tool": "op-child"}},
            ],
            "edges": [{"id": "ce1", "source": "child-trigger", "target": "child-tool"}],
        }
    )
    parent = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "s",
                 "config": {"graphId": "g-child", "inputs": {}}},
                {"id": "tool-after", "type": "tool_call", "name": "after",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )
    run = _DebugRun(parent, graph_resolver={"g-child": child}.get).start()
    order = ["trigger-1", "subgraph-1", "child-trigger", "child-tool", "tool-after"]
    frames = {}
    for node_id in order:
        frame = run.wait_paused(node_id)
        frames[node_id] = frame
        run.resume(frame, "step")
    run.join()
    assert run.paused_nodes == order
    assert frames["child-trigger"]["subgraphPath"] == ["subgraph-1"]
    assert frames["child-tool"]["subgraphPath"] == ["subgraph-1"]
    assert "subgraphPath" not in frames["tool-after"]
    assert run.errors == []

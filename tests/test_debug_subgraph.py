"""docs/28 批 2⑦：子图内部断点与逐帧上屏（U162–U166，契约 docs/28 §3.3，04 §5.12/§5.7）。

子图重入透传同一 DebugController/DebugSession：子层 paused/debug_log 经命名空间 emit
附 subgraphPath 上屏，终帧仍吞（整图终帧唯一）；子层 stop 的 DebugStopped 穿透
fail-safe，不被折叠成 subgraph 节点 failed；重入返回后 controller emit 栈恢复。
"""

from __future__ import annotations

import threading
import time

from atlas.debug.controller import DebugController
from atlas.debug.sessions import DebugStopped, DebuggerBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph

TIMEOUT = 5


def _tool(node_id, name, tool="op-after", params=None):
    config = {"tool": tool}
    if params is not None:
        config["params"] = params
    return {"id": node_id, "type": "tool_call", "name": name, "config": config}


def _simple_child():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                _tool("c-tool", "子工具", params="done"),
            ],
            "edges": [{"id": "ce1", "source": "c-trigger", "target": "c-tool"}],
        }
    )


def _parent_with_subgraph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": "g-child", "inputs": {}}},
                _tool("tool-after", "后继", params="done"),
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )


class _SubDebugRun:
    def __init__(self, *, breakpoints=None):
        self.events: list[dict] = []
        self.errors: list[Exception] = []
        broker = DebuggerBroker()
        self.session = broker.create(graph_id="g-parent", breakpoints=breakpoints)
        self.controller = DebugController(self.session, self.events.append)
        parent = _parent_with_subgraph()
        self.resolver = {"g-child": _simple_child()}.get

        def work():
            try:
                run_graph(
                    parent,
                    graph_id="g-parent",
                    graph_resolver=self.resolver,
                    emit=self.events.append,
                    debug_controller=self.controller,
                )
            except BaseException as exc:
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
                raise AssertionError(f"stopped before pause {node_id}: {self.errors[0]!r}")
            if not self.thread.is_alive():
                raise AssertionError(f"ended without pause {node_id}: {self.events}")
            time.sleep(0.01)
        raise AssertionError(f"timeout pause {node_id}")

    def resume(self, frame, action):
        assert self.session.resolve(frame["token"], action)

    def join(self):
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive(), "debug subgraph run hung"

    def paused(self, node_id):
        return [e for e in self.events if e.get("type") == "paused" and e.get("node_id") == node_id]


def test_u162_step_pauses_inside_subgraph_carry_path():
    run = _SubDebugRun().start()

    order = ["p-trigger", "subgraph-1", "c-trigger", "c-tool", "tool-after"]
    frames = []
    for node_id in order:
        frames.append(run.wait_paused(node_id))
        run.resume(frames[-1], "step")
    run.join()

    assert run.errors == []
    assert [f["node_id"] for f in run.events if f.get("type") == "paused"] == order

    # 子层两帧带完整 subgraphPath；父层节点（含 subgraph 节点自身）不带。
    by_id = {f["node_id"]: f for f in frames}
    assert by_id["c-trigger"]["subgraphPath"] == ["subgraph-1"]
    assert by_id["c-tool"]["subgraphPath"] == ["subgraph-1"]
    for top in ("p-trigger", "subgraph-1", "tool-after"):
        assert "subgraphPath" not in by_id[top]

    # 整图终帧唯一：子层 run_end 被吞。
    run_ends = [e for e in run.events if e.get("type") == "run_end"]
    assert len(run_ends) == 1


def test_u163_continue_hits_breakpoint_inside_subgraph_then_restores_emit():
    run = _SubDebugRun(breakpoints=[{"node_id": "c-tool"}]).start()
    run.resume(run.wait_paused("p-trigger"), "continue")

    hit = run.wait_paused("c-tool")
    assert hit["reason"] == "breakpoint"
    assert hit["subgraphPath"] == ["subgraph-1"]
    run.resume(hit, "continue")
    run.join()

    assert run.errors == []
    # 重入返回后 controller emit 栈已恢复：后继顶层节点帧不带路径。
    after_frames = run.paused("tool-after")
    assert after_frames == []  # continue 模式 tool-after 无断点不停
    assert run.controller._emit_stack == []
    top_node_end = [
        e for e in run.events
        if e.get("type") == "node_end" and e.get("node_id") == "tool-after"
    ]
    assert top_node_end and "subgraphPath" not in top_node_end[0]


def test_u164_logpoint_inside_subgraph_emits_namespaced_debug_log():
    run = _SubDebugRun(
        breakpoints=[{"node_id": "c-tool", "logMessage": "子层日志 {x}"}]
    ).start()
    run.resume(run.wait_paused("p-trigger"), "continue")

    deadline = time.monotonic() + TIMEOUT
    logs = []
    while time.monotonic() < deadline:
        logs = [e for e in run.events if e.get("type") == "debug_log"]
        if logs or not run.thread.is_alive():
            break
        time.sleep(0.01)
    run.join()

    assert len(logs) == 1
    assert logs[0]["node_id"] == "c-tool"
    assert logs[0]["message"] == "子层日志 {x}"
    assert logs[0]["subgraphPath"] == ["subgraph-1"]
    # logpoint 不暂停。
    assert run.paused("c-tool") == []


def test_u165_stop_inside_subgraph_propagates_debug_stopped_not_failed():
    run = _SubDebugRun().start()
    run.wait_paused("p-trigger")
    run.resume(run.paused("p-trigger")[0], "step")
    run.wait_paused("subgraph-1")
    run.resume(run.paused("subgraph-1")[0], "step")
    child = run.wait_paused("c-trigger")
    assert child["subgraphPath"] == ["subgraph-1"]
    run.resume(child, "stop")
    run.join()

    # DebugStopped 穿透子图 fail-safe，不被通用 except 折叠为 subgraph 节点 failed。
    assert len(run.errors) == 1
    assert isinstance(run.errors[0], DebugStopped)
    assert run.errors[0].node_id == "c-trigger"
    sub_ends = [
        e for e in run.events
        if e.get("type") == "node_end"
        and e.get("node_id") == "subgraph-1"
        and e.get("output", {}).get("status") == "failed"
    ]
    assert sub_ends == []
    assert not any(e.get("type") == "run_end" for e in run.events)

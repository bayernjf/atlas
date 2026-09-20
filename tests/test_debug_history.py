"""docs/28 批 2⑤：暂停帧变量变化历史（U150–U156，契约 docs/28 §3.1，04 §5.12）。

易失、随调试会话存活：相邻两次暂停之间 global 顶层键的新增/变更与经过节点；
不持久化、不进录制。resume 用户改写经 seed_baseline 同步基线、不计运行变化。
"""

from __future__ import annotations

import threading

from atlas.debug.controller import DebugController
from atlas.debug.sessions import VARIABLE_HISTORY_LIMIT, DebuggerBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.debug.sessions import DebugStopped

TIMEOUT = 5


def _broker_session(breakpoints=None):
    broker = DebuggerBroker()
    return broker, broker.create(graph_id="g-history", breakpoints=breakpoints)


def test_u150_first_pause_has_empty_changes_and_since_nodes():
    _, session = _broker_session()
    session.mark_node("trigger-1")
    session.snapshot_change(node_id="trigger-1", reason="step", globals_={"amount": 100})

    assert len(session.variable_history) == 1
    entry = session.variable_history[0]
    assert entry["seq"] == 0
    assert entry["node_id"] == "trigger-1"
    assert entry["reason"] == "step"
    assert entry["since_nodes"] == ["trigger-1"]
    assert entry["changes"] == []


def test_u151_second_pause_records_added_and_changed_keys():
    _, session = _broker_session()
    session.mark_node("trigger-1")
    session.snapshot_change(node_id="trigger-1", reason="step", globals_={"a": 1})

    # trigger-1 执行后到达 tool-1：a 被改写、b 新增。
    session.mark_node("tool-1")
    session.snapshot_change(node_id="tool-1", reason="step", globals_={"a": 2, "b": 3})

    entry = session.variable_history[1]
    assert entry["since_nodes"] == ["tool-1"]
    by_key = {c["key"]: c for c in entry["changes"]}
    assert by_key["a"] == {"key": "a", "old": 1, "new": 2}
    assert by_key["b"] == {"key": "b", "old": None, "new": 3}


def test_u152_unchanged_and_removed_keys_are_not_recorded():
    _, session = _broker_session()
    session.mark_node("n1")
    session.snapshot_change(node_id="n1", reason="step", globals_={"a": 1, "b": 2})

    # a 未变；b 被移除（v1 不记删除）。
    session.mark_node("n2")
    session.snapshot_change(node_id="n2", reason="step", globals_={"a": 1})

    changes = session.variable_history[1]["changes"]
    assert changes == []


def test_u153_resume_manual_override_seeds_baseline_and_is_not_a_change():
    _, session = _broker_session()
    session.mark_node("n1")
    session.snapshot_change(node_id="n1", reason="step", globals_={"a": 1})

    # 用户在暂停卡片把 a 改成 99、新增 x；resume 写回后 seed_baseline 同步基线。
    session.seed_baseline({"a": 99, "x": 1})
    # 下一节点 before：a 仍为 99（用户值），只有节点运行新增的 y 应被记录。
    session.mark_node("n2")
    session.snapshot_change(node_id="n2", reason="step", globals_={"a": 99, "x": 1, "y": 7})

    by_key = {c["key"]: c for c in session.variable_history[1]["changes"]}
    assert set(by_key) == {"y"}
    assert by_key["y"] == {"key": "y", "old": None, "new": 7}


def test_u154_history_is_capped_at_limit_dropping_oldest():
    _, session = _broker_session()
    for i in range(VARIABLE_HISTORY_LIMIT + 5):
        session.mark_node(f"n{i}")
        session.snapshot_change(node_id=f"n{i}", reason="step", globals_={"k": i})

    assert len(session.variable_history) == VARIABLE_HISTORY_LIMIT
    # 最旧的 5 条被丢弃，保留最后 50 条（seq 为入列时序号，不重排）。
    assert session.variable_history[0]["node_id"] == "n5"
    assert session.variable_history[-1]["node_id"] == f"n{VARIABLE_HISTORY_LIMIT + 4}"


def test_u155_unjsonable_value_is_skipped_fail_safe():
    _, session = _broker_session()
    session.mark_node("n1")
    session.snapshot_change(node_id="n1", reason="step", globals_={"a": 1})

    session.mark_node("n2")
    # 含不可 JSON 序列化值（线程锁对象）；不得抛、不得进入历史。
    session.snapshot_change(
        node_id="n2", reason="step", globals_={"a": 1, "bad": threading.Lock()}
    )

    changes = session.variable_history[1]["changes"]
    assert [c["key"] for c in changes] == []
    # 历史整体仍可 JSON 序列化（要随 paused 帧下发）。
    import json as _json

    _json.dumps(session.variable_history)


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


def test_u156_paused_frame_carries_history_end_to_end():
    graph = _linear_graph()
    events: list[dict] = []
    errors: list[Exception] = []
    broker = DebuggerBroker()
    session = broker.create(graph_id="g-history", breakpoints=None)
    controller = DebugController(session, events.append)

    def work():
        try:
            run_graph(
                graph,
                inputs={"amount": 1500},
                graph_id="g-history",
                emit=events.append,
                debug_controller=controller,
            )
        except DebugStopped as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    # 首暂停帧（trigger-1，step）：纯超集 history，一条、changes 空。
    deadline_events = events
    first = None
    import time

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        for e in reversed(deadline_events):
            if e.get("type") == "paused" and e.get("node_id") == "trigger-1":
                first = e
                break
        if first:
            break
        time.sleep(0.01)
    assert first is not None

    assert "history" in first
    assert len(first["history"]) == 1
    assert first["history"][0]["changes"] == []
    assert first["history"][0]["since_nodes"] == ["trigger-1"]

    assert session.resolve(first["token"], "stop")
    thread.join(TIMEOUT)
    assert not thread.is_alive()

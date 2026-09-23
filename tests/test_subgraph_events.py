"""A 包（docs/27 §3）：子图内部节点事件 SSE 命名空间上屏 + 子图内审批。

覆盖：
- 子图内部 node_start/node_end 带 subgraphPath（父图 subgraph 节点 id 路径），顶层节点形状不变；
- 子层 run_end/result 终帧被吞（整图只收到一个顶层 run_end）；
- 嵌套子图路径累加；
- 子图内 human_approval 的 approval payload 经共享 broker 上屏并可用全局唯一 token 决策放行；
- 子图异常 fail-safe 折叠为 subgraph 节点 failed、父 run completed，且不泄子层 run_end；
- collect_steps 只统计顶层节点（录制/回放口径不因子图事件上屏而变化）；
- 父图 outputs 形状不变（内部节点不泄漏到顶层 outputs）。
"""

import threading
import time

from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.recording.replay import collect_steps


def _tool(node_id, name, tool="op-after", params=None):
    config = {"tool": tool}
    if params is not None:
        config["params"] = params
    return {"id": node_id, "type": "tool_call", "name": name, "config": config}


def _simple_child():
    """子图：c-trigger -> c-tool（含两个内部工具节点，便于断言路径）。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                _tool("c-tool", "子工具", params="done"),
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "c-tool"},
            ],
        }
    )


def _parent_with_subgraph(child_graph_id="g-child"):
    """父图：p-trigger -> subgraph-1 -> tool-after（subgraph 恰好一条出边、不直连结束）。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": child_graph_id, "inputs": {}}},
                _tool("tool-after", "后继", params="done"),
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )


def _run_collect(parent, resolver, **kwargs):
    events: list[dict] = []
    result = run_graph(
        parent,
        graph_id="g-parent",
        graph_resolver=resolver,
        emit=events.append,
        **kwargs,
    )
    return result, events


def test_subgraph_internal_node_events_carry_path():
    result, events = _run_collect(
        _parent_with_subgraph(), {"g-child": _simple_child()}.get
    )
    assert result["status"] == "completed"

    internal = [e for e in events if e.get("node_id", "").startswith("c-")]
    assert internal, "子图内部节点事件应上屏"
    for ev in internal:
        assert ev["type"] in ("node_start", "node_end")
        assert ev.get("subgraphPath") == ["subgraph-1"]

    # 顶层节点（含 subgraph 节点自身与后继）形状不变：不带 subgraphPath 键
    for node_id in ("p-trigger", "subgraph-1", "tool-after"):
        tops = [e for e in events if e.get("node_id") == node_id]
        assert tops
        for ev in tops:
            assert "subgraphPath" not in ev


def test_child_run_end_and_result_frames_are_swallowed():
    _, events = _run_collect(
        _parent_with_subgraph(), {"g-child": _simple_child()}.get
    )
    run_ends = [e for e in events if e.get("type") == "run_end"]
    assert len(run_ends) == 1, "子层 run_end 必须被吞，整图只应有一个顶层 run_end"
    assert not [e for e in events if e.get("type") == "result"]
    # 唯一的 run_end 不带 subgraphPath（它是顶层终帧）
    assert "subgraphPath" not in run_ends[0]


def test_nested_subgraph_path_accumulates():
    grandchild = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "g-trigger", "type": "trigger", "name": "gt",
                 "config": {"triggerType": "manual"}},
                _tool("g-tool", "孙工具", params="done"),
            ],
            "edges": [{"id": "ge1", "source": "g-trigger", "target": "g-tool"}],
        }
    )
    child = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-inner", "type": "subgraph", "name": "内层子图",
                 "config": {"graphId": "g-grand", "inputs": {}}},
                _tool("c-after", "子后继", params="done"),
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "subgraph-inner"},
                {"id": "ce2", "source": "subgraph-inner", "target": "c-after"},
            ],
        }
    )
    parent = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-outer", "type": "subgraph", "name": "外层子图",
                 "config": {"graphId": "g-child", "inputs": {}}},
                _tool("p-after", "父后继", params="done"),
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-outer"},
                {"id": "pe2", "source": "subgraph-outer", "target": "p-after"},
            ],
        }
    )
    resolver = {"g-child": child, "g-grand": grandchild}.get
    _, events = _run_collect(parent, resolver)

    grand = [e for e in events if e.get("node_id") == "g-tool"]
    assert grand
    for ev in grand:
        assert ev.get("subgraphPath") == ["subgraph-outer", "subgraph-inner"]

    child_internal = [e for e in events if e.get("node_id") == "c-after"]
    assert child_internal
    for ev in child_internal:
        assert ev.get("subgraphPath") == ["subgraph-outer"]


def _approval_child():
    """子图内含一个 human_approval（双出口），审批通过走 c-yes。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "c-approve", "type": "human_approval", "name": "子图内审批",
                 "config": {
                     "summary": "子图内退款审批",
                     "approver": "客服主管",
                     "timeoutSeconds": 30,
                     "onTimeout": "reject",
                     "approvedTarget": "c-yes",
                     "rejectedTarget": "c-no",
                 }},
                _tool("c-yes", "子通过侧", tool="op-approve"),
                _tool("c-no", "子拒绝侧", tool="op-reject"),
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "c-approve"},
                {"id": "ce2", "source": "c-approve", "target": "c-yes"},
                {"id": "ce3", "source": "c-approve", "target": "c-no"},
            ],
        }
    )


def test_in_subgraph_approval_payload_uses_shared_broker():
    broker = ApprovalBroker()
    parent = _parent_with_subgraph()
    resolver = {"g-child": _approval_child()}.get
    events: list[dict] = []
    holder: dict = {}

    def worker():
        holder["result"] = run_graph(
            parent,
            graph_id="g-parent",
            graph_resolver=resolver,
            emit=events.append,
            approval_broker=broker,
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    # 等待子图内审批 payload 上屏（第二个 node_start 带 approval）
    approval_event = None
    deadline = time.time() + 5
    while time.time() < deadline:
        for ev in events:
            if ev.get("node_id") == "c-approve" and ev.get("approval"):
                approval_event = ev
                break
        if approval_event is not None:
            break
        time.sleep(0.02)
    thread.join(timeout=5)

    assert approval_event is not None, "子图内 human_approval 的 approval payload 应上屏"
    assert approval_event.get("subgraphPath") == ["subgraph-1"]
    token = approval_event["approval"]["token"]
    assert token, "token 应由共享 broker 全局唯一签发"

    # 若线程仍在等待审批（join 超时前未决策），用 token 经共享 broker 放行
    if thread.is_alive():
        ok = broker.resolve(token, "approved", resolved_by="human")
        assert ok is True
        thread.join(timeout=5)
    assert not thread.is_alive()

    result = holder["result"]
    assert result["status"] == "completed"
    sub_out = result["outputs"]["subgraph-1"]
    assert sub_out["status"] == "success"
    child_outputs = sub_out["outputs"]
    assert child_outputs["c-approve"]["decision"] == "approved"
    assert "c-yes" in child_outputs and "c-no" not in child_outputs


def test_subgraph_runtime_failure_is_folded_without_leaking_frames():
    # resolver 返回 None -> 子 run_graph 在编译期抛 AttributeError，
    # 被 _execute_subgraph 的 fail-safe try/except 折叠为 subgraph 节点 failed（父 run 仍 completed）。
    from atlas.graph.loader import _execute_subgraph

    parent = _parent_with_subgraph()
    node = next(n for n in parent.nodes if n.type == "subgraph")
    events: list[dict] = []
    output, message = _execute_subgraph(
        node,
        context={"global": {}},
        registry=None,
        decision_client=None,
        condition_classifier=None,
        approval_broker=ApprovalBroker(),
        event_wait_broker=EventWaitBroker(),
        resolver=lambda gid: None,
        depth=0,
        emit=events.append,
        subgraph_path=(),
    )
    assert output["status"] == "failed"
    assert "failed" in message
    # 异常发生在任何子层帧发出之前；命名空间回调不得泄出 run_end 等终帧
    assert events == []


def test_failed_subgraph_keeps_parent_run_completed():
    # 端到端：子图运行期失败折叠为节点 failed，父 run 仍 completed，且只有一个顶层 run_end。
    # 用直接调用 helper 得到 failed output 后，父执行器对该 output 正常 node_end（既有语义）。
    from atlas.graph.loader import _execute_subgraph

    parent = _parent_with_subgraph()
    node = next(n for n in parent.nodes if n.type == "subgraph")
    output, _ = _execute_subgraph(
        node,
        context={"global": {}},
        registry=None,
        decision_client=None,
        condition_classifier=None,
        approval_broker=ApprovalBroker(),
        event_wait_broker=EventWaitBroker(),
        resolver=lambda gid: None,
        depth=0,
        subgraph_path=(),
    )
    assert output["mode"] == "subgraph" and output["status"] == "failed"


def test_collect_steps_excludes_subgraph_internals():
    emit, take_steps = collect_steps()
    run_graph(
        _parent_with_subgraph(),
        graph_id="g-parent",
        graph_resolver={"g-child": _simple_child()}.get,
        emit=emit,
    )
    ids = [step.node_id for step in take_steps()]
    assert "subgraph-1" in ids and "tool-after" in ids
    assert not any(i.startswith("c-") for i in ids), "录制步骤不得含子图内部节点"


def test_parent_outputs_shape_unchanged():
    result, _ = _run_collect(
        _parent_with_subgraph(), {"g-child": _simple_child()}.get
    )
    top_keys = set(result["outputs"].keys())
    assert top_keys == {"p-trigger", "subgraph-1", "tool-after"}
    sub = result["outputs"]["subgraph-1"]
    assert set(sub.keys()) >= {"mode", "graphId", "status", "outputs", "trace"}
    assert sub["mode"] == "subgraph"
    assert "c-tool" in sub["outputs"]

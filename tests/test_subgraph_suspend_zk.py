"""打包 ZK（04 §5.11 契约补丁）：子图内挂起点在持久化档的显式拒绝（fail-closed）。

覆盖（各带反向对照）：
- U1051 子图内 human_approval + 持久化档（_block_subgraph_suspend=True）→ 抛
  SubgraphSuspendUnsupported（穿透 _execute_subgraph fail-safe），frame_sink 零帧、
  broker pending 零泄漏（拦截在 _register_approval 之前）；
- U1052 反向：内存档（False）→ 照旧挂起（写帧、broker pending、resolve 可 completed）；
- U1053 子图内 event wait（无预置）+ 持久化档 → 同样抛错（拦截在 request_any 前）；
- U1054 反向：路径限定预置键 "sub-1/c-wait" → 不拦秒过（resolvedBy=input）；
- U1055 嵌套两层子图内挂起 + 持久化档 → 同样抛错（subgraph_depth 判据在嵌套层生效）；
- U1056 反向：顶层挂起 + 持久化档 → 不受影响（写帧、可 resolve）——顶层零回归。
"""

import threading
import time

import pytest

from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import SubgraphSuspendUnsupported, run_graph


def _tool(node_id, name, tool="op-after", params=None):
    config = {"tool": tool}
    if params is not None:
        config["params"] = params
    return {"id": node_id, "type": "tool_call", "name": name, "config": config}


def _approval_child():
    """子图：c-trigger -> c-approve(human_approval) -> c-yes（双出口）。"""
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


def _wait_child():
    """子图：c-trigger -> c-wait(event) -> c-tool-after。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "c-wait", "type": "wait", "name": "等待事件",
                 "config": {"waitType": "event", "eventKey": "order_paid",
                            "timeoutSeconds": 30, "onTimeout": "continue"}},
                _tool("c-tool-after", "子后继", params="done"),
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "c-wait"},
                {"id": "ce2", "source": "c-wait", "target": "c-tool-after"},
            ],
        }
    )


def _parent(child_id="g-child", sub_node="subgraph-1"):
    """父图：p-trigger -> subgraph -> tool-after（subgraph 恰好一条出边）。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": sub_node, "type": "subgraph", "name": "子流程",
                 "config": {"graphId": child_id, "inputs": {}}},
                _tool("tool-after", "后继", params="done"),
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": sub_node},
                {"id": "pe2", "source": sub_node, "target": "tool-after"},
            ],
        }
    )


def test_u1051_subgraph_approval_blocked_in_persistent_mode():
    """持久化档（block=True）：子图内 human_approval 挂起 → 抛错穿透 fail-safe、零帧、零 pending。"""
    broker = ApprovalBroker()
    parent = _parent()
    resolver = {"g-child": _approval_child()}.get
    frames: list[dict] = []

    with pytest.raises(SubgraphSuspendUnsupported) as ei:
        run_graph(
            parent,
            graph_id="g-parent",
            graph_resolver=resolver,
            approval_broker=broker,
            frame_sink=frames.append,
            _block_subgraph_suspend=True,
        )
    exc = ei.value
    assert exc.code == "SUBGRAPH_SUSPEND_UNSUPPORTED"
    assert exc.node_id == "c-approve"  # 子图内节点 id 透传
    assert frames == [], "拦截发生在 _emit_frame 之前：不得写任何中断帧"
    assert broker.list_pending() == [], "拦截发生在 _register_approval 之前：不得泄漏 pending"


def test_u1052_subgraph_approval_still_works_in_memory_mode():
    """反向对照：内存档（block=False，无跨进程承诺）→ 不拦；子图内审批照旧登记 broker
    pending（子图重入本就不写中断帧，本批契约不改变此行为），resolve 可正常 completed。"""
    broker = ApprovalBroker()
    parent = _parent()
    resolver = {"g-child": _approval_child()}.get
    holder: dict = {}

    def worker():
        holder["result"] = run_graph(
            parent,
            graph_id="g-parent",
            graph_resolver=resolver,
            approval_broker=broker,
            _block_subgraph_suspend=False,
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline:
        if broker.list_pending():
            break
        time.sleep(0.02)
    pending = broker.list_pending()
    assert pending, "内存档应照旧登记 pending（子图内审批可挂起）"
    token = pending[0]["token"]
    if thread.is_alive():
        assert broker.resolve(token, "approved", resolved_by="tester") is True
        thread.join(timeout=5)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["status"] == "completed"
    child_out = result["outputs"]["subgraph-1"]
    assert child_out["status"] == "success"
    assert child_out["outputs"]["c-approve"]["decision"] == "approved"


def test_u1053_subgraph_event_wait_blocked_in_persistent_mode():
    """持久化档：子图内 event wait 真挂起（无预置）→ 抛错、零 pending 泄漏。"""
    broker = EventWaitBroker()
    parent = _parent()
    resolver = {"g-child": _wait_child()}.get
    frames: list[dict] = []

    with pytest.raises(SubgraphSuspendUnsupported) as ei:
        run_graph(
            parent,
            graph_id="g-parent",
            graph_resolver=resolver,
            event_wait_broker=broker,
            frame_sink=frames.append,
            _block_subgraph_suspend=True,
        )
    assert ei.value.code == "SUBGRAPH_SUSPEND_UNSUPPORTED"
    assert ei.value.node_id == "c-wait"
    assert frames == [], "拦截发生在 request_any/_emit_frame 之前：不得写帧"
    assert broker.list_pending() == [], "拦截发生在 request_any 之前：不得泄漏 pending"


def test_u1054_subgraph_event_wait_preset_bypasses_block():
    """反向对照：路径限定预置键 "sub-1/c-wait" 不真挂起 → 放行秒过（resolvedBy=input）。"""
    parent = _parent()
    resolver = {"g-child": _wait_child()}.get
    frames: list[dict] = []

    result = run_graph(
        parent,
        graph_id="g-parent",
        graph_resolver=resolver,
        inputs={"waitEvents": {"subgraph-1/c-wait": {"paidAt": "2026-10-02"}}},
        frame_sink=frames.append,
        _block_subgraph_suspend=True,
    )
    assert result["status"] == "completed"
    child = result["outputs"]["subgraph-1"]["outputs"]
    assert child["c-wait"]["resolvedBy"] == "input"
    assert frames == [], "预置路径不写帧"


def test_u1055_nested_subgraph_suspension_blocked():
    """持久化档：嵌套两层子图内挂起同样拦截（subgraph_depth 判据在嵌套层生效）。"""
    broker = ApprovalBroker()
    inner = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "i-trigger", "type": "trigger", "name": "it",
                 "config": {"triggerType": "manual"}},
                {"id": "i-approve", "type": "human_approval", "name": "孙图内审批",
                 "config": {
                     "summary": "孙图审批",
                     "approver": "主管",
                     "timeoutSeconds": 30,
                     "onTimeout": "reject",
                     "approvedTarget": "i-yes",
                     "rejectedTarget": "i-no",
                 }},
                _tool("i-yes", "孙通过侧", tool="op-approve"),
                _tool("i-no", "孙拒绝侧", tool="op-reject"),
            ],
            "edges": [
                {"id": "ie1", "source": "i-trigger", "target": "i-approve"},
                {"id": "ie2", "source": "i-approve", "target": "i-yes"},
                {"id": "ie3", "source": "i-approve", "target": "i-no"},
            ],
        }
    )
    mid = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "m-trigger", "type": "trigger", "name": "mt",
                 "config": {"triggerType": "manual"}},
                {"id": "sub-2", "type": "subgraph", "name": "子子流程",
                 "config": {"graphId": "g-inner", "inputs": {}}},
                _tool("m-after", "中子后继", params="done"),
            ],
            "edges": [
                {"id": "me1", "source": "m-trigger", "target": "sub-2"},
                {"id": "me2", "source": "sub-2", "target": "m-after"},
            ],
        }
    )
    parent = _parent(child_id="g-mid", sub_node="subgraph-1")
    resolver = {"g-mid": mid, "g-inner": inner}.get

    with pytest.raises(SubgraphSuspendUnsupported) as ei:
        run_graph(
            parent,
            graph_id="g-parent",
            graph_resolver=resolver,
            approval_broker=broker,
            _block_subgraph_suspend=True,
        )
    assert ei.value.code == "SUBGRAPH_SUSPEND_UNSUPPORTED"
    assert ei.value.node_id == "i-approve"
    assert broker.list_pending() == []


def test_u1056_top_level_suspension_untouched_in_persistent_mode():
    """反向对照：顶层挂起 + 持久化档 → 不受影响（写帧、resolve 可 completed）——顶层零回归。"""
    broker = ApprovalBroker()
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "t-trigger", "type": "trigger", "name": "tt",
                 "config": {"triggerType": "manual"}},
                {"id": "t-approve", "type": "human_approval", "name": "顶层审批",
                 "config": {
                     "summary": "顶层退款审批",
                     "approver": "主管",
                     "timeoutSeconds": 30,
                     "onTimeout": "reject",
                     "approvedTarget": "t-yes",
                     "rejectedTarget": "t-no",
                 }},
                _tool("t-yes", "通过侧", tool="op-approve"),
                _tool("t-no", "拒绝侧", tool="op-reject"),
            ],
            "edges": [
                {"id": "te1", "source": "t-trigger", "target": "t-approve"},
                {"id": "te2", "source": "t-approve", "target": "t-yes"},
                {"id": "te3", "source": "t-approve", "target": "t-no"},
            ],
        }
    )
    frames: list[dict] = []
    holder: dict = {}

    def worker():
        holder["result"] = run_graph(
            graph,
            graph_id="g-top",
            approval_broker=broker,
            frame_sink=frames.append,
            _block_subgraph_suspend=True,
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while time.time() < deadline:
        if frames:
            break
        time.sleep(0.02)
    assert frames, "顶层挂起不受拦截：应写中断帧"
    assert frames[0]["kind"] == "approval"
    assert broker.list_pending(), "顶层应照旧登记 pending"
    token = frames[0]["resume_token"]
    if thread.is_alive():
        assert broker.resolve(token, "approved", resolved_by="tester") is True
        thread.join(timeout=5)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["status"] == "completed"
    assert result["outputs"]["t-approve"]["decision"] == "approved"

"""M10 批 2：run/节点/tool/parallel/subgraph 埋点 + SSE 三帧超集（U50）。

契约：04 §5.15 / 06 §6.15 / 03 run_event+trace_span / docs/19 §2.3.4。
- run_graph 自建/复用 Tracer，result 带 traceId/traceTree；
- node_start/node_end 带 traceId/spanId/parentSpanId，run_end 带 traceId/spanId/graphVersion；
  只增字段、不增事件类型（前端零改动）；
- 工具 FAILED→tool/node span error；parallel fork attrs、__join__ 汇聚 span internal 折叠；
- subgraph 复用同一 tracer、子图内部 span internal，to_tree(include_internal=False) 折叠。
"""

from __future__ import annotations

import re

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.tracing import KIND_PARALLEL, KIND_SUBGRAPH, KIND_TOOL, Tracer

HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _collect(node: dict, acc: list[dict]) -> None:
    acc.append(node)
    for child in node.get("children", []):
        _collect(child, acc)


def _simple_graph(tool: str = "op-x"):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "tool-1", "type": "tool_call", "name": "工具",
                 "config": {"tool": tool}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
    )


def _child_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "child-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "child-tool", "type": "tool_call", "name": "ctool",
                 "config": {"tool": "op-child"}},
            ],
            "edges": [{"id": "ce1", "source": "child-trigger", "target": "child-tool"}],
        }
    )


def _parent_graph(child_id: str):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": child_id, "inputs": {}}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )


def _parallel_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "parallel-1", "type": "parallel", "name": "并行",
                 "config": {
                     "joinStrategy": "all_success",
                     "branches": [
                         {"label": "A", "target": "tool-a"},
                         {"label": "B", "target": "tool-b"},
                     ],
                     "joinTarget": "tool-join",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": "op-a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": "op-b"}},
                {"id": "tool-join", "type": "tool_call", "name": "汇聚",
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


def test_result_carries_trace_id_and_closed_span_tree():
    result = run_graph(_simple_graph(), graph_id="g", graph_version="g@7")
    assert HEX32.match(result["traceId"])
    tree = result["traceTree"]
    assert tree["kind"] == "run"
    assert tree["graphVersion"] == "g@7"

    flat: list[dict] = []
    _collect(tree, flat)
    # run + 2 节点
    assert len(flat) == 3
    assert {n["traceId"] for n in flat} == {result["traceId"]}
    ids = [n["spanId"] for n in flat]
    assert all(HEX16.match(i) for i in ids) and len(set(ids)) == 3
    # 两个节点 span 的父都是 root
    root_id = tree["spanId"]
    for child in tree["children"]:
        assert child["kind"] == "node"
        assert child["parentSpanId"] == root_id


def test_sse_frames_are_superset_with_no_new_event_types():
    events: list[dict] = []
    run_graph(
        _simple_graph(),
        graph_id="g",
        graph_version="g@7",
        emit=events.append,
    )
    types = [e["type"] for e in events]
    # docs/28 §4.1 ⑧ 起工具节点新增 tool_metric 埋点帧（仍为既有节点/终帧之外的受控新事件型）
    assert set(types) <= {"node_start", "node_end", "run_end", "tool_metric"}
    assert "run_end" in types

    for event in events:
        if event["type"] in ("node_start", "node_end"):
            assert HEX32.match(event["traceId"])
            assert HEX16.match(event["spanId"])
            assert HEX16.match(event["parentSpanId"])  # 节点帧父为 root
        if event["type"] == "run_end":
            assert HEX32.match(event["traceId"])
            assert HEX16.match(event["spanId"])
            assert "parentSpanId" not in event  # root 无父
            assert event["graphVersion"] == "g@7"
            # 原有字段保留
            assert event["status"] == "completed"
            assert "outputs" in event and "trace" in event


def test_run_end_omits_graph_version_when_unversioned():
    events: list[dict] = []
    run_graph(_simple_graph(), emit=events.append)
    run_end = next(e for e in events if e["type"] == "run_end")
    assert "graphVersion" not in run_end


def test_explicit_none_tracer_disables_instrumentation():
    """debug 单步路径显式传 tracer=None：不建 span，SSE 帧保持无 span 字段旧形状。"""
    events: list[dict] = []
    result = run_graph(_simple_graph(), tracer=None, emit=events.append)
    assert "traceId" not in result
    assert "traceTree" not in result
    for event in events:
        assert "traceId" not in event
        assert "spanId" not in event
        assert "parentSpanId" not in event


def test_failed_tool_marks_tool_and_node_spans_error():
    # web-playwright 适配器未在 demo registry 注册 → 工具 FAILED
    result = run_graph(_simple_graph(tool="web-playwright/click"))
    flat: list[dict] = []
    _collect(result["traceTree"], flat)
    tool_spans = [n for n in flat if n["kind"] == KIND_TOOL]
    assert len(tool_spans) == 1
    ts = tool_spans[0]
    assert ts["status"] == "error"
    assert ts["attrs"]["adapter"] == "web-playwright"
    assert ts["attrs"]["capability"] == "click"
    # 承载该工具的 node span 也标 error
    node_span = next(
        n for n in flat if n["spanId"] == ts["parentSpanId"]
    )
    assert node_span["status"] == "error"


def test_subgraph_reuses_tracer_and_internal_spans_fold():
    child_id = "graph-child"
    tracer = Tracer(graph_id="graph-parent", graph_version="graph-parent@3")
    result = run_graph(
        _parent_graph(child_id),
        graph_id="graph-parent",
        graph_version="graph-parent@3",
        graph_resolver={child_id: _child_graph()}.get,
        tracer=tracer,
    )
    assert result["traceId"] == tracer.trace_id  # 子图复用同一 trace

    full = tracer.to_tree(include_internal=True)
    folded = tracer.to_tree(include_internal=False)

    full_flat: list[dict] = []
    _collect(full, full_flat)
    # 完整树含 subgraph span 与子图 internal 节点
    sub = next(n for n in full_flat if n["kind"] == KIND_SUBGRAPH)
    assert sub["attrs"]["graphId"] == child_id
    inner = [n for n in full_flat if n["name"] in ("trigger:child-trigger", "tool_call:child-tool")]
    assert inner and all(n.get("internal") is True for n in inner)
    # 子图 internal 节点的祖先链闭合到 subgraph span
    assert all(n["traceId"] == tracer.trace_id for n in full_flat)

    fold_flat: list[dict] = []
    _collect(folded, fold_flat)
    # 折叠后无 child-* 内部节点、无任何 internal 外泄，但 subgraph span 保留
    assert not [n for n in fold_flat if "child-" in n["name"]]
    assert not [n for n in fold_flat if n.get("internal") is True]
    folded_sub = next(n for n in fold_flat if n["kind"] == KIND_SUBGRAPH)
    # 折叠为单个 subgraph span，身份/属性/状态保留（U52①）
    assert folded_sub["spanId"] == sub["spanId"]
    assert folded_sub["attrs"]["graphId"] == child_id
    assert folded_sub["status"] == sub["status"]


def test_parallel_fork_span_exposed_join_span_internal():
    tracer = Tracer(graph_id="g")
    run_graph(_parallel_graph(), graph_id="g", tracer=tracer)

    folded = tracer.to_tree(include_internal=False)
    full = tracer.to_tree(include_internal=True)
    fold_flat: list[dict] = []
    _collect(folded, fold_flat)
    full_flat: list[dict] = []
    _collect(full, full_flat)

    # __join__ 网关不外泄：折叠树无 internal span、无 __join__ 命名
    assert not [n for n in fold_flat if n.get("internal") is True]
    assert not [n for n in fold_flat if "__join__" in n["name"]]

    # fork 的 parallel span 外显，记 fork attrs
    fork_spans = [
        n for n in fold_flat
        if n["kind"] == KIND_PARALLEL and n["name"] == "parallel:parallel-1"
    ]
    assert len(fork_spans) == 1
    assert fork_spans[0]["attrs"]["forkCount"] == 2
    assert set(fork_spans[0]["attrs"]["targets"]) == {"tool-a", "tool-b"}

    # 汇聚补发达成时建一个 internal parallel-join span（仅完整树可见）
    join_spans = [n for n in full_flat if n["name"] == "parallel-join:parallel-1"]
    assert len(join_spans) == 1
    assert join_spans[0].get("internal") is True

"""M10 批 1：tracing 包 span 模型 + contextvars 传播（08 M10 立项条，U49）。

覆盖 03 ``trace_span`` / 04 §5.15 / 06 §6.15：traceId/spanId/parentSpanId 形状与闭合、
嵌套父子、异常 status=error 不泄漏当前 span、graphVersion 继承、internal 折叠、
后台线程 attach/reset 语义。纯 stdlib、零新依赖。
"""

from __future__ import annotations

import re
import threading

import pytest

from atlas.tracing import (
    KIND_NODE,
    KIND_RUN,
    KIND_SUBGRAPH,
    KIND_TOOL,
    Span,
    Tracer,
    current_span,
    new_span_id,
    new_trace_id,
)

HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _collect(node: dict, acc: list[dict]) -> None:
    acc.append(node)
    for child in node.get("children", []):
        _collect(child, acc)


def test_id_shapes():
    assert HEX32.match(new_trace_id())
    assert HEX16.match(new_span_id())


def test_root_run_span_and_trace_context():
    tracer = Tracer(graph_id="refund-flow", graph_version="refund-flow@7")
    tree = tracer.finish()
    assert tree["kind"] == KIND_RUN
    assert tree["name"] == "run:refund-flow"
    assert HEX32.match(tree["traceId"])
    assert HEX16.match(tree["spanId"])
    assert "parentSpanId" not in tree  # root 无父
    assert tree["graphVersion"] == "refund-flow@7"
    assert tree["status"] == "ok"
    assert tracer.trace_id == tree["traceId"]


def test_nested_parent_child_links_close():
    tracer = Tracer(graph_id="g", graph_version="g@3")
    with tracer.span("node:tool", kind=KIND_NODE, nodeId="n1") as node:
        with tracer.span("tool:shop/refund", kind=KIND_TOOL, adapter="shop") as tool:
            tool_span_id = tool.span_id
        node_span_id = node.span_id
    tree = tracer.finish()

    flat: list[dict] = []
    _collect(tree, flat)
    by_id = {n["spanId"]: n for n in flat}

    # 全部 span 共享同一 traceId，spanId 唯一
    trace_ids = {n["traceId"] for n in flat}
    assert trace_ids == {tree["traceId"]}
    assert len(by_id) == len(flat) == 3

    tool_node = by_id[tool_span_id]
    node_node = by_id[node_span_id]
    assert tool_node["parentSpanId"] == node_span_id
    assert node_node["parentSpanId"] == tree["spanId"]
    # attrs 透传
    assert node_node["attrs"] == {"nodeId": "n1"}
    assert tool_node["attrs"] == {"adapter": "shop"}
    # 子 span 继承 root graphVersion
    assert tool_node["graphVersion"] == tree["graphVersion"]


def test_exception_marks_error_and_restores_current_span():
    tracer = Tracer(graph_id="g")
    outer = tracer.root
    assert current_span() is None  # 未 attach
    with pytest.raises(RuntimeError):
        with tracer.span("node:boom", kind=KIND_NODE) as boom:
            assert current_span() is boom
            raise RuntimeError("node failed")
    # 异常后 span 结束为 error，且当前 span 已复位（不泄漏）
    assert boom.status == "error"
    assert boom.end_ns is not None
    assert current_span() is None
    tree = tracer.finish("error")
    flat: list[dict] = []
    _collect(tree, flat)
    boom_node = next(n for n in flat if n["spanId"] == boom.span_id)
    assert boom_node["status"] == "error"


def test_explicit_parent_for_subgraph_reentry():
    """子图重入复用同一 tracer：subgraph span 为子图节点的父（04 §5.15）。"""
    tracer = Tracer(graph_id="parent")
    with tracer.span("node:sub", kind=KIND_NODE) as parent_node:
        with tracer.span("subgraph:child-flow", kind=KIND_SUBGRAPH) as sg:
            # 子图内部节点标 internal，显式 parent=sg
            with tracer.span("node:inner", kind=KIND_NODE, parent=sg, internal=True):
                pass
    full = tracer.to_tree(include_internal=True)
    folded = tracer.to_tree(include_internal=False)

    flat_full: list[dict] = []
    _collect(full, flat_full)
    assert len(flat_full) == 4  # run + node + subgraph + inner

    flat_folded: list[dict] = []
    _collect(folded, flat_folded)
    assert len(flat_folded) == 3  # inner 被折叠
    kinds = [(n["name"], n["kind"]) for n in flat_folded]
    assert ("node:inner", KIND_NODE) not in kinds
    assert any(n["kind"] == KIND_SUBGRAPH for n in flat_folded)


def test_current_context_triple():
    tracer = Tracer(graph_id="g")
    root_ctx = tracer.current_context()
    assert set(root_ctx) == {"traceId", "spanId"}  # root 无 parentSpanId
    with tracer.span("node:x", kind=KIND_NODE) as node:
        ctx = tracer.current_context()
        assert ctx["traceId"] == tracer.trace_id
        assert ctx["spanId"] == node.span_id
        assert ctx["parentSpanId"] == tracer.root.span_id


def test_end_is_idempotent():
    s = Span(trace_id="t", span_id="s", parent_span_id=None, name="x")
    s.end("ok")
    first_end = s.end_ns
    s.end("error")  # 不覆盖
    assert s.end_ns == first_end
    assert s.status == "ok"


def test_attach_reset_for_background_thread():
    """SSE worker 后台线程入口 attach(root)，同线程节点就近取父，退出 reset。"""
    tracer = Tracer(graph_id="g")
    seen: dict[str, object] = {}

    def worker() -> None:
        token = tracer.attach()
        try:
            assert current_span() is tracer.root
            with tracer.span("node:bg", kind=KIND_NODE) as node:
                seen["parent"] = node.parent_span_id
                seen["current"] = current_span() is node
        finally:
            tracer.reset(token)
            seen["after_reset"] = current_span()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["parent"] == tracer.root.span_id
    assert seen["current"] is True
    assert seen["after_reset"] is None
    # 主线程上下文未被后台线程污染
    assert current_span() is None


def test_finish_closes_root_and_duration_nonnegative():
    tracer = Tracer(graph_id="g")
    tree = tracer.finish()
    assert tree["durationMs"] >= 0
    assert tracer.root.end_ns is not None

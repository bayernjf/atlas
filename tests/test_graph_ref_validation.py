"""编译期 L2 模板引用复查（04 §6.5，I20）：规则与前端 lib/scope.ts 同构。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import build_demo_registry, compile_graph


def _chain(*nodes: dict, edges: list[dict] | None = None, variables: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "variables": variables or [],
        "nodes": list(nodes),
        "edges": edges
        if edges is not None
        else [
            {"id": f"e{i}", "source": nodes[i]["id"], "target": nodes[i + 1]["id"]}
            for i in range(len(nodes) - 1)
        ],
    }


def _trigger(node_id: str = "trigger-1") -> dict:
    return {
        "id": node_id,
        "type": "trigger",
        "name": "触发",
        "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"},
    }


def _ai(node_id: str, template: str) -> dict:
    return {
        "id": node_id,
        "type": "ai_decision",
        "name": node_id,
        "config": {"promptTemplate": template, "model": "demo"},
    }


def _tool(node_id: str, tool: str, params: str = "{}") -> dict:
    return {"id": node_id, "type": "tool_call", "name": node_id, "config": {"tool": tool, "params": params}}


def _compile_error(raw: dict) -> str:
    graph = parse_graph(raw)
    with pytest.raises(GraphValidationError) as exc_info:
        compile_graph(graph, registry=build_demo_registry())
    return "; ".join(exc_info.value.errors)


def test_legal_template_style_refs_compile():
    raw = _chain(
        _trigger(),
        _tool("tool-1", "shop/execute_refund", '{"order_id":"{{trigger-1.context.payload.order_id}}"}'),
        _ai(
            "ai-1",
            "{{trigger-1.context.payload.amount}} {{global.limit}} "
            "{{tool-1.result.status}} {{tool-1.result.order_id}}",
        ),
        variables=[{"name": "limit", "type": "number", "value": "500", "scope": "global"}],
    )
    compile_graph(parse_graph(raw), registry=build_demo_registry())


def test_undeclared_global_rejected_at_compile():
    message = _compile_error(_chain(_trigger(), _ai("ai-1", "{{global.unknown}}")))
    assert "REF_NODE_NOT_FOUND" in message


def test_missing_node_rejected_at_compile():
    message = _compile_error(_chain(_trigger(), _ai("ai-1", "{{ghost-1.result.x}}")))
    assert "REF_NODE_NOT_FOUND" in message


def test_non_upstream_ref_rejected_at_compile():
    raw = _chain(
        _trigger(),
        _ai("ai-a", "a"),
        _ai("ai-b", "{{ai-a.decision}}"),
        edges=[
            {"id": "e1", "source": "trigger-1", "target": "ai-a"},
            {"id": "e2", "source": "trigger-1", "target": "ai-b"},
        ],
    )
    message = _compile_error(raw)
    assert "REF_NOT_IN_SCOPE" in message


def test_tool_deep_path_checked_against_output_schema():
    raw = _chain(
        _trigger(),
        _tool("tool-1", "shop/execute_refund"),
        _ai("ai-1", "{{tool-1.result.refunded}}"),
    )
    message = _compile_error(raw)
    assert "REF_PATH_NOT_FOUND" in message


def test_open_schema_deep_path_passes():
    # http result.body 为空 schema（{}），深层任意放行
    raw = _chain(
        _trigger(),
        _tool("tool-1", "http/request"),
        _ai("ai-1", "{{tool-1.result.body.data[0].x}}"),
    )
    compile_graph(parse_graph(raw), registry=build_demo_registry())


def test_trigger_context_unknown_key_rejected():
    message = _compile_error(_chain(_trigger(), _ai("ai-1", "{{trigger-1.context.secret}}")))
    assert "REF_PATH_NOT_FOUND" in message


def test_loop_index_visible_in_own_condition_and_body_but_not_after_exit():
    base_nodes = [
        _trigger(),
        {
            "id": "loop-1",
            "type": "loop",
            "name": "循环",
            "config": {
                "mode": "while",
                "continueExpression": "{{loop-1.index}} < 3",
                "maxIterations": 10,
                "bodyTarget": "tool-body",
                "exitTarget": "tool-exit",
            },
        },
        _tool("tool-body", "shop/list_pending_refunds", '{"note":"{{loop-1.index}}"}'),
        _tool("tool-exit", "message/send"),
    ]
    base_edges = [
        {"id": "e1", "source": "trigger-1", "target": "loop-1"},
        {"id": "e2", "source": "loop-1", "target": "tool-body"},
        {"id": "e3", "source": "tool-body", "target": "loop-1"},
        {"id": "e4", "source": "loop-1", "target": "tool-exit"},
    ]
    compile_graph(
        parse_graph(_chain(*base_nodes, edges=base_edges)),
        registry=build_demo_registry(),
    )

    after = _ai("ai-after", "{{loop-1.index}}")
    raw = _chain(
        *base_nodes,
        after,
        edges=[*base_edges, {"id": "e5", "source": "tool-exit", "target": "ai-after"}],
    )
    message = _compile_error(raw)
    assert "REF_NOT_IN_SCOPE" in message


def test_parallel_dynamic_result_and_subgraph_outputs_deep_pass():
    raw = _chain(
        _trigger(),
        {"id": "par-1", "type": "parallel", "name": "并行",
         "config": {"joinStrategy": "all_success", "branches": [
             {"label": "b1", "target": "tool-b1"}, {"label": "b2", "target": "tool-b2"}],
                    "joinTarget": "ai-1"}},
        _tool("tool-b1", "message/send"),
        _tool("tool-b2", "message/send"),
        _ai("ai-1", "{{par-1.result.tool-b1.x}} {{par-1.status}}"),
        edges=[
            {"id": "e0", "source": "trigger-1", "target": "par-1"},
            {"id": "e1", "source": "par-1", "target": "tool-b1"},
            {"id": "e2", "source": "par-1", "target": "tool-b2"},
            {"id": "e3", "source": "tool-b1", "target": "ai-1"},
            {"id": "e4", "source": "tool-b2", "target": "ai-1"},
        ],
    )
    compile_graph(parse_graph(raw), registry=build_demo_registry())

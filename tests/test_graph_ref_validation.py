"""编译期 L2 模板引用复查（04 §6.5，I20）：规则与前端 lib/scope.ts 同构。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph, validate_graph_report
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


def _parallel_raw(b1_params: str = "{}", ai_template: str = "{{par-1.result.tool-b1.x}}") -> dict:
    return _chain(
        _trigger(),
        {"id": "par-1", "type": "parallel", "name": "并行",
         "config": {"joinStrategy": "all_success", "branches": [
             {"label": "b1", "target": "tool-b1"}, {"label": "b2", "target": "tool-b2"}],
                    "joinTarget": "ai-1"}},
        _tool("tool-b1", "message/send", b1_params),
        _tool("tool-b2", "message/send"),
        _ai("ai-1", ai_template),
        edges=[
            {"id": "e0", "source": "trigger-1", "target": "par-1"},
            {"id": "e1", "source": "par-1", "target": "tool-b1"},
            {"id": "e2", "source": "par-1", "target": "tool-b2"},
            {"id": "e3", "source": "tool-b1", "target": "ai-1"},
            {"id": "e4", "source": "tool-b2", "target": "ai-1"},
        ],
    )


def test_parallel_result_visible_after_join_but_not_inside_branch():
    # 汇聚点 ai-1 引用合法入口的深层路径：通过
    compile_graph(parse_graph(_parallel_raw()), registry=build_demo_registry())
    # 分支区域内（汇聚前）节点引用 par-1.result.*：REF_NOT_IN_SCOPE
    err = _compile_error(_parallel_raw(b1_params='{"x":"{{par-1.result.tool-b2.y}}"}'))
    assert "REF_NOT_IN_SCOPE" in err and "汇聚" in err
    # 汇聚点引用不存在的分支入口：REF_PATH_NOT_FOUND
    err2 = _compile_error(_parallel_raw(ai_template="{{par-1.result.nope.z}}"))
    assert "REF_PATH_NOT_FOUND" in err2 and "入口" in err2


def _subgraph_parent(template: str) -> dict:
    return _chain(
        _trigger(),
        {"id": "sub-1", "type": "subgraph", "name": "子流程",
         "config": {"graphId": "graph-child", "inputs": {}}},
        _ai("ai-1", template),
    )


def test_subgraph_outputs_inner_node_expansion_d30_b2():
    index = {"sub-1": {"child-a", "child-b"}}
    # 解析到子图结构：合法内部节点 id 的深层路径放行
    msgs, _, _codes, _params = validate_graph_report(
        parse_graph(_subgraph_parent("{{sub-1.outputs.child-a.x}}")),
        {}, check_refs=True, subgraph_index=index,
    )
    assert not any("子图输出中不存在" in m for m in msgs)
    # outputs.<不存在的内部节点 id>：REF_PATH_NOT_FOUND
    msgs2, _, _codes, _params = validate_graph_report(
        parse_graph(_subgraph_parent("{{sub-1.outputs.ghost.x}}")),
        {}, check_refs=True, subgraph_index=index,
    )
    assert any("REF_PATH_NOT_FOUND" in m and "子图输出中不存在" in m for m in msgs2)
    # 解析不到子图（无 index）：降级仅放行 outputs 根，不报路径错
    msgs3, _, _codes, _params = validate_graph_report(
        parse_graph(_subgraph_parent("{{sub-1.outputs.ghost.x}}")),
        {}, check_refs=True,
    )
    assert not any("子图输出中不存在" in m for m in msgs3)
    # outputs 根本身始终放行
    msgs4, _, _codes, _params = validate_graph_report(
        parse_graph(_subgraph_parent("{{sub-1.outputs}}")),
        {}, check_refs=True, subgraph_index=index,
    )
    assert not any("子图输出中不存在" in m for m in msgs4)


def _loop_graph(continue_expression: str, body_params: str = "{}") -> dict:
    nodes = [
        _trigger(),
        {
            "id": "loop-1",
            "type": "loop",
            "name": "循环",
            "config": {
                "mode": "while",
                "continueExpression": continue_expression,
                "maxIterations": 10,
                "bodyTarget": "tool-body",
                "exitTarget": "tool-exit",
            },
        },
        _tool("tool-body", "shop/list_pending_refunds", body_params),
        _tool("tool-exit", "message/send"),
    ]
    edges = [
        {"id": "e1", "source": "trigger-1", "target": "loop-1"},
        {"id": "e2", "source": "loop-1", "target": "tool-body"},
        {"id": "e3", "source": "tool-body", "target": "loop-1"},
        {"id": "e4", "source": "loop-1", "target": "tool-exit"},
    ]
    return _chain(*nodes, edges=edges)


def test_data_dependency_cycle_loop_condition_referencing_body_rejected():
    # loop 条件引用体内节点输出，体内节点又引用 loop.index → 数据环
    # （拓扑回边合法、被 GRAPH_ILLEGAL_CYCLE 豁免；首轮条件求值时体内尚无输出）。
    raw = _loop_graph(
        "{{tool-body.result}}",
        '{"idx":"{{loop-1.index}}"}',
    )
    message = _compile_error(raw)
    assert "GRAPH_DATA_CYCLE" in message
    assert "loop-1" in message and "tool-body" in message


def test_loop_self_index_reference_is_not_a_data_cycle():
    # 合法基线：条件只引用自身 index、体内引用 loop.index，不构成数据环。
    raw = _loop_graph("{{loop-1.index}} < 3", '{"idx":"{{loop-1.index}}"}')
    compile_graph(parse_graph(raw), registry=build_demo_registry())

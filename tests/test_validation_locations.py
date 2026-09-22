"""U38：编译 422 locations 稀疏侧车（docs/03 422 契约、docs/06 §6.13）。

覆盖：侧车形状、index 与 detail 下标对齐、稀疏（只含有可定位错误）、
图级错误（version/空图/连线/重复 id/全局变量）无条目、字段 pointer 映射、
L2 模板引用挂点、子图递归归属顶层节点 id、API 422 透传。
"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import (
    GraphDSL,
    GraphValidationError,
    parse_graph,
    validate_graph_report,
)
from atlas.graph.loader import compile_graph
from tests.test_api_graphs import client


def _node(node_id, kind, name="n", **config):
    return {"id": node_id, "type": kind, "name": name, "config": config}


def _raw(nodes, edges=None, variables=None, version=1):
    return {
        "version": version,
        "variables": variables or [],
        "nodes": nodes,
        "edges": edges or [],
    }


def _locations_for(raw, **kwargs):
    graph = GraphDSL.model_validate(raw)
    messages, locations = validate_graph_report(graph, **kwargs)
    return messages, locations


def _assert_index_aligned(messages, locations):
    """侧车形状与下标对齐：keys 固定、index 严格递增、回指自身文案。"""
    seen_indices: list[int] = []
    for location in locations:
        assert set(location) <= {"index", "nodeId", "pointer"}
        assert "index" in location
        seen_indices.append(location["index"])
        assert 0 <= location["index"] < len(messages)
    assert seen_indices == sorted(seen_indices)
    assert len(seen_indices) == len(set(seen_indices))


def test_graph_level_errors_have_no_locations():
    # 错误版本 + 空图：全部图级，侧车为空
    messages, locations = _locations_for(_raw([], version=2))
    assert messages
    assert locations == []

    raw = _raw(
        [
            _node("t1", "trigger", triggerType="manual"),
            _node("t1", "wait", waitType="duration", durationSeconds=5),
        ],
        edges=[{"id": "e1", "source": "t1", "target": "ghost"}],
        variables=[
            {"name": "g", "type": "string", "value": "1", "scope": "global"},
            {"name": "g", "type": "string", "value": "2", "scope": "global"},
        ],
    )
    graph = GraphDSL.model_validate(raw)
    messages, locations = validate_graph_report(graph)
    joined = "\n".join(messages)
    assert "节点 id 重复" in joined
    assert "target 节点不存在" in joined
    assert "全局变量名重复" in joined
    _assert_index_aligned(messages, locations)
    # 重复 id / 连线错误 / 全局变量错误均为图级：无条目指向它们
    for location in locations:
        message = messages[location["index"]]
        assert "节点 id 重复" not in message
        assert "节点不存在" not in message
        assert "全局变量名重复" not in message


def test_node_field_errors_carry_node_id_and_pointer():
    raw = _raw(
        [
            _node("trig", "trigger", triggerType="schedule", cron=""),
            _node("ai-1", "ai_decision", promptTemplate=""),
            _node("tool-1", "tool_call", tool=""),
            _node("wait-1", "wait", name="", waitType="until", durationSeconds=5),
        ],
    )
    graph = GraphDSL.model_validate(raw)
    messages, locations = validate_graph_report(graph)
    _assert_index_aligned(messages, locations)

    def find(node_id, pointer):
        return [
            location
            for location in locations
            if location.get("nodeId") == node_id and location.get("pointer") == pointer
        ]

    assert find("trig", "/cron")
    assert find("ai-1", "/promptTemplate")
    assert find("tool-1", "/tool")
    assert find("wait-1", "/waitType")

    # 名称必填只有 nodeId，无 pointer
    name_locs = [
        location
        for location in locations
        if messages[location["index"]] == "节点 wait-1 名称必填"
    ]
    assert name_locs == [{"index": name_locs[0]["index"], "nodeId": "wait-1"}]


def test_branch_and_loop_pointers_with_escaped_tokens():
    raw = _raw(
        [
            _node("t1", "trigger", triggerType="manual"),
            _node(
                "cond-1",
                "condition",
                branches=[],
                defaultTarget="",
            ),
            _node(
                "loop-1",
                "loop",
                mode="while",
                continueExpression="",
                maxIterations=10,
                bodyTarget="",
                exitTarget="",
            ),
            _node(
                "par-1",
                "parallel",
                joinStrategy="bad",
                branches=[{"label": "A", "target": "x"}],
                joinTarget="",
            ),
            _node(
                "human-1",
                "human_approval",
                summary="",
                timeoutSeconds=300,
                onTimeout="reject",
                approvedTarget="",
                rejectedTarget="",
            ),
            _node(
                "sub-1",
                "subgraph",
                graphId="g-x",
                inputs={"a/b": "   ", "ok": "{{t1.context.payload.id}}"},
            ),
        ],
    )
    graph = GraphDSL.model_validate(raw)
    messages, locations = validate_graph_report(graph)
    _assert_index_aligned(messages, locations)
    pointers = {(loc.get("nodeId"), loc.get("pointer")) for loc in locations}
    assert ("cond-1", "/branches") in pointers
    assert ("cond-1", "/defaultTarget") in pointers
    assert ("loop-1", "/continueExpression") in pointers
    assert ("loop-1", "/bodyTarget") in pointers
    assert ("par-1", "/joinStrategy") in pointers
    assert ("human-1", "/summary") in pointers
    assert ("sub-1", "/graphId") not in pointers  # graphId 有值
    # RFC6901 转义：a/b → a~1b
    assert ("sub-1", "/inputs/a~1b") in pointers
    assert ("sub-1", "/inputs/ok") not in pointers


def test_topology_errors_are_node_id_only():
    # condition 分支配置合法但缺出边：拓扑错误 nodeId 有、pointer 无
    raw = _raw(
        [
            _node("t1", "trigger", triggerType="manual"),
            _node(
                "cond-1",
                "condition",
                branches=[{"label": "A", "expression": "true", "target": "w1"}],
                defaultTarget="w2",
            ),
            _node("w1", "wait", waitType="duration", durationSeconds=5),
            _node("w2", "wait", waitType="duration", durationSeconds=5),
        ],
        edges=[],
    )
    graph = GraphDSL.model_validate(raw)
    messages, locations = validate_graph_report(graph)
    condition_topo = [
        location
        for location in locations
        if location.get("nodeId") == "cond-1"
        and messages[location["index"]].startswith("条件节点 cond-1")
    ]
    assert condition_topo
    assert all("pointer" not in location for location in condition_topo)


def test_l2_template_refs_attach_field_pointer_at_compile_check():
    raw = _raw(
        [
            _node("t1", "trigger", triggerType="manual"),
            _node("ai-1", "ai_decision", promptTemplate="引用 {{ghost-1.x}}"),
        ],
        edges=[{"id": "e1", "source": "t1", "target": "ai-1"}],
    )
    messages, locations = _locations_for(raw, check_refs=True)
    assert any("REF_NODE_NOT_FOUND" in message for message in messages)
    hits = [
        location
        for location in locations
        if "REF_NODE_NOT_FOUND" in messages[location["index"]]
    ]
    assert hits == [{"index": hits[0]["index"], "nodeId": "ai-1", "pointer": "/promptTemplate"}]

    # 子图入参模板挂 /inputs/<key>
    raw2 = _raw(
        [
            _node("t1", "trigger", triggerType="manual"),
            _node("sub-1", "subgraph", graphId="g1", inputs={"order_id": "{{ghost.x}}"}),
        ],
        edges=[{"id": "e1", "source": "t1", "target": "sub-1"}],
    )
    messages2, locations2 = _locations_for(raw2, check_refs=True)
    assert any("REF_NODE_NOT_FOUND" in m for m in messages2)
    assert any(
        loc.get("nodeId") == "sub-1" and loc.get("pointer") == "/inputs/order_id"
        for loc in locations2
    )


def test_parse_graph_raises_with_locations():
    raw = _raw([_node("ai-1", "ai_decision", promptTemplate="")])
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert exc.value.locations
    _assert_index_aligned(exc.value.errors, exc.value.locations)
    assert exc.value.locations[0]["nodeId"] == "ai-1"
    assert exc.value.locations[0]["pointer"] == "/promptTemplate"


def _parent_subgraph_graph(child_id):
    return parse_graph(
        _raw(
            [
                _node("trigger-1", "trigger", triggerType="manual"),
                _node(
                    "subgraph-1",
                    "subgraph",
                    name="子流程",
                    graphId=child_id,
                    inputs={"order_id": "{{trigger-1.context.payload.order_id}}"},
                ),
                _node("tool-after", "tool_call", tool="op-after"),
            ],
            edges=[
                {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
            ],
        )
    )


def test_compile_subgraph_locations_attribute_top_level_node_id():
    # 引用不存在：nodeId-only，无 pointer
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(
            _parent_subgraph_graph("graph-missing"),
            graph_id="graph-parent",
            graph_resolver={}.get,
        )
    messages, locations = exc.value.errors, exc.value.locations
    _assert_index_aligned(messages, locations)
    missing = [
        location
        for location in locations
        if "引用的子图不存在：graph-missing" in messages[location["index"]]
    ]
    assert missing == [{"index": missing[0]["index"], "nodeId": "subgraph-1"}]

    # 子图深层子错误：v1 挂顶层父节点 id，不带 pointer
    bad_child = GraphDSL.model_validate(
        _raw(
            [
                _node("child-trigger", "trigger", triggerType="manual"),
                _node("child-wait", "wait", waitType="duration", durationSeconds=2),
            ],
            edges=[],
        )
    )
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(
            _parent_subgraph_graph("graph-bad-child"),
            graph_id="graph-parent",
            graph_resolver={"graph-bad-child": bad_child}.get,
        )
    messages, locations = exc.value.errors, exc.value.locations
    _assert_index_aligned(messages, locations)
    wrapped = [
        location
        for location in locations
        if messages[location["index"]].startswith("子图 graph-bad-child：")
    ]
    assert wrapped
    assert all(location.get("nodeId") == "subgraph-1" for location in wrapped)
    assert all("pointer" not in location for location in wrapped)


def test_compile_locations_index_spans_graph_and_subgraph_sections():
    # 父图自身字段错误 + 子图缺失：偏移拼接后 index 仍严格对齐 detail
    parent = parse_graph(
        _raw(
            [
                _node("trigger-1", "trigger", triggerType="manual"),
                _node("subgraph-1", "subgraph", name="子流程", graphId="graph-missing"),
                _node("tool-after", "tool_call", tool="op-after"),
            ],
            edges=[
                {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
            ],
        )
    )
    # subgraph-1 单出边已满足结构；手工补一个父图字段错误：清空 graphId 由 loader 侧报错
    parent.nodes[1].config = {"graphId": "", "inputs": {}}
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(parent, graph_id="graph-parent")
    messages, locations = exc.value.errors, exc.value.locations
    _assert_index_aligned(messages, locations)
    missing_ref = [
        location
        for location in locations
        if "必须选择引用的已保存子图" in messages[location["index"]]
    ]
    # 根图缺 graphId 带 /graphId pointer
    assert missing_ref[0]["pointer"] == "/graphId"
    assert missing_ref[0]["nodeId"] == "subgraph-1"


def test_api_422_emits_sparse_locations_sidecar():
    bad = {
        "version": 1,
        "variables": [],
        "nodes": [
            _node("trigger-1", "trigger", triggerType="webhook", webhookUrl="/hooks/x"),
            _node("ai-1", "ai_decision", promptTemplate=""),
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "ghost"}],
    }
    response = client.post("/api/graphs", json=bad)
    assert response.status_code == 422
    body = response.json()
    detail = body["detail"]
    locations = body["locations"]
    assert isinstance(detail, list) and locations
    _assert_index_aligned(detail, locations)
    # 可定位：空提示词
    assert any(
        location.get("nodeId") == "ai-1" and location.get("pointer") == "/promptTemplate"
        for location in locations
    )
    # 图级连线错误不出条目
    assert all("ghost" not in detail[location["index"]] for location in locations)


def test_api_422_omits_locations_when_nothing_locatable():
    response = client.post("/api/graphs", json=_raw([], version=2))
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert "locations" not in body

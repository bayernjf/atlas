"""DSL → LangGraph 编译与运行测试（docs/08 §7.1 W7-W8）。"""

from __future__ import annotations

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import compile_graph, interpolate, resolve_path, run_graph


def _sample_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "company_name", "type": "string", "value": "Atlas", "scope": "global"},
                {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"},
            ],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"}},
                {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
                 "config": {"promptTemplate": "公司 {{global.company_name}} 限额 {{global.approval_limit}} 缺失 {{global.missing}}",
                            "confidenceThreshold": 0.6, "model": "demo"}},
                {"id": "tool_call-1", "type": "tool_call", "name": "工具",
                 "config": {"tool": "web-playwright/click",
                            "params": "依据 {{ai_decision-1.decision}} 执行"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
            ],
        }
    )


def test_interpolate_resolves_dotted_paths_and_preserves_missing():
    assert interpolate("公司 {{global.company_name}}", {"global": {"company_name": "Atlas"}}) == "公司 Atlas"
    template = "限额 {{global.missing}} 与 {{order.items[0].price}}"
    context = {"order": {"items": [{"price": 299}]}}
    assert interpolate(template, context) == "限额 {{global.missing}} 与 299"


def test_resolve_path_bracket_and_missing_segments():
    assert resolve_path("order.items[1].price", {"order": {"items": [{"price": 1}, {"price": 2}]}}) == 2
    assert resolve_path("order.items[9].price", {"order": {"items": []}}) is None
    assert resolve_path("x.y", {"x": 1}) is None


def test_compile_produces_graph_and_runs_in_edge_order():
    result = run_graph(_sample_graph())
    assert result["status"] == "completed"
    assert list(result["outputs"].keys()) == ["trigger-1", "ai_decision-1", "tool_call-1"]
    assert result["outputs"]["trigger-1"]["context"]["webhookUrl"] == "/hooks/approval"
    decision = result["outputs"]["ai_decision-1"]
    assert decision["decision"]["action"] == "request_human_approval"
    assert decision["decision"]["source"] == "rule"
    assert "公司 Atlas 限额 500 缺失 {{global.missing}}" == decision["prompt_rendered"]
    # 未在 Demo 注册表中的适配器 → 结构化失败，不抛异常
    tool_output = result["outputs"]["tool_call-1"]["result"]
    assert tool_output["status"] == "FAILED"
    assert "web-playwright" in tool_output["error"]
    assert result["trace"][0].startswith("trigger-1")


def test_compiled_graph_has_start_and_end_wiring():
    compiled = compile_graph(_sample_graph())
    node_ids = set(compiled.get_graph().nodes)
    assert {"trigger-1", "ai_decision-1", "tool_call-1"}.issubset(node_ids)


def test_run_with_runtime_inputs_overrides_global():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [{"name": "limit", "type": "number", "value": "100", "scope": "global"}],
            "nodes": [
                {"id": "ai_decision-1", "type": "ai_decision", "name": "d",
                 "config": {"promptTemplate": "限额 {{global.limit}}"}},
            ],
            "edges": [],
        }
    )
    result = run_graph(graph, inputs={"limit": "999"})
    assert result["outputs"]["ai_decision-1"]["prompt_rendered"] == "限额 999"


def _condition_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "退款单进入",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "condition-1", "type": "condition", "name": "金额路由",
                 "config": {
                     "branches": [
                         {"label": "大额",
                          "expression": "{{trigger-1.context.payload.amount}} > 1000",
                          "target": "tool-human"},
                     ],
                     "defaultTarget": "tool-auto",
                 }},
                {"id": "tool-human", "type": "tool_call", "name": "转人工",
                 "config": {"tool": "human-review"}},
                {"id": "tool-auto", "type": "tool_call", "name": "自动退款",
                 "config": {"tool": "auto-refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-human"},
                {"id": "e3", "source": "condition-1", "target": "tool-auto"},
            ],
        }
    )


def test_condition_routes_to_matching_branch_only():
    result = run_graph(_condition_graph(), inputs={"amount": 1500, "order_id": "12346"})
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-human"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "大额"
    assert routed["target"] == "tool-human"
    assert routed["evaluation"][0]["result"] is True
    assert routed["expression_errors"] == []
    assert result["trace"][1] == "condition-1: branch=大额 → tool-human"


def test_condition_falls_back_to_default_branch():
    result = run_graph(_condition_graph(), inputs={"amount": 500, "order_id": "12345"})
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-auto"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "__default__"
    assert routed["target"] == "tool-auto"
    assert routed["evaluation"][0]["result"] is False


def test_condition_runtime_error_fails_safe_to_default():
    result = run_graph(_condition_graph(), inputs={"order_id": "x"})  # 缺 amount
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-auto"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "__default__"
    assert routed["evaluation"][0]["result"] is None
    assert routed["expression_errors"]


def test_condition_stops_at_first_true_branch():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "condition-1", "type": "condition", "name": "c",
                 "config": {
                     "branches": [
                         {"label": "first", "expression": "true", "target": "tool-a"},
                         {"label": "broken", "expression": "{{missing}} > 1", "target": "tool-b"},
                     ],
                     "defaultTarget": "tool-c",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A", "config": {"tool": "a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B", "config": {"tool": "b"}},
                {"id": "tool-c", "type": "tool_call", "name": "C", "config": {"tool": "c"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-a"},
                {"id": "e3", "source": "condition-1", "target": "tool-b"},
                {"id": "e4", "source": "condition-1", "target": "tool-c"},
            ],
        }
    )
    result = run_graph(graph)
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-a"}
    assert result["outputs"]["condition-1"]["branch"] == "first"
    assert result["outputs"]["condition-1"]["expression_errors"] == []

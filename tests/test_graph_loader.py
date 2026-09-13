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

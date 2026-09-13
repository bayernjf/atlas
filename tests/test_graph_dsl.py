"""Graph DSL 解析与静态校验测试（docs/04 §5.2 契约）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph


def make_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发", "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策", "position": {"x": 1, "y": 1},
             "config": {"promptTemplate": "限额 {{global.approval_limit}}", "confidenceThreshold": 0.6},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具", "position": {"x": 2, "y": 2},
             "config": {"tool": "web-playwright/click", "params": ""},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_graph_matches_serializer_shape():
    graph = parse_graph(make_graph())
    assert graph.version == 1
    assert [node.id for node in graph.nodes] == ["trigger-1", "ai_decision-1", "tool_call-1"]
    assert graph.variables[0].name == "approval_limit"
    assert graph.nodes[1].retry.on_error == "stop"


def test_reject_duplicate_node_ids():
    raw = make_graph()
    raw["nodes"][2]["id"] = "trigger-1"
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("节点 id 重复" in error for error in exc.value.errors)


def test_reject_edge_referencing_missing_node():
    raw = make_graph()
    raw["edges"][0]["target"] = "ghost"
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("target 节点不存在" in error for error in exc.value.errors)


def test_reject_unsupported_node_type_and_empty_name():
    raw = make_graph()
    raw["nodes"][1]["type"] = "condition"
    raw["nodes"][1]["name"] = ""
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert len(exc.value.errors) >= 2


def test_reject_missing_required_config_per_kind():
    raw = make_graph()
    raw["nodes"][0]["config"] = {"triggerType": "cron"}
    raw["nodes"][1]["config"] = {"promptTemplate": "   "}
    raw["nodes"][2]["config"] = {"tool": ""}
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "Cron" in messages and "提示词" in messages and "工具" in messages


def test_schedule_trigger_type_uses_same_cron_rule_as_frontend():
    raw = make_graph()
    raw["nodes"][0]["config"] = {"triggerType": "schedule"}
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("Cron" in message for message in exc.value.errors)


def test_reject_unknown_version_and_empty_graph():
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(make_graph(version=2, nodes=[]))
    assert any("版本" in error for error in exc.value.errors)
    assert any("至少需要一个节点" in error for error in exc.value.errors)


def test_reject_self_loop_and_duplicate_variable():
    raw = make_graph()
    raw["edges"].append({"id": "e3", "source": "trigger-1", "target": "trigger-1"})
    raw["variables"].append({"name": "approval_limit", "type": "string", "value": "x"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "自环" in messages and "全局变量名重复" in messages

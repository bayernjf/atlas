"""自然语言 → 流程草稿测试（docs/08 §7.3 验收 6）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import parse_graph, validate_graph
from atlas.llm.nl_generate import generate_graph, validate_param_fills


def test_refund_intent_returns_valid_refund_template():
    graph = generate_graph("帮我做一个电商退款自动审批流程")
    parsed = parse_graph(graph)
    assert validate_graph(parsed) == []
    assert [node.type for node in parsed.nodes] == ["trigger", "ai_decision", "tool_call"]
    assert parsed.nodes[2].config["tool"] == "shop/process_refund"


def test_unknown_intent_without_llm_raises(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    with pytest.raises(ValueError):
        generate_graph("帮我管管日历日程")


def test_llm_prompt_advertises_condition_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "condition" in system
    assert "branches" in system
    assert "defaultTarget" in system


def test_llm_prompt_advertises_loop_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "loop" in system
    assert '"mode":"while"' in system
    assert "continueExpression" in system
    assert "maxIterations" in system
    assert "bodyTarget" in system
    assert "exitTarget" in system
    assert "回到该 loop 节点的回边" in system


def test_llm_prompt_advertises_parallel_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "parallel" in system
    assert "joinStrategy" in system
    assert "all_success" in system
    assert "all_completed" in system
    assert "joinTarget" in system
    assert "2-10 个" in system
    assert "不直连结束" in system
    assert "不得交叉" in system
    assert "不得再嵌套 parallel" in system


def test_llm_prompt_advertises_wait_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/wait" in system
    assert "/subgraph" in system
    assert "/human_approval" in system
    assert "九类" in system
    assert "waitType" in system
    assert "durationSeconds" in system
    assert "1-600 的整数秒" in system
    assert "事件等待不支持" in system
    assert "恰好配置一条出边" in system
    assert "10-3600 的整数秒" in system
    assert "approvedTarget" in system
    assert "rejectedTarget" in system
    assert "恰好两条出边" in system


def test_llm_prompt_advertises_subgraph_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/subgraph" in system
    assert "九类" in system
    assert "graphId" in system
    assert "inputs" in system
    assert "已保存图 id" in system
    assert "不要凭空捏造 id" in system
    assert "恰好配置一条出边" in system


def test_llm_prompt_advertises_registered_tools(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/api/adapters" in system
    assert "http/request" in system
    assert "params 是 JSON 字符串" in system
    assert "database/query" in system
    assert "database/execute" in system
    assert "绑定参数" in system
    assert "message/send" in system
    assert "shop/process_refund" in system


_PROCESS_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "action": {"type": "string", "enum": ["approve_refund", "request_human_approval"]},
        "note": {"type": "string"},
    },
    "required": ["order_id", "action"],
}

_TOOL_SCHEMAS = {"shop/process_refund": _PROCESS_REFUND_SCHEMA, "open/thing": {}}


def _tool_graph(tool: str, params: str) -> dict:
    return {"version": 1, "nodes": [
        {"id": "t1", "type": "tool_call", "config": {"tool": tool, "params": params}}
    ], "edges": []}


def test_param_fills_legal():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":"O-1","action":"approve_refund","note":"ok"}',
    )
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_missing_required():
    warnings = validate_param_fills(_tool_graph("shop/process_refund", '{"order_id":"O-1"}'), _TOOL_SCHEMAS)
    assert len(warnings) == 1
    assert "缺少必填字段" in warnings[0]
    assert "action" in warnings[0]
    assert "t1" in warnings[0]


def test_param_fills_bad_type_and_enum():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":123,"action":"refund_now"}',
    )
    warnings = validate_param_fills(graph, _TOOL_SCHEMAS)
    joined = "; ".join(warnings)
    assert "order_id" in joined and "string" in joined
    assert "action" in joined and "允许范围" in joined


def test_param_fills_template_values_skipped_but_count_as_present():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":"{{trigger-1.context.payload.order_id}}","action":"approve_refund"}',
    )
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_unparseable_params_pass():
    # 裸 {{}} 插值使 params 不是合法 JSON，尽力校验直接放行
    graph = _tool_graph("shop/process_refund", '{"order_id":{{trigger-1.context.payload.id}}}')
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_unknown_tool_and_empty_schema_pass():
    assert validate_param_fills(_tool_graph("ghost/tool", '{}'), _TOOL_SCHEMAS) == []
    assert validate_param_fills(_tool_graph("open/thing", '{"anything": 1}'), _TOOL_SCHEMAS) == []


def test_param_fills_additional_properties_false():
    schemas = {"shop/process_refund": {**_PROCESS_REFUND_SCHEMA, "additionalProperties": False}}
    warnings = validate_param_fills(
        _tool_graph("shop/process_refund", '{"order_id":"O-1","action":"approve_refund","bogus":1}'),
        schemas,
    )
    assert any("未声明字段" in w and "bogus" in w for w in warnings)

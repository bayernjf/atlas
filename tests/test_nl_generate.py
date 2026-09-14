"""自然语言 → 流程草稿测试（docs/08 §7.3 验收 6）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import parse_graph, validate_graph
from atlas.llm.nl_generate import generate_graph


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

    def _fake_completion(*, model, messages, temperature):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "condition" in system
    assert "branches" in system
    assert "defaultTarget" in system

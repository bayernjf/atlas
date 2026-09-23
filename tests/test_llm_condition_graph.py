"""condition LLM 语义分支图级语义（docs/48；13 U303）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.llm.condition_classifier import (
    OfflineConditionClassifier,
)


def _llm_condition_graph(
    *,
    mode: str = "llm",
    branch_a: dict | None = None,
    branch_b: dict | None = None,
    classifier_prompt=None,
):
    branch_a = branch_a or {
        "label": "愤怒投诉",
        "description": "客户表达强烈不满或威胁升级",
        "target": "tool-a",
    }
    branch_b = branch_b or {
        "label": "普通咨询",
        "description": "客户语气平和地询问进度",
        "target": "tool-b",
    }
    config: dict = {"branches": [branch_a, branch_b], "defaultTarget": "tool-default"}
    if mode is not None:
        config["conditionMode"] = mode
    if classifier_prompt is not None:
        config["classifierPrompt"] = classifier_prompt
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "cond-1", "type": "condition", "name": "分流",
             "position": {"x": 2, "y": 0}, "config": config},
            {"id": "tool-a", "type": "tool_call", "name": "A",
             "config": {"tool": "op-a"}},
            {"id": "tool-b", "type": "tool_call", "name": "B",
             "config": {"tool": "op-b"}},
            {"id": "tool-default", "type": "tool_call", "name": "默认",
             "config": {"tool": "op-default"}},
        ],
        "edges": [
            {"id": "e0", "source": "trigger-1", "target": "cond-1"},
            {"id": "e1", "source": "cond-1", "target": "tool-a"},
            {"id": "e2", "source": "cond-1", "target": "tool-b"},
            {"id": "e3", "source": "cond-1", "target": "tool-default"},
        ],
    }


class _FixedClassifier:
    def __init__(self, label: str):
        self.label = label
        self.seen: dict = {}

    def classify(self, *, branches, context_text, instruction):
        self.seen = {"branches": branches, "context_text": context_text,
                     "instruction": instruction}
        return self.label


class _RaisingClassifier:
    def __init__(self, exc: Exception):
        self.exc = exc

    def classify(self, *, branches, context_text, instruction):
        raise self.exc


def test_llm_condition_routes_to_matching_branch():
    classifier = _FixedClassifier("普通咨询")
    result = run_graph(
        parse_graph(_llm_condition_graph()),
        condition_classifier=classifier,
    )
    assert result["status"] == "completed"
    output = result["outputs"]["cond-1"]
    assert output["mode"] == "llm"
    assert output["branch"] == "普通咨询"
    assert output["target"] == "tool-b"
    assert "tool-b" in result["outputs"] and "tool-a" not in result["outputs"]
    assert output["llm_errors"] == []


def test_llm_condition_default_label_routes_to_default():
    classifier = _FixedClassifier("__default__")
    result = run_graph(
        parse_graph(_llm_condition_graph()),
        condition_classifier=classifier,
    )
    output = result["outputs"]["cond-1"]
    assert output["branch"] == "__default__"
    assert output["target"] == "tool-default"
    assert "tool-default" in result["outputs"]


def test_llm_condition_classifier_error_falls_back_to_default():
    classifier = _RaisingClassifier(RuntimeError("rate limited"))
    result = run_graph(
        parse_graph(_llm_condition_graph()),
        condition_classifier=classifier,
    )
    assert result["status"] == "completed"
    output = result["outputs"]["cond-1"]
    assert output["target"] == "tool-default"
    assert "rate limited" in output["llm_errors"][0]


def test_llm_condition_offline_classifier_falls_back():
    result = run_graph(
        parse_graph(_llm_condition_graph()),
        condition_classifier=OfflineConditionClassifier(),
    )
    output = result["outputs"]["cond-1"]
    assert output["target"] == "tool-default"
    assert "LLM 未配置" in output["llm_errors"][0]


def test_llm_condition_serializes_context_and_instruction():
    classifier = _FixedClassifier("愤怒投诉")
    run_graph(
        parse_graph(_llm_condition_graph(classifier_prompt="优先保护客户体验")),
        condition_classifier=classifier,
        inputs={"message": "我要投诉"},
    )
    assert classifier.seen["instruction"] == "优先保护客户体验"
    assert '"trigger-1"' in classifier.seen["context_text"]
    assert len(classifier.seen["branches"]) == 2


def test_llm_condition_truncates_oversized_context():
    classifier = _FixedClassifier("普通咨询")
    huge = {"message": "x" * 20000}
    result = run_graph(
        parse_graph(_llm_condition_graph()),
        condition_classifier=classifier,
        inputs=huge,
    )
    assert result["status"] == "completed"
    assert len(classifier.seen["context_text"]) <= 12020
    assert "<截断>" in classifier.seen["context_text"]


def test_llm_condition_rule_mode_ignores_mode_and_uses_expressions():
    graph = _llm_condition_graph(
        mode="rule",
        branch_a={"label": "大额", "expression": "{{global.amount}} > 100",
                  "target": "tool-a"},
        branch_b={"label": "小额", "expression": "{{global.amount}} <= 100",
                  "target": "tool-b"},
    )
    graph["variables"] = [
        {"name": "amount", "type": "number", "value": "0", "scope": "global"}
    ]
    classifier = _RaisingClassifier(AssertionError("rule mode must not classify"))
    result = run_graph(
        parse_graph(graph),
        condition_classifier=classifier,
        inputs={"amount": 200},
    )
    output = result["outputs"]["cond-1"]
    assert output["branch"] == "大额"
    assert output["target"] == "tool-a"
    assert "mode" not in output


@pytest.mark.parametrize(
    "graph_mutator, fragment",
    [
        (lambda g: _with_mode(g, "semantic"), "conditionMode"),
        (lambda g: _with_branch_field(g, 0, "expression", "true"), "expression"),
        (lambda g: _with_branch_field(g, 0, "description", ""), "description"),
        (lambda g: _with_branch_field(g, 1, "description", "描" * 301), "description"),
        (lambda g: _with_prompt(g, "要" * 501), "classifierPrompt"),
    ],
)
def test_llm_condition_dsl_validation_errors(graph_mutator, fragment):
    raw = graph_mutator(_llm_condition_graph())
    with pytest.raises(Exception) as exc_info:
        parse_graph(raw)
    assert fragment in str(exc_info.value)


def _with_mode(raw, mode):
    raw["nodes"][1]["config"]["conditionMode"] = mode
    return raw


def _with_branch_field(raw, index, key, value):
    raw["nodes"][1]["config"]["branches"][index][key] = value
    return raw


def _with_prompt(raw, value):
    raw["nodes"][1]["config"]["classifierPrompt"] = value
    return raw

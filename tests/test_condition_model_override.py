"""打包 ZP：condition(llm) 节点级 model 覆盖＋structured outputs 强制（docs/08 打包 ZP 立项块；13 U1082 起）。

覆盖：
- classifier 层：per-call model 覆盖构造期默认、空白回退、未传走默认、response_format=json_object 强制；
- Scripted／Offline 两实现签名对齐（model 参数被接受且忽略）；
- loader 层：config.model 透传 classify（缺省走 None）；
- DSL 校验：model 超长（>200）／非字符串 422 码。
"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import run_graph
from atlas.llm.condition_classifier import (
    ConditionClassifyError,
    LiteLLMConditionClassifier,
    OfflineConditionClassifier,
    ScriptedConditionClassifier,
)

_BRANCHES = [
    {"label": "愤怒投诉", "description": "客户表达强烈不满"},
    {"label": "普通咨询", "description": "客户语气平和"},
]


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content

    def __getitem__(self, key: str):
        if key == "choices":
            return [{"message": {"content": self.content}}]
        raise KeyError(key)


def _install_capturing_litellm(monkeypatch, content: str) -> dict:
    captured: dict = {}

    def _completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


def test_node_model_overrides_environment_default(monkeypatch):
    captured = _install_capturing_litellm(monkeypatch, '{"branch": "普通咨询"}')
    LiteLLMConditionClassifier("env-default").classify(
        branches=_BRANCHES, context_text="{}", instruction="", model="node-model"
    )
    assert captured["model"] == "node-model"


def test_blank_node_model_falls_back_to_environment_default(monkeypatch):
    captured = _install_capturing_litellm(monkeypatch, '{"branch": "普通咨询"}')
    LiteLLMConditionClassifier("env-default").classify(
        branches=_BRANCHES, context_text="{}", instruction="", model="   "
    )
    assert captured["model"] == "env-default"


def test_no_model_uses_constructor_default(monkeypatch):
    captured = _install_capturing_litellm(monkeypatch, '{"branch": "普通咨询"}')
    LiteLLMConditionClassifier("env-default").classify(
        branches=_BRANCHES, context_text="{}", instruction=""
    )
    assert captured["model"] == "env-default"


def test_response_format_json_object_forced(monkeypatch):
    captured = _install_capturing_litellm(monkeypatch, '{"branch": "普通咨询"}')
    LiteLLMConditionClassifier("env-default").classify(
        branches=_BRANCHES, context_text="{}", instruction=""
    )
    assert captured["response_format"] == {"type": "json_object"}


def test_scripted_classifier_accepts_and_ignores_model():
    clf = ScriptedConditionClassifier({"cond-1": "普通咨询"})
    assert clf.classify(
        branches=_BRANCHES, context_text="{}", instruction="",
        node_id="cond-1", model="node-model",
    ) == "普通咨询"
    with pytest.raises(ConditionClassifyError):
        clf.classify(
            branches=_BRANCHES, context_text="{}", instruction="",
            node_id="missing", model="x",
        )


def test_offline_classifier_accepts_model_and_still_raises():
    with pytest.raises(ConditionClassifyError):
        OfflineConditionClassifier().classify(
            branches=_BRANCHES, context_text="{}", instruction="", model="x"
        )


def _condition_graph(*, model="sentinel"):
    config: dict = {
        "branches": [
            {"label": "A", "description": "分支A", "target": "tool-a"},
            {"label": "B", "description": "分支B", "target": "tool-b"},
        ],
        "defaultTarget": "tool-default",
        "conditionMode": "llm",
    }
    if model is not None:
        config["model"] = model
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "cond-1", "type": "condition", "name": "分流",
             "position": {"x": 2, "y": 0}, "config": config},
            {"id": "tool-a", "type": "tool_call", "name": "A", "config": {"tool": "op-a"}},
            {"id": "tool-b", "type": "tool_call", "name": "B", "config": {"tool": "op-b"}},
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


class _RecordingClassifier:
    def __init__(self, label: str):
        self.label = label
        self.seen: dict = {}

    def classify(self, *, branches, context_text, instruction, node_id=None, model=None, confidence_threshold=None):
        self.seen = {"model": model}
        return self.label


def test_loader_passes_config_model_to_classify():
    classifier = _RecordingClassifier("A")
    run_graph(parse_graph(_condition_graph(model="sentinel")), condition_classifier=classifier)
    assert classifier.seen["model"] == "sentinel"


def test_loader_passes_none_when_config_model_absent():
    classifier = _RecordingClassifier("A")
    run_graph(parse_graph(_condition_graph(model=None)), condition_classifier=classifier)
    assert classifier.seen["model"] is None


def test_dsl_rejects_model_over_200_chars():
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(_condition_graph(model="x" * 201))
    assert "COND_LLM_MODEL_TOO_LONG" in exc.value.codes
    assert any("model" in m for m in exc.value.errors)


def test_dsl_rejects_non_string_model():
    raw = _condition_graph()
    raw["nodes"][1]["config"]["model"] = 123
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert "COND_LLM_MODEL_NOT_STRING" in exc.value.codes
    assert any("model" in m for m in exc.value.errors)

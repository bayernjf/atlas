"""打包 AE（docs/111）：condition LLM 分支置信度阈值＋上下文字段级脱敏（U1285–U1291）。"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import run_graph
from atlas.llm.condition_classifier import (
    ConditionClassifyError,
    LiteLLMConditionClassifier,
    ScriptedConditionClassifier,
)
from atlas.security.secrets import PlaintextSecretProvider

_BRANCHES = [
    {"label": "A", "description": "分支A"},
    {"label": "B", "description": "分支B"},
]


# ---------- 图与替身 ----------

def _llm_condition_graph(*, threshold=None, variables=None):
    config: dict = {
        "branches": [
            {"label": "A", "description": "分支A", "target": "tool-a"},
            {"label": "B", "description": "分支B", "target": "tool-b"},
        ],
        "defaultTarget": "tool-default",
        "conditionMode": "llm",
    }
    if threshold is not None:
        config["confidenceThreshold"] = threshold
    return {
        "version": 1,
        "variables": variables or [],
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


class _CapturingClassifier:
    """记录传入的 context_text 与阈值，返回固定标签。"""

    def __init__(self, label: str = "A"):
        self.label = label
        self.context_text: str | None = None
        self.calls: list[dict] = []

    def classify(
        self, *, branches, context_text, instruction, node_id=None, model=None,
        confidence_threshold=None,
    ):
        self.context_text = context_text
        self.calls.append({"confidence_threshold": confidence_threshold})
        return self.label


class _LowConfidenceClassifier:
    """模拟 LiteLLM 分类器在低置信度时抛错。"""

    def classify(self, *, branches, context_text, instruction, node_id=None, model=None,
                 confidence_threshold=None):
        raise ConditionClassifyError("置信度 0.3 低于阈值 0.8，路由默认分支")


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content

    def __getitem__(self, key: str):
        if key == "choices":
            return [{"message": {"content": self.content}}]
        raise KeyError(key)


def _install_fake_litellm(monkeypatch, content: str) -> None:
    def _completion(**kwargs):
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)


# ---------- U1285：上下文脱敏 ----------

def test_u1285_env_context_redacted(monkeypatch):
    monkeypatch.setenv("ATLAS_AE_CTX", "env-secret-value-zzz")
    variables = [
        {"name": "API_TOKEN", "type": "string", "value": "ATLAS_AE_CTX",
         "scope": "global", "source": "env"}
    ]
    classifier = _CapturingClassifier()
    run_graph(
        parse_graph(_llm_condition_graph(variables=variables)),
        condition_classifier=classifier,
    )
    assert classifier.context_text is not None
    assert "<redacted:env:ATLAS_AE_CTX>" in classifier.context_text
    assert "env-secret-value-zzz" not in classifier.context_text


def test_u1285_secret_context_redacted():
    variables = [
        {"name": "API_TOKEN", "type": "string", "value": "api_key",
         "scope": "global", "source": "secret"}
    ]
    classifier = _CapturingClassifier()
    run_graph(
        parse_graph(_llm_condition_graph(variables=variables)),
        condition_classifier=classifier,
        secret_provider=PlaintextSecretProvider({"api_key": "sk-live-unique-7713"}),
    )
    assert classifier.context_text is not None
    assert "<redacted:secret:" in classifier.context_text
    assert "sk-live-unique-7713" not in classifier.context_text


# ---------- U1286–U1289：classifier 阈值判定 ----------

def test_u1286_confidence_at_or_above_threshold_passes(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"branch": "A", "confidence": 0.9}')
    label = LiteLLMConditionClassifier("m").classify(
        branches=_BRANCHES, context_text="{}", instruction="",
        confidence_threshold=0.8,
    )
    assert label == "A"


def test_u1287_confidence_below_threshold_raises(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"branch": "A", "confidence": 0.5}')
    with pytest.raises(ConditionClassifyError, match="低于阈值"):
        LiteLLMConditionClassifier("m").classify(
            branches=_BRANCHES, context_text="{}", instruction="",
            confidence_threshold=0.8,
        )


def test_u1287_low_confidence_routes_default_end_to_end():
    result = run_graph(
        parse_graph(_llm_condition_graph(threshold=0.8)),
        condition_classifier=_LowConfidenceClassifier(),
    )
    out = result["outputs"]["cond-1"]
    assert out["branch"] == "__default__"
    assert out["target"] == "tool-default"
    assert out["llm_errors"]


@pytest.mark.parametrize(
    "content",
    [
        '{"branch": "A"}',
        '{"branch": "A", "confidence": "high"}',
        '{"branch": "A", "confidence": 1.5}',
        '{"branch": "A", "confidence": -0.2}',
    ],
)
def test_u1288_missing_or_bad_confidence_raises(monkeypatch, content):
    _install_fake_litellm(monkeypatch, content)
    with pytest.raises(ConditionClassifyError):
        LiteLLMConditionClassifier("m").classify(
            branches=_BRANCHES, context_text="{}", instruction="",
            confidence_threshold=0.8,
        )


def test_u1289_no_threshold_low_confidence_passes(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"branch": "A", "confidence": 0.1}')
    label = LiteLLMConditionClassifier("m").classify(
        branches=_BRANCHES, context_text="{}", instruction="",
        confidence_threshold=None,
    )
    assert label == "A"


def test_loader_passes_threshold_to_classifier():
    classifier = _CapturingClassifier()
    run_graph(
        parse_graph(_llm_condition_graph(threshold=0.75)),
        condition_classifier=classifier,
    )
    assert classifier.calls[0]["confidence_threshold"] == 0.75


# ---------- U1290：Scripted 回放不受阈值影响 ----------

def test_u1290_scripted_ignores_threshold():
    result = run_graph(
        parse_graph(_llm_condition_graph(threshold=0.9)),
        condition_classifier=ScriptedConditionClassifier({"cond-1": "A"}),
    )
    out = result["outputs"]["cond-1"]
    assert out["branch"] == "A"
    assert out["target"] == "tool-a"


# ---------- U1291：DSL 编译期阈值校验 ----------

@pytest.mark.parametrize("bad", [1.5, -0.1, "0.8", True])
def test_u1291_dsl_rejects_bad_threshold(bad):
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(_llm_condition_graph(threshold=bad))
    assert "COND_LLM_THRESHOLD_INVALID" in exc.value.codes

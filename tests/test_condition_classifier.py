"""condition 语义分支分类器测试（D14 v1，docs/48；LiteLLM 标签解析与 fail-safe）。"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.llm.condition_classifier import (
    DEFAULT_BRANCH,
    LiteLLMConditionClassifier,
    OfflineConditionClassifier,
    ConditionClassifyError,
    get_condition_classifier,
)

_BRANCHES = [
    {"label": "愤怒投诉", "description": "强烈不满或威胁升级"},
    {"label": "普通咨询", "description": "语气平和地询问进度"},
]


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def __getitem__(self, key):
        return {"choices": [{"message": {"content": self._content}}]}[key]


def _install_fake_litellm(monkeypatch, content: str):
    fake = types.ModuleType("litellm")
    fake.completion = lambda **kwargs: _FakeResponse(content)
    monkeypatch.setitem(sys.modules, "litellm", fake)


def test_offline_classifier_always_raises():
    with pytest.raises(ConditionClassifyError, match="LLM 未配置"):
        OfflineConditionClassifier().classify(
            branches=_BRANCHES, context_text="{}", instruction=""
        )


def test_litellm_classifier_returns_matching_label(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"branch": "普通咨询"}')
    label = LiteLLMConditionClassifier("gpt-test").classify(
        branches=_BRANCHES, context_text='{"message": "请问发货了吗"}', instruction=""
    )
    assert label == "普通咨询"


def test_litellm_classifier_extracts_json_from_prose(monkeypatch):
    _install_fake_litellm(
        monkeypatch, '选择结果如下：\n{"branch": "愤怒投诉"}\n以上。'
    )
    label = LiteLLMConditionClassifier("gpt-test").classify(
        branches=_BRANCHES, context_text="{}", instruction=""
    )
    assert label == "愤怒投诉"


def test_litellm_classifier_accepts_default_label(monkeypatch):
    _install_fake_litellm(monkeypatch, f'{{"branch": "{DEFAULT_BRANCH}"}}')
    label = LiteLLMConditionClassifier("gpt-test").classify(
        branches=_BRANCHES, context_text="{}", instruction=""
    )
    assert label == DEFAULT_BRANCH


def test_litellm_classifier_raises_on_unparseable_output(monkeypatch):
    _install_fake_litellm(monkeypatch, "我无法判断")
    with pytest.raises(ConditionClassifyError, match="无法解析"):
        LiteLLMConditionClassifier("gpt-test").classify(
            branches=_BRANCHES, context_text="{}", instruction=""
        )


def test_litellm_classifier_raises_on_missing_branch_key(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"choice": "普通咨询"}')
    with pytest.raises(ConditionClassifyError, match="无法解析"):
        LiteLLMConditionClassifier("gpt-test").classify(
            branches=_BRANCHES, context_text="{}", instruction=""
        )


def test_litellm_classifier_raises_on_unknown_label(monkeypatch):
    _install_fake_litellm(monkeypatch, '{"branch": "紧急退款"}')
    with pytest.raises(ConditionClassifyError, match="未知分支标签"):
        LiteLLMConditionClassifier("gpt-test").classify(
            branches=_BRANCHES, context_text="{}", instruction=""
        )


def test_litellm_classifier_passes_instruction_and_context(monkeypatch):
    seen: dict = {}

    fake = types.ModuleType("litellm")

    def _completion(**kwargs):
        seen.update(kwargs)
        return _FakeResponse('{"branch": "普通咨询"}')

    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    LiteLLMConditionClassifier("gpt-test").classify(
        branches=_BRANCHES,
        context_text='{"k": "v"}',
        instruction="优先保护客户体验",
    )
    assert seen["temperature"] == 0
    user_content = seen["messages"][1]["content"]
    assert "优先保护客户体验" in user_content
    assert '{"k": "v"}' in user_content
    assert "愤怒投诉" in user_content and "普通咨询" in user_content


def test_get_classifier_returns_offline_without_model(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    assert isinstance(get_condition_classifier(), OfflineConditionClassifier)


def test_get_classifier_returns_litellm_with_model(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "gpt-test")
    assert isinstance(get_condition_classifier(), LiteLLMConditionClassifier)

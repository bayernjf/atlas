# -*- coding: utf-8 -*-
"""打包 AK（docs/122 D-2）：`memory/auto_extract` 与 `llm/structured.extract_memories`。

这条切片的重点不是"能不能调 LLM"，而是**失败形状**：
"没接模型"、"模型这次失败"、"提取到但都低于门槛"是三种不同答案，
都必须能在产出里读出来——把前两种都写成 `extracted: 0` 就是 docs/63 以来一直在修的静默降级。
假 litellm 只用来喂形状，不用于证明真模型语义（那部分见 docs/13 打包 AB 的同族判据）。
"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.llm import structured
from atlas.llm.structured import LLMStructuredUnavailable, extract_memories
from atlas.memory.adapter import MemoryHarnessAdapter
from atlas.memory.items import MemoryStore


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def __getitem__(self, key):
        return {"choices": [{"message": {"content": self._content}}]}[key]


def _fake_litellm(monkeypatch, content: str) -> dict:
    captured: dict = {}

    def _completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


# --------------------------------------------------------------------------- #
# LLM 层：形状解析与"根本没模型"
# --------------------------------------------------------------------------- #


def test_extract_memories_parses_both_buckets_and_sends_the_text(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/test-model")
    captured = _fake_litellm(
        monkeypatch,
        '{"facts": [{"content": "订单 o-1 已退款 88 元", "confidence": 0.93}],'
        ' "preferences": [{"content": "客户偏好顺丰", "confidence": 0.81}]}',
    )
    result = extract_memories(text="客服对话原文")
    assert result["facts"] == [{"content": "订单 o-1 已退款 88 元", "confidence": 0.93}]
    assert result["preferences"] == [{"content": "客户偏好顺丰", "confidence": 0.81}]
    assert "客服对话原文" in captured["messages"][-1]["content"], "文本没进模型＝整条链是假的"


def test_extract_memories_bad_json_is_empty_not_a_crash(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/test-model")
    _fake_litellm(monkeypatch, "抱歉，我没法回答")
    result = extract_memories(text="x")
    assert result["facts"] == [] and result["preferences"] == []


def test_extract_memories_clamps_garbage_confidence_to_zero(monkeypatch):
    """缺/坏置信度按 0 处理＝宁缺不滥：它必须被门槛挡住，而不是被当成"模型没说所以通过"。"""
    monkeypatch.setenv("LITELLM_MODEL", "openai/test-model")
    _fake_litellm(
        monkeypatch,
        '{"facts": [{"content": "没有置信度"}, {"content": "坏值", "confidence": "abc"},'
        ' {"content": "越界", "confidence": 7}, {"content": "", "confidence": 1.0}]}',
    )
    facts = extract_memories(text="x")["facts"]
    assert [item["content"] for item in facts] == ["没有置信度", "坏值", "越界"]
    assert [item["confidence"] for item in facts] == [0.0, 0.0, 1.0]


def test_extract_memories_without_any_model_raises_explicitly(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    with pytest.raises(LLMStructuredUnavailable) as exc:
        extract_memories(text="x")
    assert exc.value.code == "LLM_STRUCTURED_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# 工具层：门槛、幂等、以及三种失败形状
# --------------------------------------------------------------------------- #


@pytest.fixture()
def repo():
    return MemoryStore()


@pytest.fixture()
def adapter(repo):
    return MemoryHarnessAdapter(repo=repo, granted_permissions={Permission.READ, Permission.WRITE})


def _run(adapter, params):
    return adapter.execute(ActionRequest("auto_extract", params))


def test_u1332_only_candidates_above_the_threshold_are_written(adapter, repo, monkeypatch):
    monkeypatch.setattr(
        "atlas.memory.adapter.extract_memories",
        lambda **kwargs: {
            "facts": [{"content": "订单 o-1 已退款", "confidence": 0.93},
                      {"content": "疑似退款，未确认", "confidence": 0.5}],
            "preferences": [{"content": "客户偏好极速退款", "confidence": 0.88}],
        },
    )
    result = _run(adapter, {"text": "一段客服对话", "scope": {"user_id": "u-1"}})
    assert result.status == ActionStatus.SUCCESS
    output = result.output
    assert output["extracted"] == 2 and output["dropped_below_threshold"] == 1
    assert output["min_confidence"] == 0.85 and output["skipped"] == ""

    listed = repo.list()
    assert {item["content"] for item in listed} == {"订单 o-1 已退款", "客户偏好极速退款"}
    assert all(item["source"] == "auto_extract" for item in listed), "来源必须可辨（记忆页要显示）"
    assert all(item["scope"] == {"user_id": "u-1"} for item in listed)
    assert all(item["kind"] in ("fact", "preference") for item in listed)


def test_u1332b_rerunning_the_same_text_does_not_stack(adapter, repo, monkeypatch):
    """幂等声明的承重判据：缺省 overwrite 语义让重跑不产生第二条。"""
    monkeypatch.setattr(
        "atlas.memory.adapter.extract_memories",
        lambda **kwargs: {"facts": [{"content": "订单 o-1 已退款", "confidence": 0.95}],
                          "preferences": []},
    )
    _run(adapter, {"text": "同一段"})
    second = _run(adapter, {"text": "同一段"})
    assert second.status == ActionStatus.SUCCESS
    assert len(repo.list()) == 1


def test_u1333_no_model_is_reported_not_disguised_as_an_empty_extraction(adapter, repo, monkeypatch):
    def _unavailable(**kwargs):
        raise LLMStructuredUnavailable("memory_auto_extract", "未配置 LLM 模型")

    monkeypatch.setattr("atlas.memory.adapter.extract_memories", _unavailable)
    result = _run(adapter, {"text": "x"})
    assert result.status == ActionStatus.SUCCESS, "记忆是增强项，不该打断主流程"
    assert result.output["skipped"] == "llm_unavailable"
    assert result.output["extracted"] == 0 and "未配置" in result.output["reason"]
    assert repo.list() == []
    # 与"接了模型但没提取到"必须是两个答案——这条断言就是本批改判的全部理由。
    monkeypatch.setattr(
        "atlas.memory.adapter.extract_memories",
        lambda **kwargs: {"facts": [], "preferences": []},
    )
    nothing = _run(adapter, {"text": "x"}).output
    assert nothing["skipped"] == "" and nothing["extracted"] == 0
    assert nothing["skipped"] != result.output["skipped"]


@pytest.mark.parametrize("params", [
    {"text": ""},
    {"text": "   "},
    {"text": "x", "min_confidence": 1.5},
    {"text": "x", "min_confidence": "high"},
    {"text": "x", "min_confidence": True},
])
def test_invalid_tool_inputs_are_rejected(adapter, params):
    result = _run(adapter, params)
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "MEMORY_INVALID_INPUT"


def test_auto_extract_requires_write_permission(repo):
    read_only = MemoryHarnessAdapter(repo=repo, granted_permissions={Permission.READ})
    result = read_only.execute(ActionRequest("auto_extract", {"text": "x"}))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"


def test_threshold_is_a_parameter_not_a_hardcoded_truth(adapter, repo, monkeypatch):
    """0.85 只是缺省：调低门槛就该多收——否则"可配"是句空话。"""
    monkeypatch.setattr(
        "atlas.memory.adapter.extract_memories",
        lambda **kwargs: {"facts": [{"content": "弱信号", "confidence": 0.6}], "preferences": []},
    )
    assert _run(adapter, {"text": "x"}).output["extracted"] == 0
    loose = _run(adapter, {"text": "x", "min_confidence": 0.5}).output
    assert loose["extracted"] == 1 and loose["min_confidence"] == 0.5
    assert repo.list()[0]["confidence"] == pytest.approx(0.6)


def test_backend_source_set_matches_the_frontend_badge():
    """前端徽标白名单与后端 `MemorySource` 必须同一集合（两边各写一份就会漂）。

    对端判据在 `frontend/src/lib/__tests__/memory.test.ts`（U1343）。
    """
    from typing import get_args

    from atlas.memory.models import MemorySource

    assert set(get_args(MemorySource)) == {"tool", "manual", "run", "auto_extract"}


def test_store_failure_is_reported_as_storage_error_not_a_silent_zero(monkeypatch):
    class _Broken:
        def remember(self, **kwargs):
            raise RuntimeError("pg down")

    adapter = MemoryHarnessAdapter(repo=_Broken(), granted_permissions={Permission.READ, Permission.WRITE})
    monkeypatch.setattr(
        "atlas.memory.adapter.extract_memories",
        lambda **kwargs: {"facts": [{"content": "x", "confidence": 0.99}], "preferences": []},
    )
    result = _run(adapter, {"text": "x"})
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "STORAGE_ERROR"

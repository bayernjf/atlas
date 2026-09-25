"""退款决策客户端测试（06 §9.2 黄金用例；LiteLLM fail-safe）。"""

from __future__ import annotations

import sys
import types

from atlas.llm.decision import (
    AUTO_APPROVE,
    HUMAN_APPROVAL,
    LiteLLMDecisionClient,
    RuleBasedDecisionClient,
)


def test_golden_quality_reason_within_limit_auto_approves():
    decision = RuleBasedDecisionClient().decide_refund(reason="商品破损", amount=299, limit=500)
    assert decision["action"] == AUTO_APPROVE
    assert decision["source"] == "rule"


def test_golden_subjective_reason_goes_human_even_within_limit():
    decision = RuleBasedDecisionClient().decide_refund(reason="不想要了", amount=200, limit=500)
    assert decision["action"] == HUMAN_APPROVAL


def test_golden_quality_reason_over_limit_goes_human():
    decision = RuleBasedDecisionClient().decide_refund(reason="商品有质量瑕疵", amount=899, limit=500)
    assert decision["action"] == HUMAN_APPROVAL


def test_golden_cases_12345_to_12349():
    client = RuleBasedDecisionClient()
    cases = [
        ("12345", "商品破损", 299, AUTO_APPROVE),
        ("12346", "不想要了", 5000, HUMAN_APPROVAL),
        ("12347", "商品有质量瑕疵", 128, AUTO_APPROVE),
        ("12348", "商家错发商品", 460, AUTO_APPROVE),
        ("12349", "尺寸不合适", 899, HUMAN_APPROVAL),
    ]
    for _order_id, reason, amount, expected in cases:
        assert client.decide_refund(reason=reason, amount=amount, limit=500)["action"] == expected


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def __getitem__(self, key):
        return {"choices": [{"message": {"content": self._content}}]}[key]


def _install_fake_litellm(monkeypatch, content: str):
    fake = types.ModuleType("litellm")
    fake.completion = lambda **kwargs: _FakeResponse(content)
    monkeypatch.setitem(sys.modules, "litellm", fake)


def test_litellm_client_parses_json_action(monkeypatch):
    _install_fake_litellm(
        monkeypatch,
        '结果：{"action": "approve_refund", "reason": "破损且限额内", "confidence": 0.9}',
    )
    decision = LiteLLMDecisionClient("gpt-test").decide_refund(reason="商品破损", amount=100, limit=500)
    assert decision["action"] == AUTO_APPROVE
    assert decision["source"] == "llm:gpt-test"


def test_litellm_client_fail_safe_on_unparseable_output(monkeypatch):
    _install_fake_litellm(monkeypatch, "我无法判断")
    decision = LiteLLMDecisionClient("gpt-test").decide_refund(reason="商品破损", amount=100, limit=500)
    assert decision["action"] == HUMAN_APPROVAL
    assert decision["confidence"] == 0.0
# ============================ J-2a 决策模式打印 ============================


def test_get_decision_client_logs_degraded_mode_once(monkeypatch, caplog):
    """J-2a：未配置 LITELLM_MODEL 时首次调用打印降级警告（不再静默），且只打印一次。"""
    import logging

    from atlas.llm import decision as mod

    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.setattr(mod, "_decision_mode_logged", False)
    with caplog.at_level(logging.WARNING, logger="atlas.llm.decision"):
        client = mod.get_decision_client()
        mod.get_decision_client()  # 第二次不重复打印
    assert isinstance(client, RuleBasedDecisionClient)
    assert any("规则决策降级模式" in r.message for r in caplog.records)
    assert sum(1 for r in caplog.records if "规则决策降级模式" in r.message) == 1


def test_get_decision_client_logs_llm_mode_once(monkeypatch, caplog):
    """J-2a：配置 LITELLM_MODEL 时首次调用打印 LiteLLM 模式。"""
    import logging

    from atlas.llm import decision as mod

    monkeypatch.setenv("LITELLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(mod, "_decision_mode_logged", False)
    with caplog.at_level(logging.INFO, logger="atlas.llm.decision"):
        client = mod.get_decision_client()
    assert isinstance(client, LiteLLMDecisionClient)
    assert any("LiteLLM(model=gpt-4o-mini)" in r.message for r in caplog.records)

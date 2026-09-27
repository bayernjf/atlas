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


# ==================== W5-5.1 运营 prompt / model 真实生效 ====================


def _install_capturing_litellm(monkeypatch, content: str) -> dict:
    """捕获 litellm.completion 的入参，用于断言 prompt/model 是否真被消费。"""
    captured: dict = {}

    def _completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


def test_litellm_uses_operator_prompt_verbatim_as_user_message(monkeypatch):
    """R1 反门：图里改 prompt 必须原样进模型（不再发写死的退款 prompt）。"""
    captured = _install_capturing_litellm(
        monkeypatch,
        '{"action": "request_human_approval", "reason": "x", "confidence": 0.9}',
    )
    LiteLLMDecisionClient("gpt-test").decide_refund(
        reason="商品破损", amount=100, limit=500, prompt="运营写的提示词 {{tool-1.result.x}}"
    )
    messages = captured["messages"]
    # 用户消息就是运营 prompt（插值后），不是硬编码退款文案。
    assert messages[1] == {"role": "user", "content": "运营写的提示词 {{tool-1.result.x}}"}
    # 结构化输出由系统消息强制（等价 with_structured_output）。
    assert messages[0]["role"] == "system"
    assert captured["response_format"] == {"type": "json_object"}


def test_litellm_falls_back_to_refund_prompt_when_operator_prompt_blank(monkeypatch):
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    LiteLLMDecisionClient("gpt-test").decide_refund(reason="商品破损", amount=100, limit=500)
    user = captured["messages"][1]["content"]
    assert "你是电商售后审批员" in user
    assert "商品破损" in user


def test_litellm_node_model_overrides_default_model(monkeypatch):
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    decision = LiteLLMDecisionClient("default-model").decide_refund(
        reason="x", amount=1, limit=5, prompt="p", model="node-model"
    )
    assert captured["model"] == "node-model"
    assert decision["source"] == "llm:node-model"


def test_litellm_blank_node_model_falls_back_to_default(monkeypatch):
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    LiteLLMDecisionClient("default-model").decide_refund(
        reason="x", amount=1, limit=5, prompt="p", model="   "
    )
    assert captured["model"] == "default-model"


def test_rule_client_accepts_and_ignores_prompt_and_model():
    """规则兜底没有语义能力：接受 prompt/model 入参但不改变确定性结论。"""
    client = RuleBasedDecisionClient()
    base = client.decide_refund(reason="商品破损", amount=299, limit=500)
    with_prompt = client.decide_refund(
        reason="商品破损", amount=299, limit=500, prompt="任意运营提示词", model="any-model"
    )
    assert base == with_prompt
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

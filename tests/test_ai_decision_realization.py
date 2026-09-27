"""W5-5.1：`ai_decision` 真实化（docs/78）。

两条 Done-when：
① 运营 `promptTemplate` 插值后真实透传给决策器、节点 `model` 覆盖默认模型；
② `confidenceThreshold` 双向：低于阈值挂起转人工（复用 ApprovalBroker），高于不挂起。
"""

from __future__ import annotations

from atlas.collaboration.approvals import ApprovalBroker
from atlas.graph.dsl import NodeDSL, parse_graph
from atlas.graph.loader import (
    _decision_confidence_threshold,
    _escalate_low_confidence,
    run_graph,
)
from atlas.llm.decision import AUTO_APPROVE, HUMAN_APPROVAL


def _decision_graph(*, prompt="运营提示词 {{global.limit}}", model=None, threshold=None):
    config: dict = {"promptTemplate": prompt}
    if model is not None:
        config["model"] = model
    if threshold is not None:
        config["confidenceThreshold"] = threshold
    return parse_graph(
        {
            "version": 1,
            "variables": [{"name": "limit", "type": "number", "value": "100", "scope": "global"}],
            "nodes": [{"id": "ai-1", "type": "ai_decision", "name": "决策", "config": config}],
            "edges": [],
        }
    )


class _FixedDecisionClient:
    """返回固定置信度的假决策器，同时记录收到的 prompt/model。"""

    def __init__(self, confidence: float, action: str = AUTO_APPROVE):
        self.confidence = confidence
        self.action = action
        self.calls: list[dict] = []

    def decide_refund(self, *, reason, amount, limit, prompt="", model=None):
        self.calls.append(
            {"reason": reason, "amount": amount, "limit": limit, "prompt": prompt, "model": model}
        )
        return {
            "action": self.action,
            "reason": "fake",
            "confidence": self.confidence,
            "source": "fake",
        }


def _resolve_on_emit(broker: ApprovalBroker, decision: str):
    """emit 回调：看到 approval 载荷即登记 token 并立即决策（wait 前先决亦可）。

    返回捕获到的 token 列表，供断言"确实挂起登记过审批"。
    """
    tokens: list[str] = []

    def _emit(event: dict) -> None:
        approval = event.get("approval")
        if approval:
            tokens.append(approval["token"])
            broker.resolve(approval["token"], decision, resolved_by="human")

    return _emit, tokens


# -------- ① prompt / model 真实透传 --------


def test_ai_decision_passes_rendered_prompt_and_node_model_to_client():
    client = _FixedDecisionClient(0.9)
    result = run_graph(
        _decision_graph(prompt="运营提示词 {{global.limit}}", model="m-1"),
        decision_client=client,
    )
    assert result["status"] == "completed"
    assert client.calls[0]["prompt"] == "运营提示词 100"
    assert client.calls[0]["model"] == "m-1"
    assert result["outputs"]["ai-1"]["prompt_rendered"] == "运营提示词 100"


def test_ai_decision_blank_node_model_passes_none():
    client = _FixedDecisionClient(0.9)
    run_graph(_decision_graph(model=""), decision_client=client)
    assert client.calls[0]["model"] is None


# -------- ② 置信度闸门双向 --------


def test_low_confidence_suspends_and_adopts_human_approval():
    broker = ApprovalBroker()
    emit, tokens = _resolve_on_emit(broker, "approved")
    result = run_graph(
        _decision_graph(threshold=0.6),
        decision_client=_FixedDecisionClient(0.2),
        approval_broker=broker,
        emit=emit,
    )
    decision = result["outputs"]["ai-1"]["decision"]
    assert tokens, "低于阈值必须登记审批（挂起）"
    assert decision["escalated"] is True
    assert decision["resolvedBy"] == "human"
    assert decision["action"] == AUTO_APPROVE


def test_low_confidence_rejected_keeps_human_approval():
    broker = ApprovalBroker()
    emit, tokens = _resolve_on_emit(broker, "rejected")
    result = run_graph(
        _decision_graph(threshold=0.6),
        decision_client=_FixedDecisionClient(0.2, action=AUTO_APPROVE),
        approval_broker=broker,
        emit=emit,
    )
    decision = result["outputs"]["ai-1"]["decision"]
    assert tokens
    assert decision["escalated"] is True
    assert decision["action"] == HUMAN_APPROVAL


def test_confidence_at_threshold_does_not_suspend():
    """边界：confidence == threshold 不触发（严格小于才挂起）。"""
    approvals: list[dict] = []

    def _emit(event: dict) -> None:
        if event.get("approval"):
            approvals.append(event)

    result = run_graph(
        _decision_graph(threshold=0.6),
        decision_client=_FixedDecisionClient(0.6),
        approval_broker=ApprovalBroker(),
        emit=_emit,
    )
    decision = result["outputs"]["ai-1"]["decision"]
    assert "escalated" not in decision
    assert approvals == []


def test_missing_threshold_defaults_to_zero_point_six():
    broker = ApprovalBroker()
    emit, tokens = _resolve_on_emit(broker, "approved")
    result = run_graph(
        _decision_graph(),  # 无 confidenceThreshold
        decision_client=_FixedDecisionClient(0.5),
        approval_broker=broker,
        emit=emit,
    )
    assert tokens
    assert result["outputs"]["ai-1"]["decision"]["escalated"] is True


def test_invalid_threshold_falls_back_to_default():
    node = NodeDSL(
        id="n",
        type="ai_decision",
        name="n",
        config={"promptTemplate": "x", "confidenceThreshold": "abc"},
    )
    assert _decision_confidence_threshold(node) == 0.6


def test_escalation_without_broker_fails_safe_without_blocking():
    node = NodeDSL(id="n", type="ai_decision", name="决策", config={"promptTemplate": "x"})
    out = _escalate_low_confidence(
        node,
        result={"action": AUTO_APPROVE, "reason": "r", "confidence": 0.1, "source": "fake"},
        threshold=0.6,
        broker=None,
        graph_id="g",
        emit=lambda _event: None,
        start_event={},
    )
    assert out["action"] == HUMAN_APPROVAL
    assert out["escalated"] is True
    assert out["resolvedBy"] == "no_broker"
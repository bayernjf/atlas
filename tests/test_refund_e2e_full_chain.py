# -*- coding: utf-8 -*-
"""J-2b：跨 human_approval 的真实退款全链 e2e（docs/64 打包 J；docs/63 §2.2）。

同一条测试内同时存在 human_approval 节点与真实 shop/process_refund 工具：
预置 inputs.approvals 通过 → 人工审批真实落决策（resolved_by=input）→
退款工具真实落库（orders.status == "refunded"）。拒绝分支不落退款。
docs/08 §7.3 criterion 4 的验收口径据此补强（先补测试，再改口径注记）。
"""

from __future__ import annotations

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import build_demo_registry, run_graph
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.shop.service import DemoShopService


def _full_chain_graph():
    """trigger → ai_decision（规则决策）→ human_approval → 真实退款 / 记录拒绝。

    AI 决策输出在 context 中传递给下游；process_refund 依赖上游 ai_decision 输出
    （loader `_first_decision`），故 human 节点必须位于 ai_decision 之后。
    """
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
            ],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "新退款申请",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "ai_decision-1", "type": "ai_decision", "name": "退款决策",
                 "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}} 金额 {{trigger-1.context.payload.amount}}"}},
                {"id": "human-1", "type": "human_approval", "name": "人工审批",
                 "config": {
                     "summary": "订单退款审批",
                     "approver": "客服主管",
                     "timeoutSeconds": 300,
                     "onTimeout": "reject",
                     "approvedTarget": "tool-refund",
                     "rejectedTarget": "tool-note",
                 }},
                {"id": "tool-refund", "type": "tool_call", "name": "真实退款",
                 "config": {"tool": "shop/process_refund"}},
                {"id": "tool-note", "type": "tool_call", "name": "记录拒绝",
                 "config": {"tool": "op-reject"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "human-1"},
                {"id": "e3", "source": "human-1", "target": "tool-refund"},
                {"id": "e4", "source": "human-1", "target": "tool-note"},
            ],
        }
    )


def _registry_with_real_shop(service: DemoShopService):
    registry = build_demo_registry()
    registry.unregister("shop")
    from atlas.harness.base import Permission
    from atlas.shop.adapter import ShopHarnessAdapter

    registry.register(
        ShopHarnessAdapter(
            service=service,
            granted_permissions={
                Permission.READ,
                Permission.WRITE,
                Permission.FINANCIAL,
            },
        )
    )
    return registry


def test_approved_human_approval_runs_real_refund_end_to_end():
    """J-2b：人工审批通过 → 真实退款落库（同链含 human_approval + process_refund）。"""
    service = DemoShopService()
    events: list[dict] = []
    result = run_graph(
        _full_chain_graph(),
        inputs={
            "order_id": "12345",
            "reason": "商品破损",
            "amount": 299,
            "approvals": {"human-1": "approved"},
        },
        decision_client=RuleBasedDecisionClient(),
        registry=_registry_with_real_shop(service),
        emit=events.append,
    )
    assert result["status"] == "completed"
    approval = result["outputs"]["human-1"]
    assert approval["decision"] == "approved"
    assert approval["resolvedBy"] == "input"
    assert approval["target"] == "tool-refund"
    assert result["outputs"]["tool-refund"]["result"] == {
        "order_id": "12345",
        "status": "refunded",
    }
    assert result["outputs"]["tool-refund"]["action_status"] == "SUCCESS"
    assert service.orders["12345"].status == "refunded"
    # 事件序列：trigger → ai_decision → human → 退款工具（真实退款的 tool_metric）
    start_events = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert start_events == ["trigger-1", "ai_decision-1", "human-1", "tool-refund"]
    metric = next(event for event in events if event["type"] == "tool_metric")
    assert metric["node_id"] == "tool-refund"
    assert metric["action_status"] == "SUCCESS"


def test_rejected_human_approval_skips_refund():
    """J-2b：人工审批拒绝 → 走拒绝侧，不落退款。"""
    service = DemoShopService()
    result = run_graph(
        _full_chain_graph(),
        inputs={
            "order_id": "12345",
            "reason": "不想要了",
            "amount": 5000,
            "approvals": {"human-1": "rejected"},
        },
        decision_client=RuleBasedDecisionClient(),
        registry=_registry_with_real_shop(service),
    )
    assert result["status"] == "completed"
    assert result["outputs"]["human-1"]["decision"] == "rejected"
    assert result["outputs"]["human-1"]["target"] == "tool-note"
    assert "tool-refund" not in result["outputs"]
    assert service.orders["12345"].status != "refunded"

"""W9-W10 端到端：退款 Graph 经 DSL 编译运行至 shop 适配器（docs/08 §7.3）。"""

from __future__ import annotations

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import build_demo_registry, run_graph
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.shop.service import DemoShopService


def _refund_graph():
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
                {"id": "tool_call-1", "type": "tool_call", "name": "执行处理",
                 "config": {"tool": "shop/process_refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
            ],
        }
    )


def test_golden_order_12345_auto_refunded_end_to_end():
    service = DemoShopService()
    registry = build_demo_registry()
    registry.unregister("shop")
    from atlas.shop.adapter import ShopHarnessAdapter
    from atlas.harness.base import Permission

    registry.register(
        ShopHarnessAdapter(service=service, granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL})
    )
    events = []
    result = run_graph(
        _refund_graph(),
        inputs={"order_id": "12345", "reason": "商品破损", "amount": 299},
        decision_client=RuleBasedDecisionClient(),
        registry=registry,
        emit=events.append,
    )
    assert result["status"] == "completed"
    decision = result["outputs"]["ai_decision-1"]["decision"]
    assert decision["action"] == "approve_refund"
    assert result["outputs"]["tool_call-1"]["result"] == {"order_id": "12345", "status": "refunded"}
    assert result["outputs"]["tool_call-1"]["action_status"] == "SUCCESS"
    assert service.orders["12345"].status == "refunded"
    # 事件序列供 SSE 实时进度使用
    assert [event["type"] for event in events] == [
        "node_start",
        "node_end",
        "node_start",
        "node_end",
        "node_start",
        "node_end",
        "run_end",
    ]
    start_events = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert start_events == ["trigger-1", "ai_decision-1", "tool_call-1"]


def test_golden_order_12346_routed_to_human_review_end_to_end():
    result = run_graph(
        _refund_graph(),
        inputs={"order_id": "12346", "reason": "不想要了", "amount": 5000},
        decision_client=RuleBasedDecisionClient(),
        registry=build_demo_registry(),
    )
    assert result["outputs"]["ai_decision-1"]["decision"]["action"] == "request_human_approval"
    assert result["outputs"]["tool_call-1"]["result"] == {"order_id": "12346", "status": "human_review"}

def test_variables_flow_between_nodes_via_payload():
    result = run_graph(
        _refund_graph(),
        inputs={"order_id": "12347", "reason": "商品有质量瑕疵", "amount": 128},
        decision_client=RuleBasedDecisionClient(),
        registry=build_demo_registry(),
    )
    rendered = result["outputs"]["ai_decision-1"]["prompt_rendered"]
    assert "商品有质量瑕疵" in rendered and "128" in rendered

"""W9-W10 Demo API 测试：适配器发现、NL 生成、SSE 运行、模拟店铺控制台。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)


def _refund_graph() -> dict:
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "新退款",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
             "position": {"x": 0, "y": 0}, "config": {"promptTemplate": "{{trigger-1.context.payload.reason}}"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "处理",
             "position": {"x": 0, "y": 0}, "config": {"tool": "shop/process_refund"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def test_adapters_lists_shop_capabilities():
    body = client.get("/api/adapters").json()
    shop = next(item for item in body if item["id"] == "shop")
    tools = {tool["name"] for tool in shop["tools"]}
    assert {"login", "list_pending_refunds", "execute_refund", "request_human_approval", "process_refund"} <= tools


def test_nl_generate_refund_intent_returns_draft():
    response = client.post("/api/nl/generate", json={"prompt": "做一个电商退款自动审批流"})
    assert response.status_code == 200
    graph = response.json()["graph"]
    assert graph["version"] == 1
    assert len(graph["nodes"]) == 3
    # 草稿必须能直接保存（通过 DSL 校验）
    saved = client.post("/api/graphs", json=graph)
    assert saved.status_code == 200


def test_nl_generate_unknown_intent_returns_422(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    response = client.post("/api/nl/generate", json={"prompt": "帮我管日历"})
    assert response.status_code == 422


def test_run_stream_emits_sse_events():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    with client.stream(
        "POST",
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {"order_id": "12347", "reason": "商品有质量瑕疵", "amount": 128}},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = [line for line in response.iter_lines() if line.startswith("event:")]
    assert "event: node_start" in events
    assert "event: node_end" in events
    assert events[-1] == "event: result"


def test_run_refund_flow_through_demo_registry():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12348", "reason": "商家错发商品", "amount": 460}},
    )
    assert response.status_code == 200
    tool_output = response.json()["outputs"]["tool_call-1"]
    assert tool_output["result"] == {"order_id": "12348", "status": "refunded"}


def test_demo_shop_console_login_and_orders():
    assert client.post("/api/demo/shop/login", json={"username": "x", "password": "y"}).status_code == 401
    assert client.post("/api/demo/shop/login", json={"username": "demo", "password": "demo"}).status_code == 200
    orders = client.get("/api/demo/shop/orders").json()["orders"]
    assert any(order["order_id"] == "12345" for order in orders)
    page = client.get("/demo/shop")
    assert page.status_code == 200
    assert "Demo 商家售后控制台" in page.text


def test_demo_reset_restores_seed_orders_and_clears_graphs():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12349", "reason": "尺寸不合适", "amount": 899}},
    )
    client.post("/api/demo/shop/login", json={"username": "demo", "password": "demo"})
    assert all(order["order_id"] != "12349" for order in client.get("/api/demo/shop/orders").json()["orders"])
    assert client.get(f"/api/graphs/{graph_id}").status_code == 200

    assert client.post("/api/demo/reset").json() == {"reset": True}

    # 重置后登录态恢复，需重新登录
    assert client.get("/api/demo/shop/orders").status_code == 401
    client.post("/api/demo/shop/login", json={"username": "demo", "password": "demo"})
    orders = client.get("/api/demo/shop/orders").json()["orders"]
    assert any(order["order_id"] == "12349" for order in orders)
    assert client.get(f"/api/graphs/{graph_id}").status_code == 404


def test_frontend_static_served_when_dist_exists():
    response = client.get("/")
    if response.status_code == 404:
        # 仓库未构建前端（frontend/dist 不存在）时不挂载，dev 走 Vite 5174
        return
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_feedback_submit_list_and_survives_demo_reset():
    marker = f"画布上节点不动了-{uuid.uuid4()}"
    created = client.post(
        "/api/feedback",
        json={"type": "bug", "content": marker, "contact": "wechat: trial-user"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["id"].startswith("feedback-")
    assert body["created_at"]

    items = client.get("/api/feedback").json()["items"]
    assert any(item["content"] == marker and item["type"] == "bug" for item in items)

    # reset 只清 Demo 业务数据，反馈保留
    client.post("/api/demo/reset")
    assert any(item["content"] == marker for item in client.get("/api/feedback").json()["items"])


def test_feedback_rejects_invalid_type_and_empty_content():
    assert client.post("/api/feedback", json={"type": "other", "content": "x"}).status_code == 422
    assert client.post("/api/feedback", json={"type": "bug", "content": ""}).status_code == 422


def _human_approval_graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批",
             "position": {"x": 0, "y": 0},
             "config": {
                 "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
                 "approver": "客服主管",
                 "timeoutSeconds": 10,
                 "onTimeout": "reject",
                 "approvedTarget": "tool-approve",
                 "rejectedTarget": "tool-reject",
             }},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "position": {"x": 0, "y": 0}, "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "position": {"x": 0, "y": 0}, "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


def test_run_with_preset_approval_runs_rejected_branch():
    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "20001", "approvals": {"human-1": "rejected"}}},
    )
    assert response.status_code == 200
    outputs = response.json()["outputs"]
    assert outputs["human-1"]["decision"] == "rejected"
    assert outputs["human-1"]["resolvedBy"] == "input"
    assert "tool-reject" in outputs
    assert "tool-approve" not in outputs


def test_live_stream_human_approval_decision_unblocks_run():
    import json as _json
    import threading
    import time

    from fastapi.testclient import TestClient

    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]

    def decide_when_pending() -> None:
        with TestClient(app) as decider:
            token = None
            for _ in range(100):
                items = decider.get("/api/approvals").json()["items"]
                if items:
                    token = items[0]["token"]
                    break
                time.sleep(0.02)
            assert token is not None
            response = decider.post(
                f"/api/approvals/{token}/decision",
                json={"decision": "approved", "comment": "同意"},
            )
            assert response.status_code == 200

    decider_thread = threading.Thread(target=decide_when_pending)
    decider_thread.start()

    saw_approval_start = False
    result_body: dict = {}
    with client.stream(
        "POST",
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {"order_id": "20002"}},
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = _json.loads(line[len("data:"):].strip())
            if "approval" in payload:
                assert payload["approval"]["summary"] == "订单 20002 退款审批"
                assert payload["approval"]["timeoutSeconds"] == 10
                saw_approval_start = True
            if "outputs" in payload:
                result_body = payload

    decider_thread.join(timeout=5)
    assert saw_approval_start is True
    assert result_body["outputs"]["human-1"]["decision"] == "approved"
    assert result_body["outputs"]["human-1"]["resolvedBy"] == "human"
    assert "tool-approve" in result_body["outputs"]
    assert "tool-reject" not in result_body["outputs"]


def test_approval_decision_endpoint_404_409_422_and_reset():
    from atlas.api.main import _approval_broker

    assert client.post(
        "/api/approvals/unknown-token/decision", json={"decision": "approved"}
    ).status_code == 404

    token = _approval_broker.request(
        node_id="human-x",
        graph_id="graph-x",
        summary="测试审批",
        approver="tester",
        timeout_seconds=30,
    )
    pending = client.get("/api/approvals").json()["items"]
    assert any(item["token"] == token for item in pending)

    bad = client.post(f"/api/approvals/{token}/decision", json={"decision": "maybe"})
    assert bad.status_code == 422

    assert client.post(
        f"/api/approvals/{token}/decision", json={"decision": "rejected"}
    ).status_code == 200
    conflict = client.post(
        f"/api/approvals/{token}/decision", json={"decision": "approved"}
    )
    assert conflict.status_code == 409
    assert client.get("/api/approvals").json()["items"] == []

    client.post("/api/demo/reset")


def _subgraph_child_graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "child-trigger", "type": "trigger", "name": "子图触发",
             "position": {"x": 0, "y": 0}, "config": {"triggerType": "manual"}},
            {"id": "child-tool", "type": "tool_call", "name": "子图工具",
             "position": {"x": 0, "y": 0}, "config": {"tool": "op-child"}},
        ],
        "edges": [
            {"id": "ce1", "source": "child-trigger", "target": "child-tool"},
        ],
    }


def _subgraph_parent_graph(child_id: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "position": {"x": 0, "y": 0}, "config": {"triggerType": "manual"}},
            {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
             "position": {"x": 0, "y": 0},
             "config": {"graphId": child_id,
                        "inputs": {"order_id": "{{trigger-1.context.payload.order_id}}"}}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "position": {"x": 0, "y": 0}, "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
            {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
        ],
    }


def test_list_graphs_returns_saved_items_with_node_count():
    client.post("/api/demo/reset")
    saved = client.post("/api/graphs", json=_subgraph_child_graph()).json()
    items = client.get("/api/graphs").json()["items"]
    item = next(item for item in items if item["id"] == saved["id"])
    assert item["node_count"] == 2
    assert isinstance(item["updated_at"], str) and item["updated_at"]


def test_subgraph_parent_runs_saved_child_through_resolver():
    client.post("/api/demo/reset")
    child_id = client.post("/api/graphs", json=_subgraph_child_graph()).json()["id"]
    parent_id = client.post("/api/graphs", json=_subgraph_parent_graph(child_id)).json()["id"]

    assert client.post(f"/api/graphs/{parent_id}/compile").status_code == 200
    response = client.post(
        f"/api/graphs/{parent_id}/run", json={"inputs": {"order_id": "X-9"}}
    )
    assert response.status_code == 200
    outputs = response.json()["outputs"]
    node = outputs["subgraph-1"]
    assert node["status"] == "success"
    assert node["graphId"] == child_id
    assert node["outputs"]["child-trigger"]["context"]["payload"] == {"order_id": "X-9"}
    assert "tool-after" in outputs


def test_subgraph_missing_reference_compile_returns_422():
    client.post("/api/demo/reset")
    parent_id = client.post("/api/graphs", json=_subgraph_parent_graph("graph-ghost")).json()["id"]
    response = client.post(f"/api/graphs/{parent_id}/compile")
    assert response.status_code == 422
    assert any("引用的子图不存在：graph-ghost" in detail for detail in response.json()["detail"])
    client.post("/api/demo/reset")

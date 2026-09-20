"""W9-W10 Demo API 测试：适配器发现、NL 生成、SSE 运行、模拟店铺控制台。"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

from tests.conftest import DEFAULT_AUTH_HEADER

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
        with TestClient(app, headers=DEFAULT_AUTH_HEADER) as decider:
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
    from atlas.iam.deps import tenant_registry
    _approval_broker = tenant_registry.get("t1").approval_broker

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


def test_adapters_lists_http_request_capability():
    body = client.get("/api/adapters").json()
    http = next(item for item in body if item["id"] == "http")
    assert http["type"] == "api"
    assert [tool["name"] for tool in http["tools"]] == ["request"]


def test_mock_orders_requires_demo_token():
    assert client.get("/api/demo/mock/orders").status_code == 401
    ok = client.get("/api/demo/mock/orders", headers={"X-Demo-Token": "demo-token"})
    assert ok.status_code == 200
    assert len(ok.json()["orders"]) == 2


def test_mock_receipt_echoes_body():
    response = client.post(
        "/api/demo/mock/orders/12345/receipt",
        json={"note": "自动处理", "amount": 299},
    )
    assert response.status_code == 200
    assert response.json() == {
        "order_id": "12345",
        "body": {"note": "自动处理", "amount": 299},
        "received": True,
    }


def _http_mock_graph() -> dict:
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/mock"}},
            {"id": "tool-get", "type": "tool_call", "name": "拉单",
             "position": {"x": 0, "y": 0},
             "config": {
                 "tool": "http/request",
                 "params": json.dumps(
                     {"url": "/api/demo/mock/orders",
                      "headers": {"X-Demo-Token": "demo-token"}}
                 ),
             }},
            {"id": "tool-post", "type": "tool_call", "name": "回单",
             "position": {"x": 0, "y": 0},
             "config": {
                 "tool": "http/request",
                 "params": json.dumps(
                     {"method": "POST",
                      "url": "/api/demo/mock/orders/{{trigger-1.context.payload.order_id}}/receipt",
                      "body": {"note": "{{trigger-1.context.payload.note}}"}}
                 ),
             }},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "tool-get"},
            {"id": "e2", "source": "tool-get", "target": "tool-post"},
        ],
    }


def _live_server_base_url() -> str:
    """同步 httpx 不能用 ASGITransport；在回环口起一次性 uvicorn 供 http 适配器真打 mock 端点。"""
    global _live_server_url
    if _live_server_url is not None:
        return _live_server_url
    import socket
    import threading

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import httpx

    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                break
        except httpx.RequestError:
            pass
    else:
        raise RuntimeError("live uvicorn server did not become ready")
    _live_server_url = f"http://127.0.0.1:{port}"
    return _live_server_url


_live_server_url: str | None = None


def _registry_with_http(monkeypatch, base_url: str):
    from atlas.api import main as main_module
    from atlas.harness.registry import AdapterRegistry
    from atlas.httpapi.adapter import HttpApiHarnessAdapter
    from atlas.httpapi.service import HttpApiClient

    registry = AdapterRegistry()
    registry.register(
        HttpApiHarnessAdapter(
            client=HttpApiClient(base_url=base_url),
            granted_permissions={"read", "write", "delete", "financial"},
        )
    )
    monkeypatch.setattr(main_module, "_demo_registry", registry)


def test_http_request_against_mock_endpoints_end_to_end(monkeypatch):
    _registry_with_http(monkeypatch, _live_server_base_url())

    client.post("/api/demo/reset")
    graph_id = client.post("/api/graphs", json=_http_mock_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12345", "note": "自动签收"}},
    )

    assert response.status_code == 200
    outputs = response.json()["outputs"]
    assert outputs["tool-get"]["action_status"] == "SUCCESS"
    assert outputs["tool-get"]["result"]["status"] == 200
    assert outputs["tool-get"]["result"]["body"]["orders"][0]["order_id"] == "12345"
    post_result = outputs["tool-post"]["result"]
    assert post_result["status"] == 200
    assert post_result["body"]["order_id"] == "12345"
    assert post_result["body"]["body"] == {"note": "自动签收"}
    assert post_result["body"]["received"] is True
    client.post("/api/demo/reset")


def test_http_request_401_still_success_with_status(monkeypatch):
    _registry_with_http(monkeypatch, _live_server_base_url())

    graph = _http_mock_graph()
    # 去掉鉴权头：mock 端点返回 401，但 HTTP 适配器仍判 SUCCESS 带 status
    graph["nodes"] = graph["nodes"][:2]
    graph["edges"] = [graph["edges"][0]]
    graph["nodes"][1]["config"]["params"] = json.dumps({"url": "/api/demo/mock/orders"})

    client.post("/api/demo/reset")
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    outputs = client.post(f"/api/graphs/{graph_id}/run", json={}).json()["outputs"]

    assert outputs["tool-get"]["action_status"] == "SUCCESS"
    assert outputs["tool-get"]["result"]["status"] == 401
    client.post("/api/demo/reset")


def _single_tool_graph(tool: str, params: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/demo"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "position": {"x": 0, "y": 0}, "config": {"tool": tool, "params": params}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def test_adapters_lists_database_and_message_capabilities():
    body = client.get("/api/adapters").json()

    database = next(item for item in body if item["id"] == "database")
    assert database["type"] == "database"
    assert {tool["name"] for tool in database["tools"]} == {"query", "execute"}

    message = next(item for item in body if item["id"] == "message")
    assert message["type"] == "message"
    assert [tool["name"] for tool in message["tools"]] == ["send"]


def test_database_query_against_demo_sqlite_end_to_end():
    client.post("/api/demo/reset")
    params = json.dumps({"sql": "SELECT order_id, amount FROM orders ORDER BY order_id"})
    graph_id = client.post("/api/graphs", json=_single_tool_graph("database/query", params)).json()["id"]

    outputs = client.post(f"/api/graphs/{graph_id}/run", json={}).json()["outputs"]

    node = outputs["tool-1"]
    assert node["action_status"] == "SUCCESS"
    assert node["result"]["row_count"] == 2
    assert node["result"]["rows"][0] == {"order_id": "12345", "amount": 299}
    client.post("/api/demo/reset")


def test_database_execute_visible_to_query_and_reset_reseeds():
    client.post("/api/demo/reset")
    insert_params = json.dumps(
        {
            "sql": "INSERT INTO orders (order_id, reason, amount, status) "
                   "VALUES (:order_id, :reason, :amount, :status)",
            "params": {"order_id": "12350", "reason": "测试插入", "amount": 1, "status": "pending_refund"},
        }
    )
    graph_id = client.post("/api/graphs", json=_single_tool_graph("database/execute", insert_params)).json()["id"]
    outputs = client.post(f"/api/graphs/{graph_id}/run", json={}).json()["outputs"]
    assert outputs["tool-1"]["result"] == {"rowcount": 1}

    query_params = json.dumps({"sql": "SELECT COUNT(*) AS n FROM orders"})
    query_id = client.post("/api/graphs", json=_single_tool_graph("database/query", query_params)).json()["id"]
    before_reset = client.post(f"/api/graphs/{query_id}/run", json={}).json()["outputs"]
    assert before_reset["tool-1"]["result"]["rows"][0]["n"] == 3

    client.post("/api/demo/reset")
    query_id = client.post("/api/graphs", json=_single_tool_graph("database/query", query_params)).json()["id"]
    after_reset = client.post(f"/api/graphs/{query_id}/run", json={}).json()["outputs"]
    assert after_reset["tool-1"]["result"]["rows"][0]["n"] == 2


def test_message_send_visible_and_reset_clears():
    client.post("/api/demo/reset")
    assert client.get("/api/demo/messages").json()["items"] == []

    params = json.dumps(
        {
            "channel": "email",
            "to": ["ops@example.com", "boss@example.com"],
            "subject": "订单 {{trigger-1.context.payload.order_id}} 待审批",
            "body": "请处理",
        }
    )
    graph_id = client.post("/api/graphs", json=_single_tool_graph("message/send", params)).json()["id"]
    outputs = client.post(
        f"/api/graphs/{graph_id}/run", json={"inputs": {"order_id": "12346"}}
    ).json()["outputs"]

    assert outputs["tool-1"]["action_status"] == "SUCCESS"
    items = client.get("/api/demo/messages").json()["items"]
    assert len(items) == 1
    assert items[0]["channel"] == "email"
    assert items[0]["to"] == ["ops@example.com", "boss@example.com"]
    assert items[0]["subject"] == "订单 12346 待审批"
    assert items[0]["sent_at"]

    client.post("/api/demo/reset")
    assert client.get("/api/demo/messages").json()["items"] == []


def test_template_list_returns_projection_without_graph():
    client.post("/api/demo/reset")
    items = client.get("/api/templates").json()["items"]
    assert len(items) == 5
    for item in items:
        assert set(item) == {"id", "name", "description", "tags", "node_count"}
        assert "graph" not in item
        assert item["tags"]
        assert item["node_count"] >= 1
    assert [item["id"] for item in items] == [
        "refund-auto",
        "http-orders-branch",
        "sql-query-notify",
        "sql-approval-write",
        "approval-timeout-reject",
    ]


def test_template_detail_includes_graph_and_unknown_id_404():
    detail = client.get("/api/templates/sql-query-notify")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == "sql-query-notify"
    assert set(body) == {"id", "name", "description", "tags", "graph"}
    assert body["graph"]["nodes"]

    assert client.get("/api/templates/nope").status_code == 404


def test_template_detail_graph_saves_and_compiles():
    client.post("/api/demo/reset")
    for template_id in (
        "refund-auto",
        "http-orders-branch",
        "sql-query-notify",
        "sql-approval-write",
        "approval-timeout-reject",
    ):
        graph = client.get(f"/api/templates/{template_id}").json()["graph"]
        saved = client.post("/api/graphs", json=graph)
        assert saved.status_code == 200, template_id
        graph_id = saved.json()["id"]
        assert client.post(f"/api/graphs/{graph_id}/compile").status_code == 200, template_id
    client.post("/api/demo/reset")


def test_templates_survive_demo_reset():
    client.post("/api/demo/reset")
    assert len(client.get("/api/templates").json()["items"]) == 5
    assert client.get("/api/templates/refund-auto").status_code == 200


# ---------- I16：操作录制与回放（04 §5.11，06 §6.9） ----------

def _record_approval_timeout_case() -> tuple[str, dict, dict]:
    """跑 approval-timeout-reject（预置 rejected）→ 录制入库；返回 (case_id, graph, run result)。"""
    client.post("/api/demo/reset")
    graph = client.get("/api/templates/approval-timeout-reject").json()["graph"]
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    run = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"approvals": {"approval-1": "rejected"}}},
    )
    assert run.status_code == 200
    result = run.json()
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    steps = [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in result["outputs"].items()
    ]
    response = client.post(
        "/api/recordings",
        json={
            "name": "审批超时模板回放用例",
            "graph_id": graph_id,
            "inputs": {"approvals": {"approval-1": "rejected"}},
            "steps": steps,
            "status": result["status"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], graph, result


def test_recording_create_projection_detail_validation_and_404():
    case_id, graph, result = _record_approval_timeout_case()

    detail = client.get(f"/api/recordings/{case_id}")
    assert detail.status_code == 200
    case = detail.json()
    assert case["id"] == case_id and case["id"].startswith("rec-")
    assert case["graph"] == graph  # 图快照即入库时原始 JSON
    assert case["status"] == result["status"] == "completed"
    assert {step["node_id"] for step in case["steps"]} == {
        "trigger-1", "approval-1", "rejected-msg"
    }

    items = client.get("/api/recordings").json()["items"]
    mine = next(item for item in items if item["id"] == case_id)
    assert set(mine) == {"id", "name", "graph_id", "node_count", "step_count", "status", "created_at"}
    assert mine["graph_id"] == case["graph_id"]
    assert mine["node_count"] == 4 and mine["step_count"] == 3

    assert client.post(
        "/api/recordings",
        json={"name": "x", "graph_id": "graph-missing", "steps": [
            {"node_id": "a", "node_type": "trigger", "output": {}}
        ], "status": "completed"},
    ).status_code == 404
    saved_graph_id = client.post("/api/graphs", json=graph).json()["id"]
    invalid = [
        {"name": "", "graph_id": saved_graph_id, "steps": [{"node_id": "a", "node_type": "trigger", "output": {}}], "status": "completed"},
        {"name": "x" * 101, "graph_id": saved_graph_id, "steps": [{"node_id": "a", "node_type": "trigger", "output": {}}], "status": "completed"},
        {"name": "无步骤", "graph_id": saved_graph_id, "steps": [], "status": "completed"},
        {"name": "坏步骤", "graph_id": saved_graph_id, "steps": [{"node_id": "a"}], "status": "completed"},
    ]
    for payload in invalid:
        assert client.post("/api/recordings", json=payload).status_code == 422, payload

    assert client.get("/api/recordings/rec-missing").status_code == 404


def test_recording_replay_matches_with_approval_preset_and_normalization():
    case_id, _, _ = _record_approval_timeout_case()

    start = time.perf_counter()
    response = client.post(f"/api/recordings/{case_id}/replay")
    elapsed = time.perf_counter() - start
    assert response.status_code == 200
    report = response.json()
    assert elapsed < 5  # 预置决策秒回，不等待模板的 10 秒超时
    assert report["matches"] is True
    assert report["baseline_status"] == report["replay_status"] == "completed"
    rows = {row["node_id"]: row for row in report["steps"]}
    assert set(rows) == {"trigger-1", "approval-1", "rejected-msg"}
    assert all(row["match"] for row in rows.values())
    # 回放中的审批同样来自 inputs 预置（token 被归一化但 resolvedBy 保留）
    replay_detail = client.post(f"/api/recordings/{case_id}/replay").json()
    assert replay_detail["matches"] is True  # 可重复回放


def test_recording_replay_detects_tampered_output():
    case_id, graph, result = _record_approval_timeout_case()
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    tampered_steps = [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in result["outputs"].items()
    ]
    target = next(step for step in tampered_steps if step["node_id"] == "rejected-msg")
    target["output"]["result"]["body"] = "被篡改的正文"
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    tampered_id = client.post("/api/recordings", json={
        "name": "篡改用例", "graph_id": graph_id,
        "inputs": {"approvals": {"approval-1": "rejected"}},
        "steps": tampered_steps, "status": "completed",
    }).json()["id"]

    report = client.post(f"/api/recordings/{tampered_id}/replay").json()
    assert report["matches"] is False
    row = next(row for row in report["steps"] if row["node_id"] == "rejected-msg")
    assert row["match"] is False
    assert "result" in row["diff_keys"]


def _record_mockable_tool_case():
    """trigger(webhook) → 未注册工具 ghost/ping；baseline 工具产出为手造成功桩。"""
    graph = {
        "version": 1, "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "msg-1", "type": "tool_call", "name": "幽灵工具",
             "config": {"tool": "ghost/ping", "params": "{}"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "msg-1"}],
    }
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    trigger_output = {"context": {"triggerType": "webhook", "cron": "",
                                  "webhookUrl": "/hooks/x", "payload": {}}}
    tool_stub = {"result": {"status": "SUCCESS", "echo": "pong"}, "action_status": "SUCCESS"}
    case_id = client.post("/api/recordings", json={
        "name": "Mock 回放用例", "graph_id": graph_id, "inputs": {},
        "steps": [
            {"node_id": "trigger-1", "node_type": "trigger", "output": trigger_output},
            {"node_id": "msg-1", "node_type": "tool_call", "output": tool_stub},
        ],
        "status": "completed",
    }).json()["id"]
    return case_id


def test_replay_mock_tools_skips_unregistered_adapter():
    case_id = _record_mockable_tool_case()

    # 默认（无 body）：真实触达未注册适配器 → FAILED 产出，与成功桩不符；mocked_tools 为 []
    real = client.post(f"/api/recordings/{case_id}/replay").json()
    assert real["mocked_tools"] == []
    assert real["replay_status"] == "completed"
    assert real["matches"] is False
    assert next(r for r in real["steps"] if r["node_id"] == "msg-1")["match"] is False

    # 显式 false 同默认
    off = client.post(f"/api/recordings/{case_id}/replay", json={"mock_tools": False}).json()
    assert off["mocked_tools"] == [] and off["matches"] is False

    # mock_tools=true：桩命中、不触达适配器 → 全节点一致，mocked_tools 含该工具节点
    mocked = client.post(
        f"/api/recordings/{case_id}/replay", json={"mock_tools": True}
    ).json()
    assert mocked["mocked_tools"] == ["msg-1"]
    assert mocked["replay_status"] == "completed"
    assert mocked["matches"] is True
    assert all(row["match"] for row in mocked["steps"])


def test_replay_inputs_override_shallow_merges_and_validates():
    # 单 webhook trigger：trigger output.context.payload 即 inputs。
    graph = {
        "version": 1, "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
        ],
        "edges": [],
    }
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    payload = {"amount": 100, "region": "cn"}
    trigger_output = {"context": {"triggerType": "webhook", "cron": "",
                                  "webhookUrl": "/hooks/x", "payload": payload}}
    case_id = client.post("/api/recordings", json={
        "name": "入参覆写用例", "graph_id": graph_id, "inputs": dict(payload),
        "steps": [{"node_id": "trigger-1", "node_type": "trigger", "output": trigger_output}],
        "status": "completed",
    }).json()["id"]

    # 无覆写：payload 与 baseline 一致
    assert client.post(f"/api/recordings/{case_id}/replay").json()["matches"] is True
    # 覆写 amount：浅合并后 region 保留、amount 改变 → trigger 产出不符
    changed = client.post(
        f"/api/recordings/{case_id}/replay",
        json={"inputs_override": {"amount": 9999}},
    ).json()
    assert changed["matches"] is False
    # 只覆写 amount 为原值且不带 region：浅合并保留 region → 仍一致（证明非整体替换）
    kept = client.post(
        f"/api/recordings/{case_id}/replay",
        json={"inputs_override": {"amount": 100}},
    ).json()
    assert kept["matches"] is True
    # 非法类型 422
    assert client.post(
        f"/api/recordings/{case_id}/replay", json={"inputs_override": "x"}
    ).status_code == 422
    assert client.post(
        f"/api/recordings/{case_id}/replay", json={"mock_tools": ["x"]}
    ).status_code == 422


def test_recording_update_meta_endpoint():
    case_id = _record_mockable_tool_case()

    # 改名 + 改 inputs
    resp = client.put(f"/api/recordings/{case_id}", json={"name": "新名", "inputs": {"amount": 5}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "新名" and body["inputs"] == {"amount": 5}
    # 录制事实不动（steps/graph_id/status/graph 快照）
    assert len(body["steps"]) == 2 and body["status"] == "completed"
    assert body["graph_id"] and isinstance(body["graph"], dict)
    # 详情与列表投影反映新值
    got = client.get(f"/api/recordings/{case_id}").json()
    assert got["name"] == "新名" and got["inputs"] == {"amount": 5}
    proj = next(i for i in client.get("/api/recordings").json()["items"] if i["id"] == case_id)
    assert proj["name"] == "新名"
    # 仅改名时 inputs 保留
    only_name = client.put(f"/api/recordings/{case_id}", json={"name": "只改名"}).json()
    assert only_name["name"] == "只改名" and only_name["inputs"] == {"amount": 5}
    # 空 body（无字段）原样返回 200
    assert client.put(f"/api/recordings/{case_id}", json={}).json()["name"] == "只改名"
    # 404 / 422
    assert client.put("/api/recordings/rec-nope", json={"name": "x"}).status_code == 404
    assert client.put(f"/api/recordings/{case_id}", json={"name": ""}).status_code == 422
    assert client.put(f"/api/recordings/{case_id}", json={"name": "x" * 101}).status_code == 422
    assert client.put(f"/api/recordings/{case_id}", json={"inputs": "bad"}).status_code == 422
    # viewer（read）不可编辑（operate）
    viewer_token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    denied = client.put(
        f"/api/recordings/{case_id}",
        json={"name": "x"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert denied.status_code == 403


def test_recordings_survive_reset_but_delete_removes_them():
    case_id, _, _ = _record_approval_timeout_case()
    client.post("/api/demo/reset")  # GraphStore 清空，但用例图已快照
    assert client.get(f"/api/recordings/{case_id}").status_code == 200
    report = client.post(f"/api/recordings/{case_id}/replay")
    assert report.status_code == 200
    assert report.json()["matches"] is True

    assert client.delete(f"/api/recordings/{case_id}").status_code == 200
    assert client.get(f"/api/recordings/{case_id}").status_code == 404
    assert client.delete(f"/api/recordings/{case_id}").status_code == 404
    assert client.post(f"/api/recordings/{case_id}/replay").status_code == 404


def test_recording_replay_folds_missing_subgraph_reference():
    client.post("/api/demo/reset")
    # 父图引用一个「录制时就不存在」的子图（保存仅结构校验、不 check_refs，故可存）；
    # 录制收集快照时该引用 fetch 为空、不内联（subgraphs={}），回放期内联未命中、
    # 实时 store 也缺失，折叠为 failed 报告而非 500。录制时存在的子图则被内联冻结、
    # reset 后仍可回放，见 test_recording_inlines_subgraph_snapshot_for_replay_after_child_changes。
    parent = _subgraph_parent_graph("graph-never-existed")
    parent_id = client.post("/api/graphs", json=parent).json()["id"]
    case_id = client.post("/api/recordings", json={
        "name": "缺失子图用例", "graph_id": parent_id, "inputs": {"order_id": "X-1"},
        "steps": [{"node_id": "trigger-1", "node_type": "trigger", "output": {}}],
        "status": "completed",
    }).json()["id"]
    assert client.get(f"/api/recordings/{case_id}").json()["subgraphs"] == {}

    client.post("/api/demo/reset")  # 父图也清空，快照内 graphId 实时不可解析
    response = client.post(f"/api/recordings/{case_id}/replay")
    assert response.status_code == 200  # 折叠为报告而非 500
    report = response.json()
    assert report["matches"] is False
    assert report["replay_status"] == "failed"
    assert report["baseline_status"] == "completed"



# ---------------------------------------------------------------------------
# I17：单步调试与断点 v1 —— debug 流式帧、resume、422/404/409、reset 释放
#
# TestClient 的流式请求在 portal.call 内整段跑完、SSE 帧假脱机后才回到主线程，
# 故 resume 决策必须由独立线程轮询 GET /api/debug 驱动（同人工审批测试模式）。
# ---------------------------------------------------------------------------

def _drive_debug_stream(graph_id, payload, action_for):
    """action_for(projection) -> "step"|"continue"|"stop"，按暂停出现顺序调用。

    返回 (event_names, data_frames, projections)；event_names 含全部 SSE 事件名。
    """
    import threading

    seen: set[str] = set()
    projections: list[dict] = []
    finished = threading.Event()

    def decider() -> None:
        with TestClient(app, headers=DEFAULT_AUTH_HEADER) as ctl:
            while not finished.is_set():
                for item in ctl.get("/api/debug").json()["items"]:
                    if item["token"] in seen:
                        continue
                    seen.add(item["token"])
                    projections.append(item)
                    ctl.post(
                        f"/api/debug/{item['token']}/resume",
                        json={"action": action_for(item)},
                    )
                time.sleep(0.005)

    thread = threading.Thread(target=decider, daemon=True)
    thread.start()

    event_names: list[str] = []
    data_frames: list[dict] = []
    pending_event = None
    with client.stream(
        "POST", f"/api/graphs/{graph_id}/run/stream", json=payload
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event:"):
                pending_event = line[len("event:"):].strip()
                event_names.append(pending_event)
            elif line.startswith("data:"):
                data_frames.append(json.loads(line[len("data:"):].strip()))

    finished.set()
    thread.join(timeout=5)
    return event_names, data_frames, projections


def _paused(data_frames):
    return [f for f in data_frames if f.get("type") == "paused"]


def test_i17_debug_step_pauses_follow_node_order_with_snapshots():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    event_names, frames, _ = _drive_debug_stream(
        graph_id,
        {"inputs": {"order_id": "DBG-1", "reason": "测试", "amount": 128},
         "debug": {"breakpoints": []}},
        lambda item: "step",
    )

    paused = _paused(frames)
    assert [f["node_id"] for f in paused] == [
        "trigger-1", "ai_decision-1", "tool_call-1"
    ]
    assert [f["node_type"] for f in paused] == [
        "trigger", "ai_decision", "tool_call"
    ]
    assert all(f["reason"] == "step" and f["token"].startswith("dbg-") for f in paused)
    assert paused[0]["outputs"] == {}
    assert "trigger-1" in paused[1]["outputs"]
    assert event_names[-1] == "result"
    assert frames[-1]["id"] == graph_id


def test_i17_debug_continue_then_unconditional_breakpoint_pauses():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    event_names, frames, projections = _drive_debug_stream(
        graph_id,
        {"inputs": {"order_id": "DBG-2", "reason": "测试", "amount": 128},
         "debug": {"breakpoints": [{"node_id": "tool_call-1"}]}},
        lambda item: "continue",
    )

    paused = _paused(frames)
    assert [f["node_id"] for f in paused] == ["trigger-1", "tool_call-1"]
    assert [f["reason"] for f in paused] == ["step", "breakpoint"]
    assert [p["reason"] for p in projections] == ["step", "breakpoint"]
    assert event_names[-1] == "result"


def test_i17_debug_conditional_breakpoint_true_and_false_paths():
    expression = "{{trigger-1.context.payload.amount}} > 1000"

    def run(amount):
        graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
        return _drive_debug_stream(
            graph_id,
            {"inputs": {"order_id": f"DBG-{amount}", "reason": "测试", "amount": amount},
             "debug": {"breakpoints": [
                 {"node_id": "tool_call-1", "expression": expression}]}},
            lambda item: "continue",
        )

    _, hit_frames, _ = run(1500)
    hit = _paused(hit_frames)
    assert [f["node_id"] for f in hit] == ["trigger-1", "tool_call-1"]
    assert hit[1]["reason"] == "condition"

    _, miss_frames, _ = run(1)
    missed = _paused(miss_frames)
    assert [f["node_id"] for f in missed] == ["trigger-1"]
    assert "tool_call-1" in miss_frames[-1]["outputs"]


def test_i17_debug_stop_emits_stopped_frame_without_result():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    event_names, frames, projections = _drive_debug_stream(
        graph_id,
        {"inputs": {"order_id": "DBG-STOP", "reason": "测试", "amount": 128},
         "debug": {"breakpoints": [{"node_id": "trigger-1"}]}},
        lambda item: "stop",
    )

    assert "result" not in event_names
    assert event_names[-1] == "stopped"
    assert len(projections) == 1
    assert projections[0]["node_id"] == "trigger-1"
    assert frames[-1] == {"type": "stopped", "node_id": "trigger-1",
                         "reason": "user_stop"}


def test_i17_debug_get_endpoint_shape_404_and_409():
    assert client.post(
        "/api/debug/dbg-does-not-exist/resume", json={"action": "step"}
    ).status_code == 404

    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    _, frames, projections = _drive_debug_stream(
        graph_id,
        {"inputs": {"order_id": "DBG-409", "reason": "测试", "amount": 128},
         "debug": {"breakpoints": [{"node_id": "trigger-1"}]}},
        lambda item: "continue",
    )

    item = projections[0]
    assert set(item) == {"token", "node_id", "node_type", "graph_id", "reason"}
    assert item["node_id"] == "trigger-1"
    assert item["node_type"] == "trigger"
    assert item["graph_id"] == graph_id

    token = _paused(frames)[0]["token"]
    conflict = client.post(f"/api/debug/{token}/resume", json={"action": "stop"})
    assert conflict.status_code == 409
    assert client.get("/api/debug").json()["items"] == []


def test_i17_debug_422_unknown_node_bad_expression_empty_and_sync_run():
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]

    unknown = client.post(
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {}, "debug": {"breakpoints": [{"node_id": "missing-1"}]}},
    )
    assert unknown.status_code == 422
    assert "断点节点不存在" in unknown.json()["detail"]

    bad_expr = client.post(
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {}, "debug": {"breakpoints": [
            {"node_id": "trigger-1", "expression": "{{trigger-1.("}]}},
    )
    assert bad_expr.status_code == 422
    assert "表达式" in bad_expr.json()["detail"]

    empty = client.post(
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {}, "debug": {"breakpoints": "not-a-list"}},
    )
    assert empty.status_code == 422

    sync = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {}, "debug": {"breakpoints": [{"node_id": "trigger-1"}]}},
    )
    assert sync.status_code == 422
    assert "/run/stream" in sync.json()["detail"]


def test_i17_debug_reset_releases_paused_run_and_new_run_works():
    import threading

    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    pending = threading.Event()
    watcher_done = threading.Event()
    stream_errors: list[Exception] = []

    def watch_pending() -> None:
        with TestClient(app, headers=DEFAULT_AUTH_HEADER) as ctl:
            while not watcher_done.is_set():
                if ctl.get("/api/debug").json()["items"]:
                    pending.set()
                    return
                time.sleep(0.01)

    def paused_stream() -> None:
        try:
            with client.stream(
                "POST",
                f"/api/graphs/{graph_id}/run/stream",
                json={"inputs": {"order_id": "DBG-RESET", "reason": "测试",
                                 "amount": 128},
                      "debug": {"breakpoints": [{"node_id": "trigger-1"}]}},
            ) as response:
                names = [
                    line for line in response.iter_lines()
                    if line.startswith("event:")
                ]
            assert names[-1] == "event: stopped"
        except Exception as exc:  # noqa: BLE001 - 主线程断言
            stream_errors.append(exc)

    watcher = threading.Thread(target=watch_pending, daemon=True)
    stream_thread = threading.Thread(target=paused_stream, daemon=True)
    watcher.start()
    stream_thread.start()
    assert pending.wait(timeout=5)

    client.post("/api/demo/reset")
    stream_thread.join(timeout=5)
    watcher_done.set()
    watcher.join(timeout=5)
    assert not stream_thread.is_alive(), "reset 后调试流线程悬挂"
    assert stream_errors == []

    new_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    with client.stream(
        "POST", f"/api/graphs/{new_id}/run/stream",
        json={"inputs": {"order_id": "AFTER-RESET", "reason": "测试", "amount": 128}},
    ) as response:
        events = [line for line in response.iter_lines() if line.startswith("event:")]
    assert events[-1] == "event: result"


# ---------------------------------------------------------------------------
# I18：基础监控告警 —— metrics/runs/rules/alerts 端点与两个运行入口埋点
# （04 §5.13；每个用例先 _monitoring.reset()，与套件中其他运行隔离）
# ---------------------------------------------------------------------------

from atlas.iam.deps import tenant_registry  # noqa: E402
_monitoring = tenant_registry.get("t1").monitoring


def _sql_template_graph() -> dict:
    return client.get("/api/templates/sql-query-notify").json()["graph"]


def _stream_run(graph_id: str, payload: dict | None = None):
    """非 debug 流式运行，返回 (event_names, data_frames)。"""
    event_names: list[str] = []
    data_frames: list[dict] = []
    pending_event = None
    with client.stream("POST", f"/api/graphs/{graph_id}/run/stream", json=payload or {}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event:"):
                pending_event = line[len("event:"):].strip()
                event_names.append(pending_event)
            elif line.startswith("data:"):
                data_frames.append(json.loads(line[len("data:"):].strip()))
    return event_names, data_frames


def _alerts_by_rule(status: str | None = None) -> dict[str, dict]:
    return {a["rule_id"]: a for a in client.get(
        "/api/alerts" + (f"?status={status}" if status else "")
    ).json()["items"]}


def test_i18_sync_healthy_run_metrics_and_run_projection():
    client.post("/api/demo/reset")  # 重新播种待退款订单
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12345", "reason": "监控健康用例", "amount": 128}},
    )
    assert response.status_code == 200

    metrics = client.get("/api/monitoring/metrics").json()
    assert metrics["total"] == 1 and metrics["healthy"] == 1 and metrics["unhealthy"] == 0
    assert metrics["success_rate"] == 1.0
    assert metrics["p50"] is not None and metrics["p95"] is not None
    assert metrics["per_graph"][0]["graph_id"] == graph_id

    runs = client.get(f"/api/monitoring/runs?graph_id={graph_id}").json()["items"]
    assert len(runs) == 1
    run = runs[0]
    assert run["graph_id"] == graph_id and run["mode"] == "sync" and run["status"] == "completed"
    assert run["nodes"] and all(node["status"] == "success" for node in run["nodes"])
    assert run["error"] is None and run["duration_ms"] >= 0
    assert client.get("/api/alerts").json()["items"] == []


def test_i18_stream_failed_node_creates_node_failed_alert():
    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    event_names, frames = _stream_run(graph_id, {"inputs": {}})
    assert event_names[-1] == "result"

    runs = client.get("/api/monitoring/runs").json()["items"]
    run = runs[0]
    assert run["mode"] == "stream" and run["status"] == "completed"
    failed = [node for node in run["nodes"] if node["status"] == "failed"]
    assert [node["node_id"] for node in failed] == ["query-1"]
    assert failed[0]["node_type"] == "tool_call" and failed[0]["error"]

    alerts = _alerts_by_rule()
    assert "node_failed" in alerts
    alert = alerts["node_failed"]
    assert alert["severity"] == "warning" and alert["status"] == "open"
    assert alert["count"] == 1 and alert["last_run_id"] == run["id"]
    assert "query-1" in alert["message"]


def test_i18_sync_and_stream_error_runs_record_run_error(monkeypatch):
    import atlas.api.main as main

    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]

    def boom(*args, **kwargs):
        raise RuntimeError("监控异常用例 boom")

    monkeypatch.setattr(main, "run_graph", boom)

    with pytest.raises(RuntimeError):  # 同步入口异常照常冒泡（TestClient 外为 500）
        client.post(f"/api/graphs/{graph_id}/run", json={})

    event_names, frames = _stream_run(graph_id)
    assert event_names[-1] == "error"
    assert "RuntimeError" in frames[-1]["detail"]

    runs = client.get("/api/monitoring/runs").json()["items"]
    assert {run["status"] for run in runs} == {"error"}
    assert {run["mode"] for run in runs} == {"sync", "stream"}
    assert all("RuntimeError: 监控异常用例 boom" in run["error"] for run in runs)
    alerts = _alerts_by_rule()
    assert "run_error" in alerts and alerts["run_error"]["severity"] == "critical"


def test_i18_consecutive_failures_merge_then_healthy_resets_streak():
    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    failed_ids: list[str] = []
    for _ in range(3):
        response = client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
        assert response.status_code == 200
        latest = client.get(f"/api/monitoring/runs?graph_id={graph_id}").json()["items"][0]["id"]
        failed_ids.append(latest)

    alerts = _alerts_by_rule()
    assert alerts["consecutive_failures"]["severity"] == "critical"
    assert alerts["consecutive_failures"]["count"] == 1
    assert alerts["node_failed"]["count"] == 3
    assert alerts["node_failed"]["last_run_id"] == failed_ids[-1]

    # 同图一次健康运行重置 streak：再连续失败 threshold-1 次不产生新连续告警
    assert client.post(
        f"/api/graphs/{graph_id}/run", json={"inputs": {"min_amount": 0}}
    ).status_code == 200
    for _ in range(2):
        client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
    consecutive = [
        a for a in client.get("/api/alerts").json()["items"]
        if a["rule_id"] == "consecutive_failures"
    ]
    assert len(consecutive) == 1


def test_i18_runs_filter_limit_and_alerts_status_filter():
    client.post("/api/demo/reset")  # 重新播种 + 清空监控与图
    bad = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    good = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    client.post(f"/api/graphs/{bad}/run", json={"inputs": {}})
    client.post(
        f"/api/graphs/{good}/run",
        json={"inputs": {"order_id": "12345", "reason": "x", "amount": 64}},
    )

    only_good = client.get(f"/api/monitoring/runs?graph_id={good}").json()["items"]
    assert [run["graph_id"] for run in only_good] == [good]
    limited = client.get("/api/monitoring/runs?limit=1").json()["items"]
    assert len(limited) == 1
    bad_limit = client.get("/api/monitoring/runs?limit=999")
    assert bad_limit.status_code == 422 and "limit" in bad_limit.json()["detail"]

    open_alerts = client.get("/api/alerts?status=open").json()["items"]
    assert open_alerts and all(a["status"] == "open" for a in open_alerts)
    bad_status = client.get("/api/alerts?status=closed")
    assert bad_status.status_code == 422


def test_i18_rules_get_put_validation_and_threshold_takes_effect():
    _monitoring.reset()
    defaults = client.get("/api/monitoring/rules").json()
    assert defaults["consecutive_failures"]["threshold"] == 3
    assert defaults["failure_rate"] == {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5}

    invalid = client.put("/api/monitoring/rules", json={
        "run_error": {"enabled": "yes"},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 0},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 2},
    })
    assert invalid.status_code == 422
    detail = invalid.json()["detail"]
    assert "run_error.enabled" in detail and "threshold" in detail and "rate" in detail

    saved = client.put("/api/monitoring/rules", json={
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 1},
        "failure_rate": {"enabled": False, "window": 20, "min_samples": 5, "rate": 0.5},
    })
    assert saved.status_code == 200
    graph_id = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
    rule_ids = {a["rule_id"] for a in client.get("/api/alerts").json()["items"]}
    assert "node_failed" not in rule_ids  # 规则已关闭
    assert "consecutive_failures" in rule_ids  # 阈值改为 1，首次失败即触发


def test_i18_alert_acknowledge_resolve_404_and_409():
    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
    alert_id = client.get("/api/alerts?status=open").json()["items"][0]["id"]

    assert client.post(f"/api/alerts/alt-missing/acknowledge").status_code == 404
    acked = client.post(f"/api/alerts/{alert_id}/acknowledge")
    assert acked.status_code == 200 and acked.json()["status"] == "acknowledged"
    assert client.post(f"/api/alerts/{alert_id}/acknowledge").status_code == 409

    assert client.post("/api/alerts/alt-missing/resolve").status_code == 404
    resolved = client.post(f"/api/alerts/{alert_id}/resolve")
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert client.post(f"/api/alerts/{alert_id}/resolve").status_code == 409
    assert client.get("/api/alerts?status=open").json()["items"] == []


def test_i18_demo_reset_clears_monitoring_and_restores_rules():
    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_sql_template_graph()).json()["id"]
    client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
    client.put("/api/monitoring/rules", json={
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 9},
        "failure_rate": {"enabled": True, "window": 10, "min_samples": 3, "rate": 0.9},
    })
    assert client.get("/api/monitoring/metrics").json()["total"] == 1

    assert client.post("/api/demo/reset").status_code == 200
    assert client.get("/api/monitoring/metrics").json()["total"] == 0
    assert client.get("/api/monitoring/runs").json()["items"] == []
    assert client.get("/api/alerts").json()["items"] == []
    rules = client.get("/api/monitoring/rules").json()
    assert rules["node_failed"]["enabled"] is True
    assert rules["consecutive_failures"]["threshold"] == 3


def test_i18_debug_run_is_not_recorded():
    _monitoring.reset()
    graph_id = client.post("/api/graphs", json=_refund_graph()).json()["id"]
    event_names, _, _ = _drive_debug_stream(
        graph_id,
        {"inputs": {"order_id": "MON-DBG", "reason": "调试不记录", "amount": 128},
         "debug": {"breakpoints": [{"node_id": "trigger-1"}]}},
        lambda item: "stop",
    )
    assert event_names[-1] == "stopped"
    assert client.get("/api/monitoring/runs").json()["items"] == []
    assert client.get("/api/monitoring/metrics").json()["total"] == 0


def test_recording_inlines_subgraph_snapshot_for_replay_after_child_changes():
    """D26：录制时冻结 subgraph 快照；子图事后被改坏，单用例回放仍走内联成功（gate 仍验实时）。"""
    client.post("/api/demo/reset")
    child_id = client.post("/api/graphs", json=_subgraph_child_graph()).json()["id"]
    parent_id = client.post("/api/graphs", json=_subgraph_parent_graph(child_id)).json()["id"]

    created = client.post("/api/recordings", json={
        "name": "子图内联回放",
        "graph_id": parent_id,
        "inputs": {"order_id": "X-9"},
        "steps": [{"node_id": "trigger-1", "node_type": "trigger", "output": {}}],
        "status": "completed",
    })
    assert created.status_code == 201
    case_id = created.json()["id"]

    detail = client.get(f"/api/recordings/{case_id}").json()
    assert child_id in detail["subgraphs"]
    assert {"child-trigger", "child-tool"} <= {n["id"] for n in detail["subgraphs"][child_id]["nodes"]}

    # 子图事后被改成「引用不存在孙图」的坏草稿（结构合法、编译期 check_refs 失败）
    # 结构合法（可保存），但编译期 check_refs 会因子图内模板引用不存在节点而 422
    broken_child = {
        "version": 1, "variables": [],
        "nodes": [
            {"id": "child-trigger", "type": "trigger", "name": "子图触发",
             "config": {"triggerType": "manual"}},
            {"id": "child-tool", "type": "tool_call", "name": "子图工具",
             "config": {"tool": "op-child",
                        "params": json.dumps({"x": "{{ghost-node.result.x}}"})}},
        ],
        "edges": [{"id": "ce1", "source": "child-trigger", "target": "child-tool"}],
    }
    assert client.put(f"/api/graphs/{child_id}", json=broken_child).status_code == 200

    # 对照：实时路径（编译）因子图已坏而 422
    assert client.post(f"/api/graphs/{parent_id}/compile").status_code == 422

    # 录制回放走内联冻结的好子图，仍 completed（不被事后改动影响）
    replay = client.post(f"/api/recordings/{case_id}/replay")
    assert replay.status_code == 200
    assert replay.json()["replay_status"] == "completed"
    client.post("/api/demo/reset")

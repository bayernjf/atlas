# -*- coding: utf-8 -*-
"""D26 打包 AH：影子运行 SSE 流式化（docs/114；U1317–U1320）。

验证新端点 ``POST /api/graphs/{graph_id}/shadow-runs/stream``：
- 实时下发 node_start/node_end，终帧 event:result 携带完整 ShadowRun，并在 shadow_store 沉淀；
- 影子纪律：不写 RunRecord（监控 runs 不增）、店铺零变化、写意图短路；
- run_graph 异常仍沉淀 status=error 记录、HTTP 不报错（不设 event:error）；
- 鉴权：未登录 401、viewer 403、未知图 404。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)


def _refund_graph_dict() -> dict:
    return {
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


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        event = next((ln[7:] for ln in lines if ln.startswith("event: ")), None)
        data_line = next((ln[6:] for ln in lines if ln.startswith("data: ")), None)
        if event is None or data_line is None:
            continue
        frames.append((event, json.loads(data_line)))
    return frames


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


def _create_refund_graph() -> str:
    return client.post("/api/graphs", json=_refund_graph_dict()).json()["id"]


def _monitoring_run_count() -> int:
    return len(client.get("/api/monitoring/runs").json().get("items", []))


# ---------- U1317 完成流：节点帧 + 终帧 + store 沉淀一致 ----------


def test_shadow_stream_emits_nodes_and_result_with_persistent_record():
    graph_id = _create_refund_graph()
    response = client.post(
        f"/api/graphs/{graph_id}/shadow-runs/stream",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299}},
    )
    assert response.status_code == 200, response.text
    frames = _parse_sse(response.text)
    event_names = [name for name, _ in frames]
    assert "node_start" in event_names and "node_end" in event_names
    assert event_names[-1] == "result"  # 终帧最后

    result = frames[-1][1]
    assert result["id"].startswith("sr-")
    assert result["graph_id"] == graph_id and result["status"] == "completed"
    assert result["auto_action"] == "refunded"

    # store 恰沉淀一条且与终帧内容一致
    items = client.get("/api/shadow-runs").json()["items"]
    assert len(items) == 1 and items[0]["id"] == result["id"]
    detail = client.get(f"/api/shadow-runs/{result['id']}").json()
    assert detail["tool_intents"] == result["tool_intents"]


# ---------- U1318 影子纪律：零 RunRecord、店铺零变化、写意图短路 ----------


def test_shadow_stream_keeps_zero_production_pollution():
    from atlas.api import main as main_module

    graph_id = _create_refund_graph()
    before_runs = _monitoring_run_count()
    before_status = main_module._demo_shop.orders["12345"].status

    response = client.post(
        f"/api/graphs/{graph_id}/shadow-runs/stream",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299}},
    )
    frames = _parse_sse(response.text)
    result = frames[-1][1]
    write_intents = [item for item in result["tool_intents"] if item["dry_run"]]
    assert len(write_intents) == 1
    assert write_intents[0]["action_status"] == "SHADOW_DRY_RUN"

    # 零污染：监控 RunRecord 不增、店铺状态不变
    assert _monitoring_run_count() == before_runs
    assert main_module._demo_shop.orders["12345"].status == before_status == "pending"


# ---------- U1319 run_graph 异常：终帧 status=error + store 沉淀 error ----------


def test_shadow_stream_records_error_when_run_graph_raises(monkeypatch):
    graph_id = _create_refund_graph()

    def _boom(*args, **kwargs):  # 模拟运行期异常
        raise RuntimeError("boom in shadow")

    monkeypatch.setattr("atlas.api.main.run_graph", _boom)
    response = client.post(
        f"/api/graphs/{graph_id}/shadow-runs/stream", json={"inputs": {}}
    )
    assert response.status_code == 200  # 影子异常不 HTTP 报错
    frames = _parse_sse(response.text)
    assert frames[-1][0] == "result"
    result = frames[-1][1]
    assert result["status"] == "error" and "RuntimeError" in result["error"]

    items = client.get("/api/shadow-runs").json()["items"]
    assert len(items) == 1 and items[0]["status"] == "error"


# ---------- U1320 鉴权：未登录 401、viewer 403、未知图 404 ----------


def test_shadow_stream_auth_matrix_and_404():
    graph_id = _create_refund_graph()

    # 登录会同时下发 httpOnly session cookie（TestClient 自动持久化），须头与 cookie 同清。
    client.headers.pop("authorization", None)
    client.cookies.clear()
    assert client.post(
        f"/api/graphs/{graph_id}/shadow-runs/stream", json={}
    ).status_code == 401

    viewer_token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {viewer_token}"
    assert client.post(
        f"/api/graphs/{graph_id}/shadow-runs/stream", json={}
    ).status_code == 403

    client.cookies.clear()
    admin_token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {admin_token}"
    assert client.post(
        "/api/graphs/graph-ghost/shadow-runs/stream", json={}
    ).status_code == 404

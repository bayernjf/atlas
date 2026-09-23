"""M5b 批 3：run 状态落库 + /api/runs 查询（docs/24 §3.3/§4，U45）。"""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from atlas.api.main import app

from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)


def _human_approval_graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批",
             "config": {
                 "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
                 "approver": "客服主管",
                 "timeoutSeconds": 10,
                 "onTimeout": "reject",
                 "approvedTarget": "tool-approve",
                 "rejectedTarget": "tool-reject",
             }},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


def test_completed_run_is_listed_and_queryable():
    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "30000", "approvals": {"human-1": "approved"}}},
    )
    assert response.status_code == 200

    items = client.get("/api/runs").json()["items"]
    assert items and items[0]["status"] == "completed"
    assert items[0]["graphId"] == graph_id

    run_id = items[0]["runId"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "completed"
    assert detail["outputs"]["human-1"]["decision"] == "approved"
    assert detail["suspension"] is None

    # 跨租户 404：t2 查不到 t1 的运行
    t2 = client.post("/api/auth/login", json={"username": "admin-b", "password": "admin123"}).json()
    t2_headers = {"Authorization": f"Bearer {t2['token']}"}
    assert client.get(f"/api/runs/{run_id}", headers=t2_headers).status_code == 404

    # 非法过滤值 422
    assert client.get("/api/runs", params={"status": "bogus"}).status_code == 422


def test_suspended_run_queryable_then_resumed():
    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]

    result: dict = {}

    def run_in_background() -> None:
        result["resp"] = client.post(
            f"/api/graphs/{graph_id}/run", json={"inputs": {"order_id": "30001"}}
        )

    thread = threading.Thread(target=run_in_background)
    thread.start()

    # 轮询挂起态（run 状态在 frame_sink 挂起时标 suspended）
    run_id = None
    token = None
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        items = client.get("/api/runs", params={"status": "suspended"}).json()["items"]
        if items:
            run_id = items[0]["runId"]
            token = items[0]["resumeToken"]
            break
        time.sleep(0.02)
    assert run_id is not None and token is not None

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "suspended"
    assert detail["suspension"]["nodeId"] == "human-1"
    assert detail["suspension"]["resumeToken"] == token

    # 同 token 决策 → 续跑完成
    decision = client.post(
        f"/api/approvals/{token}/decision", json={"decision": "approved", "comment": "同意"}
    )
    assert decision.status_code == 200
    thread.join(timeout=5)

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "completed"
    assert detail["outputs"]["human-1"]["decision"] == "approved"

    # reset 清空运行状态
    client.post("/api/demo/reset")
    assert client.get("/api/runs").json()["items"] == []


def test_wait_timeout_fail_returns_structured_500_and_runs_list_still_serves():
    # docs/54 收口：运行期 WaitNodeFailure 经全局 handler 返回结构化 500（不逃逸 ASGI 顶层
    # re-raise 关闭 keep-alive）；失败运行后紧邻的列表请求复用连接仍 200。
    client.post("/api/demo/reset")
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "event", "eventKey": "u540_never_fires",
                        "timeoutSeconds": 1, "onTimeout": "fail"}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }
    gid = client.post("/api/graphs", json=graph).json()["id"]
    resp = client.post(f"/api/graphs/{gid}/run", json={})
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "WAIT_TIMEOUT_FAILED"
    assert detail["nodeId"] == "wait-1"
    # 失败后紧邻请求复用同一连接仍可用（keep-alive 未被重置）
    assert client.get("/api/runs").status_code == 200
    items = client.get("/api/runs").json()["items"]
    assert next(r for r in items if r["graphId"] == gid)["status"] == "failed"
    client.post("/api/demo/reset")

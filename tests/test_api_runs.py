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
                 "approver": "",
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


def test_runs_projection_exposes_resolved_version():
    """O-2 修复：带 event 的路由 run，/api/runs 列表与详情投影暴露 resolvedVersion（canary 钉版）；
    无 event 的手动/草稿 run 恒 None（零回归）。"""
    client.post("/api/demo/reset")
    # 造两个发布版（v1/v2），再开 canary（stable=1/candidate=2）
    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/publish").status_code == 200
    from atlas.iam.deps import tenant_registry

    store = tenant_registry.get("t1").graph_store
    store._graphs[graph_id] = _human_approval_graph()  # 草稿换代（仅产生新版本号）
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 2
    client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "progressive", "rules": [{"to": "canary", "percent": 100}]},
    )
    started = client.post(f"/api/graphs/{graph_id}/rollout/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "canary" and started.json()["candidate"] == 2

    # 带 event → RoutingStore 解析 candidate=2，同步 run 完成后投影应暴露 2
    resp = client.post(
        f"/api/graphs/{graph_id}/run",
        json={
            "event": {"channel": "api", "payload": {"order_id": "70001", "amount": 999}},
            "inputs": {"order_id": "70001", "approvals": {"human-1": "approved"}},
        },
    )
    assert resp.status_code == 200, resp.text
    items = client.get("/api/runs").json()["items"]
    run = next(r for r in items if r["graphId"] == graph_id)
    assert run["resolvedVersion"] == 2
    detail = client.get(f"/api/runs/{run['runId']}").json()
    assert detail["resolvedVersion"] == 2

    # 无 event 手动 run：resolvedVersion 恒 None（零回归）
    gid2 = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]
    resp2 = client.post(
        f"/api/graphs/{gid2}/run",
        json={"inputs": {"order_id": "30002", "approvals": {"human-1": "approved"}}},
    )
    assert resp2.status_code == 200, resp2.text
    items2 = client.get("/api/runs").json()["items"]
    run2 = next(r for r in items2 if r["graphId"] == gid2)
    assert run2["resolvedVersion"] is None
    client.post("/api/demo/reset")


def test_sync_run_concurrency_guard_429():
    """O-5 护栏：同步 run 并发达上限（32）时 429 且不阻塞线程池；信号量释放后可恢复。"""
    from atlas.api.main import _sync_run_slots

    acquired = [_sync_run_slots.acquire(blocking=False) for _ in range(32)]
    assert all(acquired), "测试前置：应能占满同步 run 信号量"
    graph_id = client.post("/api/graphs", json=_human_approval_graph()).json()["id"]
    try:
        resp = client.post(
            f"/api/graphs/{graph_id}/run",
            json={"inputs": {"order_id": "30009", "approvals": {"human-1": "approved"}}},
        )
        assert resp.status_code == 429, resp.text
        assert "并发已达上限" in resp.json()["detail"]
    finally:
        for _ in acquired:
            _sync_run_slots.release()
    # 释放后可正常运行（护栏不误伤常规并发）
    resp2 = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "30009", "approvals": {"human-1": "approved"}}},
    )
    assert resp2.status_code == 200, resp2.text
    client.post("/api/demo/reset")


def test_sync_run_detached_executor_structure():
    """O-5 彻底解耦（docs/135 §6.9）结构回归：同步 run 端点必须保持 async 协程，
    run 在专用线程池执行（不占 uvicorn 默认线程池，挂起只占专用池线程）。
    防止未来回退成同步 def（挂起重新占用 uvicorn 线程池导致同步面被拖垮）。"""
    import inspect

    from atlas.api.main import _SYNC_RUN_POOL, run_saved_graph

    assert inspect.iscoroutinefunction(run_saved_graph), (
        "run_saved_graph 必须是 async 协程（O-5 彻底解耦结构约束）"
    )
    assert _SYNC_RUN_POOL._max_workers == 32, (
        f"专用 run 池容量应等于信号量上限 32，实际 {_SYNC_RUN_POOL._max_workers}"
    )

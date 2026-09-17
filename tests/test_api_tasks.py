"""M7 批 3：任务信封查询端点（docs/20 §4.2 / 12 任务端点）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)


def test_tasks_empty_list_and_detail_404():
    assert client.get("/api/tasks").json()["items"] == []
    assert client.get("/api/tasks/nope").status_code == 404


def test_task_dispatch_via_store_then_queryable():
    from atlas.iam.deps import tenant_registry

    store = tenant_registry.get("t1").task_store
    envelope, created = store.dispatch(
        run_id="run-1", idempotency_key="refund-1|verify|v1", trace_id="run-1",
        graph_version="return-flow@1", type="refund.verify_order", assignee="bot.customer",
        payload={"orderId": "12345"}, deadline_ms=30000,
    )
    assert created is True
    store.accept(envelope.taskId)
    store.start(envelope.taskId)
    store.complete(envelope.taskId, {"ok": True})

    items = client.get("/api/tasks").json()["items"]
    assert len(items) == 1
    assert items[0]["taskId"] == envelope.taskId
    assert items[0]["state"] == "done"
    assert items[0]["assignee"] == "bot.customer"

    detail = client.get(f"/api/tasks/{envelope.taskId}").json()
    assert detail["idempotencyKey"] == "refund-1|verify|v1"
    assert detail["state"] == "done"
    assert detail["result"] == {"ok": True}

    # assignee 过滤 + 非法 state 422 + 跨租户 404
    assert len(client.get("/api/tasks", params={"assignee": "bot.logistics"}).json()["items"]) == 0
    assert client.get("/api/tasks", params={"state": "bogus"}).status_code == 422
    t2 = client.post("/api/auth/login", json={"username": "admin-b", "password": "admin123"}).json()
    t2_headers = {"Authorization": f"Bearer {t2['token']}"}
    assert client.get(f"/api/tasks/{envelope.taskId}", headers=t2_headers).status_code == 404

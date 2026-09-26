# -*- coding: utf-8 -*-
"""定时调度的 REST 面与发布派生（docs/68 §4 U887–U891，打包 N）。

这里测的是"发布之后运营者看得见什么、改得动什么"：登记来自发布（不是另填一张表）、
重发布换版本但保留开关、`run-now` 不占槽位、越权与跨租户按现有 capability 口径。
派发判定本身（同槽一次/不补跑/重叠不消耗认领）在 `test_scheduling_engine.py`，
两档一致性在 `test_scheduling_pg_integration.py`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app, schedule_store
from atlas.iam.deps import session_store, tenant_registry

client = TestClient(app)


def _auth(username: str, password: str) -> dict[str, str]:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")

GRAPH_NAME = "定时调度 REST 冒烟"
ONE_MINUTE = timedelta(minutes=1)
FIVE_MINUTES = timedelta(minutes=5)


def _graph(cron: str = "*/5 * * * *", *, trigger_type: str = "schedule", name: str = GRAPH_NAME) -> dict:
    return {
        "version": 1,
        "name": name,
        "nodes": [
            {"id": "t1n", "type": "trigger", "name": "定时",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": trigger_type, "cron": cron}},
            {"id": "a1", "type": "ai_decision", "name": "决策",
             "position": {"x": 1, "y": 0}, "config": {"promptTemplate": "要不要退款"}},
        ],
        "edges": [{"id": "e1", "source": "t1n", "target": "a1"}],
    }


def _publish(body: dict) -> tuple[str, int]:
    created = client.post("/api/graphs", json=body, headers=ADMIN_A)
    assert created.status_code == 200, created.text
    graph_id = created.json()["id"]
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    assert published.status_code == 200, published.text
    return graph_id, int(published.json()["releaseVersion"])


@pytest.fixture()
def schedule_graph():
    """每个用例自己的图＋结束时清调度（store 是全局的，不清会串味）。"""
    graph_id, version = _publish(_graph())
    yield graph_id, version
    schedule_store().reset_tenant("t1")
    schedule_store().reset_tenant("t2")


def _find(graph_id: str, headers=None) -> dict | None:
    items = client.get("/api/schedules", headers=headers or ADMIN_A).json()["items"]
    return next((item for item in items if item["graphId"] == graph_id), None)


# --- U887 发布派生 --------------------------------------------------------

def test_u887_publishing_a_scheduled_graph_registers_it_with_the_publish_version(schedule_graph):
    graph_id, version = schedule_graph
    item = _find(graph_id)
    assert item is not None, "发布后 GET /api/schedules 里没有这条调度"
    assert item["version"] == version
    assert item["cron"] == "*/5 * * * *"
    assert item["enabled"] is True
    assert item["skipCount"] == 0 and item["lastFiredAt"] is None
    # 下次触发时刻是算出来的，不是配置项：`*/5` 在 `*/5` 的格点上，且不早于现在
    upcoming = datetime.fromisoformat(item["nextFireAt"])
    now = datetime.now(timezone.utc)
    assert upcoming.tzinfo is not None
    assert now - ONE_MINUTE <= upcoming <= now + FIVE_MINUTES * 2
    assert upcoming.minute % 5 == 0 and upcoming.second == 0


def test_u887b_publishing_a_graph_without_a_schedule_trigger_registers_nothing():
    graph_id, _ = _publish(_graph(trigger_type="manual"))
    assert _find(graph_id) is None


def test_u887c_republishing_without_the_trigger_revokes_the_schedule(schedule_graph):
    graph_id, _ = schedule_graph
    assert _find(graph_id) is not None
    body = _graph(trigger_type="manual")
    assert client.put(f"/api/graphs/{graph_id}", json=body, headers=ADMIN_A).status_code == 200
    assert client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A).status_code == 200
    assert _find(graph_id) is None, "定时节点已被撤掉，调度还挂在册上会对着老 cron 空跑"


# --- U888 重发布换版本、保留开关 ------------------------------------------

def test_u888_republish_updates_version_and_cron_but_keeps_the_switch(schedule_graph):
    graph_id, _ = schedule_graph
    assert client.post(
        f"/api/schedules/{graph_id}/enabled", json={"enabled": False}, headers=OPERATOR_A
    ).status_code == 200

    body = _graph(cron="15 3 * * *")
    assert client.put(f"/api/graphs/{graph_id}", json=body, headers=ADMIN_A).status_code == 200
    published = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN_A)
    version = int(published.json()["releaseVersion"])

    item = _find(graph_id)
    assert item["version"] == version and version >= 2
    assert item["cron"] == "15 3 * * *"
    assert item["enabled"] is False, "重发布把运营者的开关翻回来了"


# --- U889 run-now 不占槽位 ------------------------------------------------

def test_u889_run_now_starts_a_pinned_run_without_consuming_the_slot_claim(schedule_graph):
    graph_id, version = schedule_graph
    response = client.post(f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["graphId"] == graph_id and payload["version"] == version
    assert payload["runId"]

    services = tenant_registry.get("t1")
    run = services.run_store.get(payload["runId"])
    assert run is not None, "run-now 返回了 runId 但没有对应的 run"

    # 认领槽位必须还是空的：run-now 是"额外跑一次"，不是替本分钟交差。
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    assert schedule_store().claim("t1", graph_id, now) is True
    assert schedule_store().claim("t1", graph_id, now) is False, "认领表幂等性破了"


def test_u889b_run_now_is_409_while_the_graph_still_has_a_live_run(schedule_graph):
    graph_id, _ = schedule_graph
    services = tenant_registry.get("t1")
    services.run_store.begin(run_id="run-hanging", graph_id=graph_id, mode="schedule")
    try:
        response = client.post(f"/api/schedules/{graph_id}/run-now", headers=OPERATOR_A)
        assert response.status_code == 409
        assert "执行中" in response.json()["detail"]
    finally:
        services.run_store.finish(run_id="run-hanging", status="completed")


def test_u889c_unknown_graph_and_missing_schedule_are_404():
    assert client.post("/api/schedules/no-such-graph/run-now", headers=OPERATOR_A).status_code == 404
    assert client.post(
        "/api/schedules/no-such-graph/enabled", json={"enabled": True}, headers=OPERATOR_A
    ).status_code == 404


# --- U890 权限与租户分区 --------------------------------------------------

def test_u890_anonymous_is_401_viewer_cannot_mutate_and_tenant_b_sees_nothing(schedule_graph):
    graph_id, _ = schedule_graph
    assert client.get("/api/schedules").status_code == 401
    assert client.get("/api/schedules", headers=VIEWER_A).status_code == 200
    assert client.post(
        f"/api/schedules/{graph_id}/enabled", json={"enabled": False}, headers=VIEWER_A
    ).status_code == 403
    assert client.post(
        f"/api/schedules/{graph_id}/run-now", headers=VIEWER_A
    ).status_code == 403

    assert client.get("/api/schedules", headers=ADMIN_B).json()["items"] == []
    assert client.post(
        f"/api/schedules/{graph_id}/run-now", headers=ADMIN_B
    ).status_code == 404, "跨租户要按不存在处理（404），而不是把别人的图跑起来"


def test_u890b_demo_reset_clears_the_tenant_schedules(schedule_graph):
    graph_id, _ = schedule_graph
    assert _find(graph_id) is not None
    assert client.post("/api/demo/reset", headers=ADMIN_A).status_code == 200
    assert _find(graph_id) is None


# --- U891 保存期校验（与既有 REQUIRED 并存） -------------------------------

@pytest.mark.parametrize("cron,code", [
    ("", "NODE_TRIGGER_CRON_REQUIRED"),
    ("5/2 * * * *", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 32 * *", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 * * MON", "NODE_TRIGGER_CRON_INVALID"),
    ("0 0 30 2 *", "NODE_TRIGGER_CRON_INVALID"),
])
def test_u891_bad_cron_is_refused_at_save_time_with_a_precise_code(cron, code):
    response = client.post("/api/graphs", json=_graph(cron=cron), headers=ADMIN_A)
    assert response.status_code == 422, response.text
    body = response.json()
    assert code in body["codes"], body
    assert any("\u4e00" <= char <= "\u9fff" for char in "".join(body["detail"])), body


def test_u891b_a_valid_cron_still_saves():
    graph_id, _ = _publish(_graph(cron="0 9 * * 1-5"))
    assert _find(graph_id)["cron"] == "0 9 * * 1-5"

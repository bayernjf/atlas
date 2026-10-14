"""圈会话 REST 端点验收（docs/130 打包 BN＝D69；U1435 ＋ 停整条圈的 HTTP 语义）。

端点：`GET /api/runs/{run_id}/cycle`（read）、`POST /api/runs/{run_id}/cycle/stop`（operate）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.cycles import cycle_registry
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate

client = TestClient(app)


def _node(node_id: str, node_type: str, config: dict) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "description": "", "position": {"x": 0, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _graph(loop: dict | None = None) -> dict:
    body = {
        "version": 1,
        "nodes": [
            _node("trigger-1", "trigger", {"triggerType": "manual"}),
            _node("tool-1", "tool_call", {
                "tool": "message/send",
                "params": '{"channel":"email","to":["ops@example.com"],'
                          '"subject":"圈","body":"b"}',
            }),
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    if loop is not None:
        body["loop"] = loop
    return body


@pytest.fixture()
def admin():
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    client.headers["Authorization"] = f"Bearer {token}"
    cycle_registry().reset()
    yield {"Authorization": f"Bearer {token}"}
    cycle_registry().reset()
    session_store.revoke(token)


def _run_cycle(headers: dict, loop: dict) -> tuple[str, list[dict]]:
    saved = client.post("/api/graphs", json=_graph(loop), headers=headers)
    assert saved.status_code == 200, saved.text
    graph_id = saved.json()["id"]
    response = client.post(f"/api/graphs/{graph_id}/run", json={}, headers=headers)
    assert response.status_code == 200, response.text
    runs = client.get("/api/runs?limit=50", headers=headers).json()["items"]
    return graph_id, [r for r in runs if r["graphId"] == graph_id]


# --- U1435 投影读面 ------------------------------------------------------------


def test_u1435_cycle_projection_is_readable_after_a_cycle_session(admin):
    _graph_id, runs = _run_cycle(admin, {"maxIterations": 2, "completeWhen": "{{nope}} == 1"})
    assert len(runs) == 2
    last = runs[0]
    view = client.get(f"/api/runs/{last['runId']}/cycle", headers=admin)
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["groupId"] == last["cycleGroupId"]
    assert body["state"] == "budget_exhausted"
    assert body["stopReason"] == "maxIterations"
    assert body["seq"] == 2
    assert [r["seq"] for r in body["runs"]] == [1, 2]
    assert body["maxIterations"] == 2


def test_u1435_non_cycle_run_is_404(admin):
    saved = client.post("/api/graphs", json=_graph(None), headers=admin)
    graph_id = saved.json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/run", json={}, headers=admin).status_code == 200
    run_id = [
        r["runId"] for r in client.get("/api/runs?limit=50", headers=admin).json()["items"]
        if r["graphId"] == graph_id
    ][0]
    missing = client.get(f"/api/runs/{run_id}/cycle", headers=admin)
    assert missing.status_code == 404
    assert "圈会话" in missing.json()["detail"]


def test_u1435_unknown_run_is_404(admin):
    assert client.get("/api/runs/nope/cycle", headers=admin).status_code == 404


def test_u1435_anonymous_is_401():
    anon = TestClient(app)
    assert anon.get("/api/runs/whatever/cycle").status_code == 401
    assert anon.post("/api/runs/whatever/cycle/stop").status_code == 401


def test_cycle_projection_is_tenant_scoped(admin):
    """跨租户不得读到别租户的圈会话（同形 404，不泄漏存在性）。"""
    from atlas.iam.principals import authenticate as auth2

    _graph_id, runs = _run_cycle(admin, {"maxIterations": 1, "completeWhen": "{{nope}} == 1"})
    run_id = runs[0]["runId"]
    other = auth2("admin-b", "admin123")
    assert other is not None
    token = session_store.issue(other)
    try:
        response = client.get(
            f"/api/runs/{run_id}/cycle", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404
    finally:
        session_store.revoke(token)


# --- 停整条圈（C-D8）-----------------------------------------------------------


def test_stop_cycle_marks_live_session_stopped(admin):
    registry = cycle_registry()
    session = registry.create(
        tenant_id="t1", graph_id="g", max_iterations=5,
        max_wall_clock_seconds=600, max_tokens=1000,
    )
    # 造一条指向该会话的 run（投影端点靠 run→group 索引反查）
    services_runs = client  # 仅示意：真实用 run_store
    from atlas.iam.deps import tenant_registry

    run_store = tenant_registry.get("t1").run_store
    run_store.begin(
        run_id="r-live", graph_id="g", mode="sync",
        cycle_seq=1, cycle_group_id=session.group_id,
    )
    registry.bind_run("r-live", session.group_id)
    assert session.state == "running"

    stopped = client.post(f"/api/runs/r-live/cycle/stop", headers=admin)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["stopReason"] == "cycle_stopped"
    # 幂等：再停一次仍 200 且状态不变
    again = client.post(f"/api/runs/r-live/cycle/stop", headers=admin)
    assert again.status_code == 200 and again.json()["state"] == "stopped"
    assert services_runs is client       # 保持引用，避免误删


def test_stop_cycle_after_terminal_keeps_history(admin):
    _graph_id, runs = _run_cycle(admin, {"maxIterations": 1, "completeWhen": "{{nope}} == 1"})
    run_id = runs[0]["runId"]
    before = client.get(f"/api/runs/{run_id}/cycle", headers=admin).json()["state"]
    assert before == "budget_exhausted"
    after = client.post(f"/api/runs/{run_id}/cycle/stop", headers=admin)
    assert after.status_code == 200
    assert after.json()["state"] == before, "终态不可被 stop 覆写"


def test_stop_cycle_unknown_run_is_404(admin):
    assert client.post("/api/runs/nope/cycle/stop", headers=admin).status_code == 404


def test_stop_cycle_requires_operate(admin):
    from atlas.iam.principals import authenticate as auth2

    registry = cycle_registry()
    session = registry.create(
        tenant_id="t1", graph_id="g", max_iterations=5,
        max_wall_clock_seconds=600, max_tokens=1000,
    )
    from atlas.iam.deps import tenant_registry

    tenant_registry.get("t1").run_store.begin(
        run_id="r-viewer", graph_id="g", mode="sync",
        cycle_seq=1, cycle_group_id=session.group_id,
    )
    registry.bind_run("r-viewer", session.group_id)
    viewer = auth2("viewer-a", "viewer123")
    assert viewer is not None
    token = session_store.issue(viewer)
    try:
        response = client.post(
            "/api/runs/r-viewer/cycle/stop", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403
    finally:
        session_store.revoke(token)

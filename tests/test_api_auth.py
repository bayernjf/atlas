# -*- coding: utf-8 -*-
"""I19：登录/会话、端点级三角色 RBAC、租户分区与 reset 作用域（04 §5.14；13 I19）。

用自建匿名 TestClient 显式带不同种子用户的 Bearer；conftest 的 t1 admin
自动注入只作用于 test_api_demo/test_api_graphs 的模块级 client。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate

anon = TestClient(app)


def _token(username: str, password: str) -> str:
    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


def _auth(username: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(username, password)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")


def _minimal_graph() -> dict:
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "position": {"x": 0, "y": 0},
             "config": {"tool": "message/send"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "tool-1"},
        ],
    }


def test_public_endpoints_remain_open():
    assert anon.get("/api/health").status_code == 200
    mock = anon.get("/api/demo/mock/orders", headers={"X-Demo-Token": "demo-token"})
    assert mock.status_code == 200


def test_missing_invalid_and_revoked_token_401():
    assert anon.get("/api/graphs").status_code == 401
    assert anon.get("/api/graphs", headers={"Authorization": "Bearer sess-bad"}).status_code == 401
    assert anon.get("/api/graphs", headers={"Authorization": "Basic abc"}).status_code == 401

    token = _token("viewer-a", "viewer123")
    headers = {"Authorization": f"Bearer {token}"}
    assert anon.get("/api/graphs", headers=headers).status_code == 200
    session_store.revoke(token)
    assert anon.get("/api/graphs", headers=headers).status_code == 401


def test_login_me_logout_lifecycle():
    bad = anon.post("/api/auth/login", json={"username": "admin-a", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == {
        "code": "AUTH_INVALID_CREDENTIALS",
        "message": "用户名或密码错误",
    }

    login = anon.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["token"].startswith("sess-")
    assert body["principal"]["username"] == "viewer-a"
    assert body["principal"]["tenant_id"] == "t1"
    assert body["principal"]["tenant_name"] == "演示企业 A"
    assert body["principal"]["role"] == "viewer"

    headers = {"Authorization": f"Bearer {body['token']}"}
    me = anon.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["principal"]["display_name"] == "A 企业访客"

    assert anon.post("/api/auth/logout", headers=headers).status_code == 200
    assert anon.get("/api/auth/me", headers=headers).status_code == 401


def test_viewer_is_read_only_but_can_submit_feedback():
    assert anon.get("/api/graphs", headers=VIEWER_A).status_code == 200
    assert anon.post("/api/graphs", headers=VIEWER_A, json=_minimal_graph()).status_code == 403
    assert anon.post("/api/graphs/graph-1/run", headers=VIEWER_A).status_code == 403
    assert anon.put("/api/monitoring/rules", headers=VIEWER_A, json={}).status_code == 403
    assert anon.post("/api/demo/reset", headers=VIEWER_A).status_code == 403
    assert anon.get("/api/feedback", headers=VIEWER_A).status_code == 403

    feedback = anon.post(
        "/api/feedback",
        headers=VIEWER_A,
        json={"type": "suggestion", "content": "viewer 反馈 I19"},
    )
    assert feedback.status_code == 201


def test_operator_can_edit_and_run_but_not_admin_endpoints():
    saved = anon.post("/api/graphs", headers=OPERATOR_A, json=_minimal_graph())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]
    run = anon.post(f"/api/graphs/{graph_id}/run", headers=OPERATOR_A, json={"inputs": {}})
    assert run.status_code == 200
    assert run.json()["status"] == "completed"

    assert anon.put("/api/monitoring/rules", headers=OPERATOR_A, json={}).status_code == 403
    assert anon.post("/api/demo/reset", headers=OPERATOR_A).status_code == 403
    assert anon.get("/api/feedback", headers=OPERATOR_A).status_code == 403


def test_admin_only_endpoints_for_admin():
    rules = anon.get("/api/monitoring/rules", headers=ADMIN_A).json()
    rules["consecutive_failures"]["threshold"] = 5
    updated = anon.put("/api/monitoring/rules", headers=ADMIN_A, json=rules)
    assert updated.status_code == 200
    assert updated.json()["consecutive_failures"]["threshold"] == 5

    assert anon.get("/api/feedback", headers=ADMIN_A).status_code == 200


def test_graphs_are_partitioned_across_tenants():
    saved = anon.post("/api/graphs", headers=ADMIN_A, json=_minimal_graph())
    graph_id = saved.json()["id"]

    assert anon.get(f"/api/graphs/{graph_id}", headers=ADMIN_A).status_code == 200
    b_list = anon.get("/api/graphs", headers=ADMIN_B).json()["items"]
    assert b_list == []
    # 跨租户访问对象 → 404，不泄漏存在性
    assert anon.get(f"/api/graphs/{graph_id}", headers=ADMIN_B).status_code == 404


def test_cross_tenant_approval_token_is_404():
    broker = tenant_registry.get("t1").approval_broker
    token = broker.request(
        node_id="human-i19",
        graph_id="graph-i19",
        summary="I19 审批",
        approver="tester",
        timeout_seconds=30,
    )
    # t2 管理员看不到 t1 的审批 token
    blocked = anon.post(
        f"/api/approvals/{token}/decision",
        headers=ADMIN_B,
        json={"decision": "approved"},
    )
    assert blocked.status_code == 404
    # token 仍归 t1，t1 运营可正常决策
    ok = anon.post(
        f"/api/approvals/{token}/decision",
        headers=OPERATOR_A,
        json={"decision": "approved"},
    )
    assert ok.status_code == 200


def test_feedback_is_partitioned_and_only_admin_lists():
    marker = f"I19 分区反馈 {time.time()}"
    created = anon.post(
        "/api/feedback",
        headers=VIEWER_A,
        json={"type": "bug", "content": marker},
    )
    assert created.status_code == 201

    a_items = anon.get("/api/feedback", headers=ADMIN_A).json()["items"]
    assert any(item["content"] == marker for item in a_items)
    assert anon.get("/api/feedback", headers=ADMIN_B).json()["items"] == []


def test_monitoring_runs_and_rules_are_partitioned():
    monitoring = tenant_registry.get("t1").monitoring
    monitoring.record_run(
        graph_id="graph-i19-mon",
        mode="sync",
        status="error",
        started_at="2026-09-16T00:00:00+00:00",
        duration_ms=12.0,
        nodes=[],
        error="I19",
    )
    a_runs = anon.get("/api/monitoring/runs", headers=ADMIN_A).json()["items"]
    assert any(run["graph_id"] == "graph-i19-mon" for run in a_runs)
    assert anon.get("/api/monitoring/runs", headers=ADMIN_B).json()["items"] == []

    # t1 阈值改动对 t2 不可见（reset 用例会把 t1 规则恢复默认）
    rules_a = anon.get("/api/monitoring/rules", headers=ADMIN_A).json()
    rules_a["consecutive_failures"]["threshold"] = 4
    anon.put("/api/monitoring/rules", headers=ADMIN_A, json=rules_a)
    rules_b = anon.get("/api/monitoring/rules", headers=ADMIN_B).json()
    assert rules_b["consecutive_failures"]["threshold"] != 4


def test_reset_scopes_to_caller_tenant_but_keeps_recordings_and_feedback():
    saved = anon.post("/api/graphs", headers=ADMIN_A, json=_minimal_graph())
    a_graph = saved.json()["id"]
    recording = anon.post(
        "/api/recordings",
        headers=ADMIN_A,
        json={
            "name": "I19 reset 保留用例",
            "graph_id": a_graph,
            "inputs": {},
            "steps": [{"node_id": "trigger-1", "node_type": "trigger", "output": {}}],
            "status": "completed",
        },
    )
    assert recording.status_code == 201
    case_id = recording.json()["id"]
    marker = f"I19 reset 保留反馈 {time.time()}"
    anon.post(
        "/api/feedback", headers=ADMIN_A, json={"type": "bug", "content": marker}
    )

    b_graph = anon.post("/api/graphs", headers=ADMIN_B, json=_minimal_graph()).json()["id"]

    assert anon.post("/api/demo/reset", headers=ADMIN_A).status_code == 200

    assert anon.get("/api/graphs", headers=ADMIN_A).json()["items"] == []
    assert anon.get(f"/api/recordings/{case_id}", headers=ADMIN_A).status_code == 200
    a_feedback = anon.get("/api/feedback", headers=ADMIN_A).json()["items"]
    assert any(item["content"] == marker for item in a_feedback)
    # t2 数据不受 t1 reset 影响
    b_items = anon.get("/api/graphs", headers=ADMIN_B).json()["items"]
    assert any(item["id"] == b_graph for item in b_items)
    # 规则恢复默认（threshold 回到 3）
    rules = anon.get("/api/monitoring/rules", headers=ADMIN_A).json()
    assert rules["consecutive_failures"]["threshold"] == 3
# ============================ J-1c 种子口令 prod 拒绝 ============================


def test_login_rejects_seed_credential_in_prod(monkeypatch):
    """J-1c：prod 下种子默认口令登录 → 403 AUTH_SEED_CREDENTIAL。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    resp = anon.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "AUTH_SEED_CREDENTIAL"


def test_login_allows_custom_credential_in_prod(monkeypatch):
    """J-1c：prod 下改密后的账号用新口令照常登录。"""
    from atlas.iam.accounts import Role
    from atlas.iam.deps import user_store

    user_store.create(
        tenant_id="t1", username="ops-prod", password="Str0ng!-9",
        display_name="生产运营", role=Role.OPERATOR,
    )
    monkeypatch.setenv("ATLAS_ENV", "prod")
    resp = anon.post("/api/auth/login", json={"username": "ops-prod", "password": "Str0ng!-9"})
    assert resp.status_code == 200


def test_login_allows_seed_credential_outside_prod(monkeypatch):
    """J-1c：非 prod 种子口令登录行为不变。"""
    monkeypatch.delenv("ATLAS_ENV", raising=False)
    resp = anon.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    assert resp.status_code == 200

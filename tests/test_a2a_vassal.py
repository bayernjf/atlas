"""A2A 执行 Agent 面（docs/90，ADR T32；Zeus 联邦 W2 接入第一阶段）。

形状对齐 loom backend/tests/unit/test_a2a_vassal.py：rpc 纯函数 + 卡片契约
+ 任务端点 Bearer 闸门。plan-only，不触链/不起 run/不花 token。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from atlas.a2a.card import FEALTY, SKILLS, build_agent_card
from atlas.a2a.rpc import handle_jsonrpc, reset_for_tests
from atlas.api.main import app


def _user_message(skill: str, params: dict | None = None, run_id: str | None = "run-1") -> dict:
    metadata = {"x-zeus-runId": run_id} if run_id else None
    return {
        "role": "user",
        **({"metadata": metadata} if metadata else {}),
        "parts": [{"kind": "data", "data": {"skill": skill, **(params or {})}}],
    }


@pytest.fixture(autouse=True)
def _clear_tasks():
    reset_for_tests()
    yield
    reset_for_tests()


# --- card ---

def test_card_carries_fealty_and_plan_only_skills():
    card = build_agent_card()
    assert card["name"] == "atlas"
    assert card["preferredTransport"] == "JSONRPC"
    assert {s["id"] for s in card["skills"]} == {"plan-approval-flow", "diagnose-run"}
    fealty = card["x-zeus-fealty"]
    assert fealty["swornTo"] == "zeus"
    assert fealty["version"] == FEALTY["version"] == "1"
    assert fealty["dataRealms"] == ["enterprise"]
    assert fealty["dataPolicy"] == "read-task-scope"
    assert fealty["escalationPolicy"] == "auto"
    # Every advertised skill is plan-only by construction.
    assert all("plan-only" in s["tags"] for s in SKILLS)


def test_card_url_resolves_from_public_base_url(monkeypatch):
    monkeypatch.setenv("ATLAS_PUBLIC_BASE_URL", "https://atlas.example.com/")
    assert build_agent_card()["url"] == "https://atlas.example.com/api/a2a/tasks"


# --- rpc ---

def test_send_completed_plan_returns_report():
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
         "params": {"message": _user_message("plan-approval-flow", {"tenant_id": "acme", "flow_name": "leave"})}}
    )
    task = resp["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "completed"
    artifact = task["artifacts"][0]
    assert artifact["x-zeus-report"]["cost"]["llmTokens"] == 0
    assert "plan-only" in " ".join(artifact["x-zeus-report"]["evidence"])
    assert artifact["parts"][0]["data"]["skill"] == "plan-approval-flow"


def test_missing_required_params_is_input_required_not_error():
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "tasks/send",
         "params": {"message": _user_message("diagnose-run", {"tenant_id": "acme"})}}
    )
    assert resp["result"]["status"]["state"] == "input-required"


def test_unknown_skill_fails_and_unknown_method_is_32601():
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/send",
         "params": {"message": _user_message("nope")}}
    )
    assert resp["result"]["status"]["state"] == "failed"
    bad = handle_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "tasks/nuke", "params": {}})
    assert bad["error"]["code"] == -32601


def test_subscribe_emits_lifecycle_events_and_final_snapshot():
    events: list[dict] = []
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 5, "method": "tasks/sendSubscribe",
         "params": {"message": _user_message("plan-approval-flow", {"tenant_id": "t", "flow_name": "f"})}},
        on_event=events.append,
    )
    states = [e["status"]["state"] for e in events if e["kind"] == "status-update"]
    assert states == ["submitted", "working", "completed"]
    assert events[-1]["final"] is True
    assert any(e["kind"] == "artifact-update" for e in events)
    assert resp["result"]["status"]["state"] == "completed"


def test_get_and_cancel_lifecycle():
    created = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 6, "method": "tasks/send",
         "params": {"message": _user_message("plan-approval-flow", {"tenant_id": "t", "flow_name": "f"})}}
    )["result"]
    got = handle_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "tasks/get", "params": {"id": created["id"]}})
    assert got["result"]["id"] == created["id"]
    # Completed task is not cancelable.
    cancel = handle_jsonrpc({"jsonrpc": "2.0", "id": 8, "method": "tasks/cancel", "params": {"id": created["id"]}})
    assert cancel["error"]["code"] == -32002
    missing = handle_jsonrpc({"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": {"id": "nope"}})
    assert missing["error"]["code"] == -32001


def test_message_without_data_part_is_invalid_params():
    resp = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 10, "method": "tasks/send",
         "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}}}
    )
    assert resp["error"]["code"] == -32602


# --- HTTP face ---

def test_well_known_and_card_endpoints_are_public():
    client = TestClient(app)
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json", "/api/a2a/agent-card"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["x-zeus-fealty"]["swornTo"] == "zeus"


def test_task_endpoint_requires_bearer_when_token_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_A2A_TASK_TOKEN", "secret")
    client = TestClient(app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
            "params": {"message": _user_message("plan-approval-flow", {"tenant_id": "t", "flow_name": "f"})}}
    assert client.post("/api/a2a/tasks", json=body).status_code == 401
    ok = client.post("/api/a2a/tasks", json=body, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert ok.json()["result"]["status"]["state"] == "completed"


def test_subscribe_http_is_sse_framed(monkeypatch):
    monkeypatch.delenv("ATLAS_A2A_TASK_TOKEN", raising=False)
    client = TestClient(app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/sendSubscribe",
            "params": {"message": _user_message("diagnose-run", {"tenant_id": "t", "run_id": "r"})}}
    r = client.post("/api/a2a/tasks", json=body)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    # Final frame is the whole JSON-RPC response whose result is the task.
    assert frames[-1]["result"]["kind"] == "task"
    assert frames[-1]["result"]["status"]["state"] == "completed"

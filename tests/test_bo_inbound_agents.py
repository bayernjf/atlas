# -*- coding: utf-8 -*-
"""打包 BO（docs/131 D73＋D74）：入站会话触发＋可管理 agent 面的 REST 测试。

用例映射：
- U1443  message_inbound 触发器图编译期合法（无额外必填，可保存可发布）；
- U1444  验签失败 401（配预共享 secret 时）；重复消息幂等（duplicate，不二次起 run）；
- U1445  映射命中 ⇒ 起 run（mode="message_inbound"，runId 真实可查、带 agentId）；
          未命中 ⇒ 死信（不静默吞）；
- U1446  图配 loop ⇒ 入站触发的 run 自动进入循环（D69＋D73 协同）；
- U1447  触发失败进 DLQ，可重放/删除/指标（复用 deliveries 范式）；
- U1448  回写会话：run 记录落会话视图（假供应商，进程内）；
- U1449  agent 维度：/api/agents CRUD＋按 agentId 看执行日记与 token 汇总（走 AU 账）；
- U1450  多轮只透传 conversationId（不做摘要）；
- U1451  反向门：摘掉去重 ⇒ 同消息二次起 run（红，防"去重只是摆设"）；
- U1452  反向门：摘掉映射命中 ⇒ 消息进死信而非静默吞（红）；
- U1454  真机端到端（假供应商腿）：入站 → 映射命中 → 起 run → 回写会话。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)

INBOUND_SECRET = "inbound-shared-secret"

CONN_BODY = {
    "provider": "generic",
    "displayName": "入站测试连接",
    "authUrl": "https://sso.example.com/auth",
    "tokenUrl": "https://sso.example.com/token",
    "clientId": "cid-inbound",
    "clientSecret": "shpss_inbound-secret",
    "scopes": ["read"],
}


class _AllowEgress:
    def check(self, url: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _setup():
    import atlas.connections.service as cs
    from atlas.iam.deps import tenant_registry

    cs._egress_guard = _AllowEgress()
    admin = _login("admin")
    client.post("/api/demo/reset", headers=admin)
    services = tenant_registry.get("t1")
    services.connection_service._egress = _AllowEgress()
    for item in services.connection_service.list():
        services.connection_service.delete(item["id"])
    for item in services.channel_registry.list():
        services.channel_registry.delete(item["id"])
    for a in services.agents_store.list("t1"):
        services.agents_store.delete("t1", a.id)
    rows = getattr(services.inbound_deliveries, "_rows", None)
    if rows is not None:
        rows.clear()
    sessions = getattr(services.inbound_sessions, "_sessions", None)
    if sessions is not None:
        sessions.clear()
    yield
    client.headers.pop("authorization", None)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_binding(headers, secret: str | None = None) -> str:
    cid = client.post("/api/connections", json=CONN_BODY, headers=headers).json()["id"]
    config: dict = {"shop": "acme"}
    if secret is not None:
        # D-D1 可插拔 verify：预共享 secret 经 binding.config 配置（bind 保留自定义键）。
        config["inbound_secret"] = secret
    bid = client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": cid, "config": config},
        headers=headers,
    ).json()["id"]
    return bid


def _sign(body: bytes, secret: str = INBOUND_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _node(node_id: str, node_type: str, config: dict) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "description": "", "position": {"x": 0, "y": 0},
        "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _graph(loop: dict | None = None) -> dict:
    body = {
        "version": 1,
        "nodes": [
            _node("trigger-1", "trigger", {"triggerType": "message_inbound"}),
            _node("tool-1", "tool_call", {
                "tool": "message/send",
                "params": '{"channel":"email","to":["ops@example.com"],'
                          '"subject":"入站跑了一圈","body":"done"}',
            }),
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    if loop is not None:
        body["loop"] = loop
    return body


def _save_graph(headers, loop: dict | None = None) -> str:
    saved = client.post("/api/graphs", json=_graph(loop), headers=headers)
    assert saved.status_code == 200, saved.text
    return saved.json()["id"]


def _publish_and_pin(headers, graph_id: str) -> None:
    """发布并直接置 full 钉版态（绕过 canary 状态机；它要求两个发布版本）。"""
    from atlas.iam.deps import tenant_registry
    from atlas.routing.store import RolloutState

    r = client.post(f"/api/graphs/{graph_id}/publish", headers=headers)
    assert r.status_code == 200, r.text
    routing = tenant_registry.get("t1").routing_store
    routing._states[graph_id] = RolloutState(graph_id=graph_id, status="full", candidate=1)


def _create_agent(headers, name: str = "入站分身") -> str:
    r = client.post("/api/agents", json={"name": name}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _put_mapping(headers, bid: str, conversation_id: str, graph_id: str,
                 agent_id: str | None = None) -> None:
    mapping = {"conversationId": conversation_id, "graphId": graph_id, "enabled": True}
    if agent_id is not None:
        mapping["agentId"] = agent_id
    r = client.put(
        f"/api/channels/{bid}/inbound-mappings", json={"mappings": [mapping]}, headers=headers
    )
    assert r.status_code == 200, r.text


def _post_inbound(bid: str, body: dict, secret: str | None = None):
    import json as _json

    raw = _json.dumps(body).encode()
    headers = {}
    if secret is not None:
        headers["X-Inbound-Signature"] = _sign(raw, secret)
    return client.post(f"/api/channels/inbound/{bid}", content=raw, headers=headers)


def _msg(message_id: str, conversation_id: str = "conv-1", text: str = "你好") -> dict:
    return {
        "messageId": message_id,
        "conversationId": conversation_id,
        "channel": "fake",
        "data": {"text": text},
    }


def _run_store():
    from atlas.iam.deps import tenant_registry

    return tenant_registry.get("t1").run_store


def _wait_terminal(run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = _run_store().get(run_id)
        if run is not None and run["status"] not in {"running", "suspended"}:
            return run
        time.sleep(0.03)
    pytest.fail(f"run {run_id} did not reach terminal state in time")


def _wait_graph_runs(graph_id: str, n: int, timeout: float = 8.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = [r for r in _run_store().list(limit=100) if r["graphId"] == graph_id]
        done = [r for r in runs if r["status"] not in {"running", "suspended"}]
        if len(done) >= n:
            return done
        time.sleep(0.05)
    pytest.fail(f"graph {graph_id} did not produce {n} terminal runs in time")


# --- U1443 触发器编译期合法 ---------------------------------------------------


def test_u1443_message_inbound_trigger_compiles_and_publishes():
    admin = _login("admin")
    graph_id = _save_graph(admin)
    # 无额外必填 ⇒ 保存即过编译；可发布 ⇒ 钉版机制认可。
    _publish_and_pin(admin, graph_id)
    got = client.get(f"/api/graphs/{graph_id}", headers=admin)
    assert got.status_code == 200


# --- U1444 验签与去重 ----------------------------------------------------------


def test_u1444_bad_signature_returns_401():
    admin = _login("admin")
    bid = _create_binding(admin, secret=INBOUND_SECRET)
    body = _msg("m-401")
    assert _post_inbound(bid, body).status_code == 401                     # 无签名
    import json as _json

    raw = _json.dumps(body).encode()
    r = client.post(
        f"/api/channels/inbound/{bid}", content=raw,
        headers={"X-Inbound-Signature": _sign(raw, "wrong-secret")},
    )
    assert r.status_code == 401                                            # 错签名


def test_u1444_duplicate_message_is_idempotent():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-dup", graph_id)

    body = _msg("m-dup", "conv-dup")
    first = _post_inbound(bid, body)
    assert first.status_code == 200 and first.json().get("received") is True
    _wait_terminal(first.json()["runId"])

    second = _post_inbound(bid, body)
    assert second.status_code == 200 and second.json() == {"duplicate": True}
    runs = [r for r in _run_store().list(limit=50) if r["graphId"] == graph_id]
    assert len(runs) == 1, "重复消息不得二次起 run"


# --- U1445 映射命中起 run／未命中死信 ------------------------------------------


def test_u1445_mapping_hit_starts_run_with_agent():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    agent_id = _create_agent(admin)
    _put_mapping(admin, bid, "conv-hit", graph_id, agent_id=agent_id)

    r = _post_inbound(bid, _msg("m-hit", "conv-hit"))
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["received"] is True and payload["agentId"] == agent_id
    # runId 真实可查（receiver 预生成＋派发方预 begin），且带 agent 维度。
    run = _wait_terminal(payload["runId"])
    assert run["agentId"] == agent_id
    assert run["graphId"] == graph_id


def test_u1445_unmapped_conversation_dead_letters():
    admin = _login("admin")
    bid = _create_binding(admin)

    r = _post_inbound(bid, _msg("m-nomap", "conv-nobody"))
    assert r.status_code == 200 and r.json()["deadLetter"] is True

    dlq = client.get("/api/channels/inbound/dead-letters", headers=admin).json()["items"]
    row = [item for item in dlq if item["messageId"] == "m-nomap"]
    assert row, "未命中映射的消息必须进死信（不静默吞）"
    assert any(reason["code"] == "NO_MAPPING" for reason in row[0]["reasons"])


# --- U1446 loop 协同 -----------------------------------------------------------


def test_u1446_loop_graph_enters_cycle_via_inbound():
    admin = _login("admin")
    bid = _create_binding(admin)
    # maxIterations=2、无 completeWhen ⇒ 靠预算收两圈（docs/130 U1428 同款口径）。
    graph_id = _save_graph(admin, loop={"maxIterations": 2})
    _publish_and_pin(admin, graph_id)
    agent_id = _create_agent(admin, "圈分身")
    _put_mapping(admin, bid, "conv-loop", graph_id, agent_id=agent_id)

    r = _post_inbound(bid, _msg("m-loop", "conv-loop"))
    assert r.json()["received"] is True

    runs = _wait_graph_runs(graph_id, 2)
    assert len(runs) == 2
    by_seq = sorted(runs, key=lambda r: r["cycleSeq"])
    # 第一圈沿用派发方预置的 run（docs/130：「预置 run_id 只用于第一圈，tick 内可见
    # running」的占位语义，调度器派发同款——cycleSeq=0/cycleGroupId='' 是预 begin 值）；
    # 第二圈起由圈层发新 run，cycleSeq/cycleGroupId 为真实圈归属 ⇒ 圈会话确实发生。
    assert by_seq[0]["cycleSeq"] == 0
    assert by_seq[1]["cycleSeq"] == 2
    assert by_seq[1]["cycleGroupId"], "入站触发的 loop 图必须进入圈会话"
    assert all(run["agentId"] == agent_id for run in runs), "圈各圈都带 agent 维度"


# --- U1447 DLQ 重放/删除/指标 ---------------------------------------------------


def test_u1447_dead_letter_replay_delete_and_metrics():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    # 不发布 ⇒ 映射命中但无可用版本 ⇒ NO_PUBLISHED_VERSION 死信。
    _put_mapping(admin, bid, "conv-replay", graph_id)

    r = _post_inbound(bid, _msg("m-replay", "conv-replay"))
    assert r.json()["deadLetter"] is True
    dlq = client.get("/api/channels/inbound/dead-letters", headers=admin).json()["items"]
    row = [item for item in dlq if item["messageId"] == "m-replay"]
    assert row and any(rr["code"] == "NO_PUBLISHED_VERSION" for rr in row[0]["reasons"])

    # 发布钉版后重放 ⇒ 转 received 且真起 run。
    _publish_and_pin(admin, graph_id)
    replay = client.post(
        "/api/channels/inbound/dead-letters/m-replay/replay", headers=_login("operator")
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "received"
    _wait_terminal(replay.json()["runId"])
    assert client.get("/api/channels/inbound/dead-letters", headers=admin).json()["items"] == []

    # 再造一条死信 ⇒ 删除。
    _post_inbound(bid, _msg("m-delete", "conv-nobody-2"))
    deleted = client.delete("/api/channels/inbound/dead-letters/m-delete", headers=admin)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}

    metrics = client.get("/api/channels/inbound/metrics", headers=admin).json()
    assert metrics["totals"]["received"] >= 1
    assert metrics["totals"]["dead"] == 0


# --- U1448 回写会话 ------------------------------------------------------------


def test_u1448_session_view_records_inbound_message():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-view", graph_id)

    r = _post_inbound(bid, _msg("m-view", "conv-view", text="在吗"))
    run_id = r.json()["runId"]
    _wait_terminal(run_id)

    view = client.get("/api/channels/inbound/sessions/conv-view", headers=admin)
    assert view.status_code == 200
    items = view.json()["items"]
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert items[0]["messageId"] == "m-view"
    assert items[0]["runId"] == run_id
    assert items[0]["data"]["text"] == "在吗"


# --- U1449 agent CRUD＋执行日记 --------------------------------------------------


def test_u1449_agents_crud_and_agent_runs():
    admin = _login("admin")
    # 缺 name ⇒ 422
    assert client.post("/api/agents", json={}, headers=admin).status_code == 422
    # viewer 不可建（administer 档）
    assert client.post("/api/agents", json={"name": "x"}, headers=_login("viewer")).status_code == 403

    agent_id = _create_agent(admin, "客服分身")
    listing = client.get("/api/agents", headers=_login("viewer"))
    assert listing.status_code == 200
    assert any(a["id"] == agent_id for a in listing.json()["items"])

    updated = client.put(
        f"/api/agents/{agent_id}", json={"enabled": False}, headers=_login("operator")
    )
    assert updated.status_code == 200 and updated.json()["enabled"] is False

    # 起一条带该 agent 的 run ⇒ 执行日记按 agentId 过滤＋token 汇总键在（走 AU 账）。
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-agent", graph_id, agent_id=agent_id)
    r = _post_inbound(bid, _msg("m-agent", "conv-agent"))
    _wait_terminal(r.json()["runId"])

    diary = client.get(f"/api/agents/{agent_id}/runs", headers=admin)
    assert diary.status_code == 200, diary.text
    body = diary.json()
    assert body["agent"]["id"] == agent_id
    assert [run["runId"] for run in body["items"]] == [r.json()["runId"]]
    assert set(body["tokenSummary"]) == {
        "calls", "promptTokens", "completionTokens", "totalTokens"
    }

    # 跨 agent 不串：新建 agent 的日记为空。
    other = _create_agent(admin, "另一个分身")
    assert client.get(f"/api/agents/{other}/runs", headers=admin).json()["items"] == []

    assert client.delete(f"/api/agents/{agent_id}", headers=_login("operator")).status_code == 403
    assert client.delete(f"/api/agents/{agent_id}", headers=admin).json() == {"deleted": True}


def test_u1449_inbound_mapping_validates_agent_and_graph():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    agent_id = _create_agent(admin)
    r = client.put(
        f"/api/channels/{bid}/inbound-mappings",
        json={"mappings": [
            {"conversationId": "c-1", "graphId": "graph-missing"},
            {"conversationId": "c-2", "graphId": graph_id, "agentId": "agent-missing"},
            {"conversationId": "c-2", "graphId": graph_id, "agentId": agent_id,
             "enabled": "yes"},
        ]},
        headers=admin,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("graph-missing" in line for line in detail)
    assert any("agent-missing" in line for line in detail)
    assert any("重复" in line for line in detail)
    assert any("enabled" in line for line in detail)


# --- U1450 多轮透传（不做摘要） -------------------------------------------------


def test_u1450_multi_turn_passthrough_conversation_id():
    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-multi", graph_id)

    first = _post_inbound(bid, _msg("m-t1", "conv-multi", text="第一轮"))
    second = _post_inbound(bid, _msg("m-t2", "conv-multi", text="第二轮"))
    assert first.json()["received"] and second.json()["received"]
    assert first.json()["runId"] != second.json()["runId"], "每消息一次图（无 loop ⇒ 独立 run）"
    _wait_terminal(first.json()["runId"])
    _wait_terminal(second.json()["runId"])

    items = client.get(
        "/api/channels/inbound/sessions/conv-multi", headers=admin
    ).json()["items"]
    assert [m["messageId"] for m in items] == ["m-t1", "m-t2"]
    assert all(m["conversation_id"] == "conv-multi" for m in items)
    # D-P2 拍板不做摘要：会话记录只透传原文，无 summary 字段。
    assert all("summary" not in m for m in items)


# --- 反向门（植缺陷实跑转红） ----------------------------------------------------


def test_u1451_reverse_gate_without_dedup_same_message_runs_twice(monkeypatch):
    """摘掉去重 ⇒ 同消息二次起 run（红，防"去重只是摆设"）。"""
    from atlas.iam.deps import tenant_registry

    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-rg1", graph_id)

    store = tenant_registry.get("t1").inbound_deliveries
    monkeypatch.setattr(store, "note_duplicate_if_seen", lambda *a, **k: False)

    body = _msg("m-rg1", "conv-rg1")
    assert _post_inbound(bid, body).json()["received"] is True
    assert _post_inbound(bid, body).json()["received"] is True, "植缺陷后第二次不再是 duplicate"
    runs = _wait_graph_runs(graph_id, 2)
    assert len(runs) == 2, "红：去重摘掉后同消息起了两条 run（证明去重在正常工作位）"


def test_u1452_reverse_gate_without_mapping_hit_message_is_dropped(monkeypatch):
    """摘掉映射命中 ⇒ 消息进死信而非静默吞（红）。"""
    from atlas.channels.inbound import InboundReceiver

    admin = _login("admin")
    bid = _create_binding(admin)
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-rg2", graph_id)

    monkeypatch.setattr(InboundReceiver, "_active_mapping", lambda self, b, c: None)

    r = _post_inbound(bid, _msg("m-rg2", "conv-rg2"))
    assert r.json()["deadLetter"] is True, "红：映射命中摘掉后消息只能去死信"
    dlq = client.get("/api/channels/inbound/dead-letters", headers=admin).json()["items"]
    assert any(item["messageId"] == "m-rg2" for item in dlq), "红：不是静默吞，死信可见"
    assert [r for r in _run_store().list(limit=50) if r["graphId"] == graph_id] == []


# --- U1454 真机端到端（假供应商腿） ----------------------------------------------


def test_u1454_end_to_end_inbound_to_session_view():
    admin = _login("admin")
    bid = _create_binding(admin, secret=INBOUND_SECRET)      # 预共享 secret 验签腿
    agent_id = _create_agent(admin, "端到端分身")
    graph_id = _save_graph(admin)
    _publish_and_pin(admin, graph_id)
    _put_mapping(admin, bid, "conv-e2e", graph_id, agent_id=agent_id)

    r = _post_inbound(bid, _msg("m-e2e", "conv-e2e", text="端到端"), secret=INBOUND_SECRET)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload == {
        "received": True, "runId": payload["runId"], "agentId": agent_id
    }

    run = _wait_terminal(payload["runId"])
    assert run["status"] == "completed", f"端到端 run 必须 completed：{run.get('status')}"
    assert run["agentId"] == agent_id

    items = client.get(
        "/api/channels/inbound/sessions/conv-e2e", headers=admin
    ).json()["items"]
    assert len(items) == 1 and items[0]["runId"] == payload["runId"]

    diary = client.get(f"/api/agents/{agent_id}/runs", headers=admin).json()
    assert [run["runId"] for run in diary["items"]] == [payload["runId"]]

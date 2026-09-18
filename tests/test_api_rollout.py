"""M9 批 1（U56）：入站事件路由 pin-to-version + rollout REST 接线（api/main.py）。

覆盖 docs/13 U56：
① 带 event 运行经 RoutingStore 解析版本、按不可变发布快照加载，
  RunRecord.resolved_version 与终帧 graphVersion 为该版本；
② 无任何发布版带 event → 409；
③ 不带 event 走草稿、resolved_version=None，旧行为零回归；
④ 在途 pin：canary 期间挂起的 human_approval 帧冻结 candidate 快照，
  rollback 后续跑仍用 candidate，新 event 全落 stable；
⑤ reset 清空 rollout 配置/状态/计数；
⑥ 跨租户状态互不可见（per-tenant RoutingStore 实例隔离；撞 id 时解析到本租户自身资源、不泄漏他租户状态，不存在才 404）、viewer 只读、operator 可写。
另含 rollout 状态机 REST 映射（409/422/投影）与低金额分桶接线。
"""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from atlas.api import main as mainmod
from atlas.api.main import app
from atlas.graph.dsl import parse_graph

client = TestClient(app)


def _human_graph(name_mark: str = "人工审批") -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "human-1", "type": "human_approval", "name": name_mark,
             "config": {
                 "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
                 "approver": "客服主管",
                 "timeoutSeconds": 300,
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


def _canary_full_config() -> dict:
    return {
        "strategy": "progressive",
        "rules": [{"to": "canary", "percent": 100}],
    }


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


def _publish_two_versions(v2_mark: str = "人工审批-v2") -> str:
    """建图 → v1 → 更新草稿（改名标记）→ v2，返回 graph_id。"""
    from atlas.iam.deps import tenant_registry

    graph_id = client.post("/api/graphs", json=_human_graph()).json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/publish").status_code == 200
    store = tenant_registry.get("t1").graph_store
    store._graphs[graph_id] = _human_graph(v2_mark)  # 草稿更新后发内容不同的 v2
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 2
    return graph_id


def _start_canary(graph_id: str, config: dict | None = None) -> dict:
    client.put(f"/api/graphs/{graph_id}/rollout", json=config or _canary_full_config())
    response = client.post(f"/api/graphs/{graph_id}/rollout/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "canary" and body["stable"] == 1 and body["candidate"] == 2
    return body


def _latest_run(graph_id: str) -> dict:
    items = client.get(
        "/api/monitoring/runs", params={"graph_id": graph_id, "limit": 1}
    ).json()["items"]
    assert items, "应有运行记录"
    return items[0]


# ② 无发布版带 event → 409
def test_event_without_published_version_is_409():
    graph_id = client.post("/api/graphs", json=_human_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "api", "payload": {"order_id": "1", "amount": 10}},
              "inputs": {"order_id": "1", "approvals": {"human-1": "approved"}}},
    )
    assert response.status_code == 409
    assert "尚未发布" in response.json()["detail"]


# ③ 不带 event 走草稿、resolved_version=None（零回归）
def test_manual_draft_run_has_no_resolved_version():
    graph_id = client.post("/api/graphs", json=_human_graph()).json()["id"]
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "2", "approvals": {"human-1": "approved"}}},
    )
    assert response.status_code == 200
    run = _latest_run(graph_id)
    assert run["status"] == "completed"
    assert run["resolved_version"] is None


# event 与 releaseVersion 同传 422；event 非对象 422
def test_event_conflicts_and_bad_shape_are_422():
    graph_id = _publish_two_versions()
    base = {"event": {"channel": "api", "payload": {}}, "releaseVersion": 1,
            "inputs": {"order_id": "3", "approvals": {"human-1": "approved"}}}
    assert client.post(f"/api/graphs/{graph_id}/run", json=base).status_code == 422
    bad = {"event": "not-an-object",
           "inputs": {"order_id": "4", "approvals": {"human-1": "approved"}}}
    assert client.post(f"/api/graphs/{graph_id}/run", json=bad).status_code == 422


# ① sync：canary 100% 下 event 落 candidate，RunRecord.resolved_version=2
def test_sync_event_pinned_to_candidate():
    graph_id = _publish_two_versions()
    state = _start_canary(graph_id)
    assert state["config"] is not None

    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "api", "payload": {"order_id": "70001", "amount": 999}},
              "inputs": {"order_id": "70001", "approvals": {"human-1": "approved"}}},
    )
    assert response.status_code == 200, response.text

    run = _latest_run(graph_id)
    assert run["resolved_version"] == 2
    rollout = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert rollout["traffic"]["candidate"] == 1
    assert rollout["traffic"]["segments"]["canary"] == 1


# ① stream：终帧 graphVersion 为钉住的 candidate
def test_stream_event_graph_version_is_candidate():
    graph_id = _publish_two_versions()
    _start_canary(graph_id)
    with client.stream(
        "POST",
        f"/api/graphs/{graph_id}/run/stream",
        json={"event": {"channel": "api", "payload": {"order_id": "70002", "amount": 999}},
              "inputs": {"order_id": "70002", "approvals": {"human-1": "approved"}}},
    ) as response:
        assert response.status_code == 200
        data = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line and line.startswith("data: ")
        ]
    result = data[-1]
    assert result["graphVersion"] == f"{graph_id}@2"
    assert _latest_run(graph_id)["resolved_version"] == 2


# 低金额桶接线：amount<=100 落 candidate，大额落 stable
def test_low_value_bucket_routes_by_amount():
    graph_id = _publish_two_versions()
    config = {
        "strategy": "progressive",
        "rules": [
            {"to": "lowValueBucket", "field": "payload.amount", "op": "<=",
             "value": 100, "percent": 100}
        ],
    }
    _start_canary(graph_id, config)

    def run_with_amount(amount: int, order_id: str) -> int | None:
        response = client.post(
            f"/api/graphs/{graph_id}/run",
            json={"event": {"channel": "api", "payload": {"order_id": order_id, "amount": amount}},
                  "inputs": {"order_id": order_id, "approvals": {"human-1": "approved"}}},
        )
        assert response.status_code == 200, response.text
        return _latest_run(graph_id)["resolved_version"]

    assert run_with_amount(50, "80001") == 2
    assert run_with_amount(5000, "80002") == 1
    rollout = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert rollout["traffic"]["stable"] == 1
    assert rollout["traffic"]["candidate"] == 1
    assert rollout["traffic"]["segments"]["lowValueBucket"] == 1


# ④ 在途 pin：candidate 挂起 → rollback → 放行，续跑仍 candidate；新 event 落 stable
def test_inflight_frame_stays_on_candidate_after_rollback(monkeypatch):
    from atlas.iam.deps import tenant_registry

    graph_id = _publish_two_versions()
    _start_canary(graph_id)

    # 包住 API 的 frame_sink 工厂，捕获真实链路写出的挂起帧
    frames: list[dict] = []
    original_factory = mainmod._frame_sink_for

    def capturing_factory(tenant_id: str, run_store, run_id: str):
        inner_sink = original_factory(tenant_id, run_store, run_id)

        def sink(frame: dict) -> None:
            frames.append(frame)
            inner_sink(frame)

        return sink

    monkeypatch.setattr(mainmod, "_frame_sink_for", capturing_factory)

    def start_inflight() -> None:
        client.post(
            f"/api/graphs/{graph_id}/run",
            json={"event": {"channel": "api", "payload": {"order_id": "90001", "amount": 999}},
                  "inputs": {"order_id": "90001"}},
        )

    worker = threading.Thread(target=start_inflight)
    worker.start()

    # 等挂起帧（帧内图快照必须是 candidate v2 的不可变内容）
    for _ in range(200):
        if frames:
            break
        threading.Event().wait(0.02)
    assert frames, "candidate 运行应在 human_approval 挂起并写出帧"
    frame = frames[0]
    snapshot_names = {node["id"]: node["name"] for node in frame["graph_snapshot"]["nodes"]}
    assert snapshot_names["human-1"] == "人工审批-v2"  # 帧冻结 candidate，不是 v1
    # 帧快照＝candidate 发布版经 DSL 规范化后的完整深拷贝（run_graph 内 graph.model_dump()）
    v2_graph = parse_graph(tenant_registry.get("t1").graph_store.get(graph_id, 2))
    assert frame["graph_snapshot"] == v2_graph.model_dump()

    # 挂起期间回滚：新 event 立即全落 stable
    rolled = client.post(
        f"/api/graphs/{graph_id}/rollout/rollback", json={"reason": "manual test"}
    )
    assert rolled.status_code == 200
    assert rolled.json()["status"] == "rolled_back"
    stable_run = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "api", "payload": {"order_id": "90002", "amount": 999}},
              "inputs": {"order_id": "90002", "approvals": {"human-1": "approved"}}},
    )
    assert stable_run.status_code == 200
    assert _latest_run(graph_id)["resolved_version"] == 1

    # 同 token 放行在途实例 → 续跑完成，记录的仍是启动时钉住的 candidate
    token = frame["resume_token"]
    decision = client.post(
        f"/api/approvals/{token}/decision", json={"decision": "approved"}
    )
    assert decision.status_code == 200, decision.text
    worker.join(timeout=10)
    assert not worker.is_alive()

    runs = client.get(
        "/api/monitoring/runs", params={"graph_id": graph_id, "limit": 10}
    ).json()["items"]
    inflight = next(run for run in runs if run["mode"] == "sync" and run["resolved_version"] == 2
                    and run["status"] == "completed")
    assert inflight is not None


# rollout REST 状态机：未配置/版本不足 409、非法配置 422、promote/幂等回滚
def test_rollout_rest_state_machine():
    graph_id = client.post("/api/graphs", json=_human_graph()).json()["id"]

    # 未配置 start → 409
    assert client.post(f"/api/graphs/{graph_id}/rollout/start").status_code == 409
    # 配置后版本不足 → 409
    client.put(f"/api/graphs/{graph_id}/rollout", json=_canary_full_config())
    assert client.post(f"/api/graphs/{graph_id}/rollout/start").status_code == 409
    # 非法配置 422（中文聚合）
    bad = client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "canary", "rules": [{"to": "canary", "percent": 101}]},
    )
    assert bad.status_code == 422 and "灰度配置非法" in bad.json()["detail"]
    # GET 投影（idle、config 已存）
    projection = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert projection["status"] == "idle" and projection["config"] is not None
    assert projection["stable"] is None and projection["candidate"] is None

    gid2 = _publish_two_versions()
    client.put(f"/api/graphs/{gid2}/rollout", json=_canary_full_config())
    assert client.post(f"/api/graphs/{gid2}/rollout/start").status_code == 200
    # 重复 start 409；promote canary→full
    assert client.post(f"/api/graphs/{gid2}/rollout/start").status_code == 409
    promoted = client.post(f"/api/graphs/{gid2}/rollout/promote")
    assert promoted.status_code == 200 and promoted.json()["status"] == "full"
    assert client.post(f"/api/graphs/{gid2}/rollout/promote").status_code == 409
    # rollback 幂等（重复调用保留首次原因）
    first = client.post(f"/api/graphs/{gid2}/rollout/rollback", json={"reason": "first"})
    assert first.status_code == 200 and first.json()["rollbackReason"] == "first"
    second = client.post(f"/api/graphs/{gid2}/rollout/rollback", json={"reason": "second"})
    assert second.status_code == 200 and second.json()["rollbackReason"] == "first"


# ⑤ reset 清空 rollout 配置/状态/计数
def test_reset_clears_rollout_state():
    graph_id = _publish_two_versions()
    _start_canary(graph_id)
    client.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "api", "payload": {"order_id": "70003", "amount": 999}},
              "inputs": {"order_id": "70003", "approvals": {"human-1": "approved"}}},
    )
    assert client.post("/api/demo/reset").status_code == 200
    # 图已随 reset 删除 → 404（不泄漏旧状态）
    assert client.get(f"/api/graphs/{graph_id}/rollout").status_code == 404


# ⑥ 跨租户 404、viewer 只读、operator 可写
def test_cross_tenant_and_rbac():
    graph_id = _publish_two_versions()
    _start_canary(graph_id)

    t2 = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    t2_headers = {"Authorization": f"Bearer {t2}"}
    # graph_id 每租户独立自增、可能撞 id：t2 解析到 t2 自身命名空间。
    # 即使 200 也只能看到 t2 自己资源的 idle 态，绝不含 t1 的 canary/config/candidate（不泄漏）。
    t2_rollout = client.get(f"/api/graphs/{graph_id}/rollout", headers=t2_headers)
    if t2_rollout.status_code == 200:
        other = t2_rollout.json()
        assert other["status"] == "idle"
        assert other["config"] is None and other["candidate"] is None
        assert other["traffic"]["candidate"] == 0
    else:
        assert t2_rollout.status_code == 404
    # t2 必然不存在的图 id → 404；启动他租户图灰度因 t2 未配置 → 409（不触碰 t1 状态）
    assert client.get("/api/graphs/graph-999999/rollout", headers=t2_headers).status_code == 404
    assert client.post(
        f"/api/graphs/{graph_id}/rollout/start", headers=t2_headers
    ).status_code == 409
    # t1 自身状态未受 t2 访问影响，仍是 canary
    assert client.get(f"/api/graphs/{graph_id}/rollout").json()["status"] == "canary"

    viewer = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    viewer_headers = {"Authorization": f"Bearer {viewer}"}
    assert client.get(
        f"/api/graphs/{graph_id}/rollout", headers=viewer_headers
    ).status_code == 200
    for method, path in (
        ("PUT", f"/api/graphs/{graph_id}/rollout"),
        ("POST", f"/api/graphs/{graph_id}/rollout/promote"),
        ("POST", f"/api/graphs/{graph_id}/rollout/rollback"),
    ):
        response = client.request(method, path, headers=viewer_headers, json={})
        assert response.status_code == 403, (method, path)

    operator = client.post(
        "/api/auth/login", json={"username": "operator-a", "password": "operator123"}
    ).json()["token"]
    operator_headers = {"Authorization": f"Bearer {operator}"}
    rolled = client.post(
        f"/api/graphs/{graph_id}/rollout/rollback",
        headers=operator_headers, json={"reason": "operator rollback"},
    )
    assert rolled.status_code == 200

"""M9 批 2（U57）：发布前批量回放门禁（recording/gate.py + release-gate 端点 + publish gate）。

覆盖 docs/13 U57：
① graph_id 匹配用例草稿全过：total=N/passed=N/blocked=false/skipped=false；
② 草稿改坏：matches=false、blocked=true、failed=1，note 指向差异；
③ publish {gate:true} blocked → 409 带报告且不产新版本；修好后正常产版本；
④ total=0 → skipped=true/blocked=false 不阻塞；旧用例 graph_id="" 不入选；
⑤ release-gate 只跑不发布，图不存在 404；
⑥ 逐例标准 run_graph（审批预置秒回，复用 replay.compare）；
⑦ RecordingCase.graph_id 纯超集落库、投影含 graph_id、旧用例（空串）不回归。
另含 RBAC：viewer 不可跑门禁/发布。
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.recording.cases import RecordStep

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    # 录制用例 reset 不清除（测试资产），而 graph_id 自增在 reset 后重用会撞 id；
    # 门禁按 graph_id 筛选用例，故本文件每例清空用例存储做隔离（不改变产品 reset 语义）。
    tenant_registry.get("t1").recording_store._items.clear()
    yield
    client.headers.pop("authorization", None)


def _load_template_graph() -> dict:
    return client.get("/api/templates/approval-timeout-reject").json()["graph"]


def _record_case(graph_id: str, graph: dict, name: str) -> str:
    """跑一次 rejected 终态（预置审批秒回），用 outputs 造 steps 并绑定 graph_id 录为用例。"""
    run = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"approvals": {"approval-1": "rejected"}}},
    )
    assert run.status_code == 200, run.text
    result = run.json()
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    steps = [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in result["outputs"].items()
    ]
    response = client.post(
        "/api/recordings",
        json={
            "name": name,
            "graph_id": graph_id,
            "inputs": {"approvals": {"approval-1": "rejected"}},
            "steps": steps,
            "status": result["status"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _make_graph_with_cases(case_count: int = 1) -> tuple[str, dict]:
    graph = _load_template_graph()
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    for index in range(case_count):
        _record_case(graph_id, graph, f"门禁用例 {index + 1}")
    return graph_id, graph


def _tamper_draft(graph_id: str, graph: dict) -> dict:
    """把草稿 rejected-msg 的邮件正文改坏（其余不动），返回坏草稿 raw。"""
    bad = copy.deepcopy(graph)
    node = next(item for item in bad["nodes"] if item["id"] == "rejected-msg")
    params = json.loads(node["config"]["params"])
    params["body"] = "草稿改坏后的正文，与录制基线不一致。"
    node["config"]["params"] = json.dumps(params, ensure_ascii=False)
    tenant_registry.get("t1").graph_store._graphs[graph_id] = bad
    return bad


def test_release_gate_all_pass_with_matching_cases():
    graph_id, _ = _make_graph_with_cases(case_count=2)

    response = client.post(f"/api/graphs/{graph_id}/release-gate")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["graph_id"] == graph_id
    assert report["target"] == "draft"
    assert report["total"] == 2
    assert report["passed"] == 2
    assert report["failed"] == 0
    assert report["skipped"] is False
    assert report["blocked"] is False
    assert len(report["cases"]) == 2
    assert all(row["matches"] for row in report["cases"])
    assert all(row["replay_status"] == "completed" for row in report["cases"])


def test_release_gate_detects_bad_draft_with_diff_note():
    graph_id, graph = _make_graph_with_cases(case_count=1)
    _tamper_draft(graph_id, graph)

    report = client.post(f"/api/graphs/{graph_id}/release-gate").json()
    assert report["blocked"] is True
    assert report["skipped"] is False
    assert report["total"] == 1 and report["failed"] == 1 and report["passed"] == 0
    row = report["cases"][0]
    assert row["matches"] is False
    assert row["case_id"].startswith("rec-")
    # 正文改坏 → message 节点归一化后 result 顶层键不一致
    assert "result" in row["note"]


def test_publish_gate_blocks_bad_draft_then_passes_after_restore():
    graph_id, graph = _make_graph_with_cases(case_count=1)
    good = copy.deepcopy(graph)
    _tamper_draft(graph_id, graph)

    blocked = client.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert isinstance(detail, dict) and detail["report"]["blocked"] is True
    # 拦截后不产新版本
    assert client.get(f"/api/graphs/{graph_id}/versions").json()["items"] == []

    # 还原草稿 → 门禁通过、正常发布
    tenant_registry.get("t1").graph_store._graphs[graph_id] = good
    ok = client.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["releaseVersion"] == 1
    assert client.get(f"/api/graphs/{graph_id}/versions").json()["items"] == [1]

    # 不带 gate 的发布保持旧行为（无 body）
    ok2 = client.post(f"/api/graphs/{graph_id}/publish")
    assert ok2.status_code == 200 and ok2.json()["releaseVersion"] == 2


def test_release_gate_skipped_when_no_cases_and_legacy_empty_graph_id_excluded():
    # 一张全新图、无绑定用例 → skipped 明示未覆盖、不阻塞
    graph_id = client.post("/api/graphs", json=_load_template_graph()).json()["id"]
    report = client.post(f"/api/graphs/{graph_id}/release-gate").json()
    assert report["total"] == 0
    assert report["skipped"] is True
    assert report["blocked"] is False
    assert report["cases"] == []

    published = client.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    assert published.status_code == 200, published.text  # 未覆盖不阻塞

    # 旧用例 graph_id="" 不入选任何图的门禁
    legacy = tenant_registry.get("t1").recording_store.add(
        name="旧用例无 graph_id",
        graph=_load_template_graph(),
        inputs={"approvals": {"approval-1": "rejected"}},
        steps=[RecordStep(node_id="approval-1", node_type="human_approval",
                          output={"decision": "rejected"})],
        status="completed",
    )
    assert legacy.graph_id == ""
    report2 = client.post(f"/api/graphs/{graph_id}/release-gate").json()
    assert report2["total"] == 0 and report2["skipped"] is True


def test_release_gate_endpoint_does_not_publish_and_404():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    before = client.get(f"/api/graphs/{graph_id}/versions").json()["items"]
    response = client.post(f"/api/graphs/{graph_id}/release-gate")
    assert response.status_code == 200
    after = client.get(f"/api/graphs/{graph_id}/versions").json()["items"]
    assert before == after == []  # 只跑门禁，不产版本

    assert client.post("/api/graphs/graph-999999/release-gate").status_code == 404
    assert client.post(
        "/api/graphs/graph-999999/publish", json={"gate": True}
    ).status_code == 404


def test_graph_id_persisted_projected_and_legacy_case_reads():
    graph_id, _ = _make_graph_with_cases(case_count=1)

    detail = client.get("/api/recordings").json()["items"]
    mine = next(item for item in detail if item["graph_id"] == graph_id)
    assert set(mine) == {
        "id", "name", "graph_id", "node_count", "step_count", "status", "created_at"
    }
    full = client.get(f"/api/recordings/{mine['id']}").json()
    assert full["graph_id"] == graph_id

    # 旧用例（graph_id 默认空串）读取不回归
    legacy = tenant_registry.get("t1").recording_store.add(
        name="旧用例",
        graph=_load_template_graph(),
        inputs=None,
        steps=[RecordStep(node_id="trigger-1", node_type="trigger", output={})],
        status="completed",
    )
    legacy_detail = client.get(f"/api/recordings/{legacy.id}").json()
    assert legacy_detail["graph_id"] == ""
    legacy_item = next(
        item for item in client.get("/api/recordings").json()["items"]
        if item["id"] == legacy.id
    )
    assert legacy_item["graph_id"] == ""


def test_release_gate_requires_operate_permission():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    viewer_token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {viewer_token}"
    assert client.post(f"/api/graphs/{graph_id}/release-gate").status_code == 403
    assert client.post(
        f"/api/graphs/{graph_id}/publish", json={"gate": True}
    ).status_code == 403

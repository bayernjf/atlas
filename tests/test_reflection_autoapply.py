# -*- coding: utf-8 -*-
"""打包 BP（docs/132 D75）：进化候选门控自动生效的 patch 单元＋REST 测试（U1455–U1470）。

覆盖（docs/132 §5 验收逐条）：
- U1455  patch 正向：图内变量与节点参数都落点，diff 形状（from/to/path）正确；
- U1456  patch 脏草稿中止（latest≠base），不覆盖人工改动；
- U1457  patch 无草稿中止；
- U1458  patch 形状异常中止：未知变量/缺 node_id/节点不存在/非 ai_decision 照实不猜；
- U1459  patch monitor/gate 行跳过并计入 diff（不静默吞、不阻断）；
- U1460  端点幂等：同一候选重复 autoapply → 409（BP-D2）；
- U1461  类别开关 fail-closed：候选含未开启类别 → run failed 留证、不发布（BP-D1）；
- U1462  完整推进链：patch→影子必跑→黄金回放→自动 publish→自动 canary（BP-D4）；
- U1463  已处理候选（adopted/dismissed）→ 409，不自动；
- U1464  黄金回放 blocked → 中止 failed，不发布（U1464 反向门）；
- U1465  人可中止：canary 态 abort＝手动回滚（BP-D5）；
- U1466  abort 404 / 终态幂等；
- U1467  列表/详情＋跨租户隔离（t2 读 t1 run → 404）；
- U1468  开关投影：enabledScopes/availableScopes/structureAuto；
- U1469  脏草稿经端点 → failed 留证（不发布）；
- U1470  reset 清空自动生效记录。
"""

from __future__ import annotations

import copy
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.recording import RecordingCase, RecordStep
from atlas.reflection import Change
from atlas.reflection.candidate import ReflectionCandidate
from atlas.reflection.patch import patch_candidate
from atlas.template.graphs import refund_template_graph

client = TestClient(app)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


ADMIN = _login("admin")
OPERATOR = _login("operator")
VIEWER = _login("viewer")


def _admin_b() -> dict[str, str]:
    """跨租户（t2）管理员头；测试内再登录，避免模块级串序。"""
    token = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _setup():
    from atlas.iam.deps import tenant_registry

    client.post("/api/demo/reset", headers=ADMIN)
    services = tenant_registry.get("t1")
    services.autoapply_store.reset()
    services.autoapply_enabled_scopes.clear()
    # 录制用例产品语义「reset 不清除」；测试间需隔离（U1464 会注入不匹配用例，
    # 不清理会污染后续测试的黄金回放）。逐条删，不动产品 reset 语义。
    for case in services.recording_store.list():
        services.recording_store.delete(case.id)
    yield
    client.headers.pop("authorization", None)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_graph(version: int = 1) -> str:
    raw = copy.deepcopy(refund_template_graph())
    raw["version"] = version
    resp = client.post("/api/graphs", json=raw, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _publish(graph_id: str) -> int:
    resp = client.post(f"/api/graphs/{graph_id}/publish", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["releaseVersion"]


def _update_draft_touched(graph_id: str) -> None:
    """人工改过 latest 草稿（内容 ≠ 已发布基线；DSL version 恒 1，不 bump）。"""
    raw = copy.deepcopy(refund_template_graph())
    for variable in raw["variables"]:
        if variable["name"] == "approval_limit":
            variable["value"] = "600"
    resp = client.put(f"/api/graphs/{graph_id}", json=raw, headers=ADMIN)
    assert resp.status_code == 200, resp.text


def _configure_rollout(graph_id: str) -> None:
    resp = client.put(f"/api/graphs/{graph_id}/rollout", json={}, headers=ADMIN)
    assert resp.status_code == 200, resp.text


def _inject_candidate(
    graph_id: str, base_version: int, changes: list, candidate_id: str
) -> str:
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get("t1")
    cand = ReflectionCandidate(
        candidate_id=candidate_id,
        graph_id=graph_id,
        base_version=base_version,
        changes=changes,
        evidence_digest="bp-test",
        generated_at=_now(),
    )
    services.reflection_store.add_candidate(cand)
    return candidate_id


def _inject_mismatch_case(graph_id: str) -> None:
    """注入一条与真实回放必然不符的用例（output 形状不同 → 黄金回放不匹配 → blocked）。"""
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get("t1")
    services.recording_store.add(
        name="bp 不匹配用例",
        graph_id=graph_id,
        graph=copy.deepcopy(refund_template_graph()),
        inputs={"order_id": "o1", "amount": 100, "reason": "商品破损"},
        steps=[
            RecordStep(
                node_id="tool_call-1", node_type="tool_call", output={"__bp_mismatch__": True}
            )
        ],
        status="completed",
    )


def _draft_value(graph_id: str) -> str | int:
    raw = client.get(f"/api/graphs/{graph_id}", headers=ADMIN).json()
    return raw["variables"][0]["value"]


# ---------------------------------------------------------------- U1455–U1459 patch 单元


def test_u1455_patch_applies_variable_and_node_config():
    draft = {
        "version": 3,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            {"id": "n1", "type": "ai_decision", "config": {"confidenceThreshold": 0.6}},
        ],
        "edges": [],
    }
    out = patch_candidate(
        changes=[
            Change(param_key="approval_limit", from_value="500", to_value=800),
            Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.8, node_id="n1"),
        ],
        latest_draft=draft,
        base_version=3,
        graph_snapshot=draft,  # 干净：latest 草稿 == 发布基线（内容一致）
    )
    assert out.ok, out.reason
    assert out.new_draft["variables"][0]["value"] == 800
    assert out.new_draft["nodes"][0]["config"]["confidenceThreshold"] == 0.8
    assert len(out.diff) == 2
    v, n = out.diff  # dict（JSON 友好，键为别名 from/to）
    assert v["param_key"] == "approval_limit" and v["path"] == "variables[0].value" and v["to"] == 800
    assert n["param_key"] == "node.confidenceThreshold" and n["node_id"] == "n1"
    assert n["path"] == "nodes[0].config.confidenceThreshold" and n["to"] == 0.8


def test_u1456_patch_dirty_draft_aborts_without_overwrite():
    draft = {
        "version": 3,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [],
        "edges": [],
    }
    snapshot = {
        "version": 3,
        "variables": [{"name": "approval_limit", "type": "number", "value": "400", "scope": "global"}],
        "nodes": [],
        "edges": [],
    }
    out = patch_candidate(
        changes=[Change(param_key="approval_limit", from_value="500", to_value=800)],
        latest_draft=draft,
        base_version=1,
        graph_snapshot=snapshot,  # 发布基线内容 ≠ latest 草稿 ⇒ 人工已改草稿（dirty）
    )
    assert not out.ok
    assert "草稿与候选基线不一致" in (out.reason or "")
    assert draft["variables"][0]["value"] == "500"  # 原草稿未被触碰


def test_u1457_patch_no_draft_aborts():
    out = patch_candidate(
        changes=[Change(param_key="approval_limit", from_value="500", to_value=800)],
        latest_draft=None,
        base_version=1,
    )
    assert not out.ok
    assert out.reason


def test_u1457b_patch_missing_snapshot_aborts_fail_closed():
    """候选 base_version 无对应发布（快照缺失）→ fail-closed 中止，不自动覆盖。"""
    draft = {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [],
        "edges": [],
    }
    out = patch_candidate(
        changes=[Change(param_key="approval_limit", from_value="500", to_value=800)],
        latest_draft=draft,
        base_version=7,
        graph_snapshot=None,
    )
    assert not out.ok
    assert "无法确认基线" in (out.reason or "")


def test_u1458_patch_malformed_targets_abort():
    draft = {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [{"id": "n1", "type": "ai_decision", "config": {"confidenceThreshold": 0.6}}],
        "edges": [],
    }
    # 图变量未命中（白名单内但草稿 variables 无此行）
    o1 = patch_candidate(
        changes=[Change(param_key="approval_limit", from_value="500", to_value=800)],
        latest_draft={**draft, "variables": []},
        base_version=1,
        graph_snapshot={**draft, "variables": []},
    )
    assert not o1.ok and "变量未命中" in (o1.reason or "")
    # 节点级缺 node_id：不自动猜节点
    o2 = patch_candidate(
        changes=[Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.8)],
        latest_draft=draft, base_version=1, graph_snapshot=draft,
    )
    assert not o2.ok and "不自动猜节点" in (o2.reason or "")
    # 节点不存在
    o3 = patch_candidate(
        changes=[Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.8, node_id="n9")],
        latest_draft=draft, base_version=1, graph_snapshot=draft,
    )
    assert not o3.ok and "节点未命中" in (o3.reason or "")
    # 非 ai_decision 节点（节点级参数只认 ai_decision）
    o4 = patch_candidate(
        changes=[Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.8, node_id="trigger-1")],
        latest_draft={
            **draft, "nodes": [{"id": "trigger-1", "type": "trigger", "config": {}}],
        },
        base_version=1,
        graph_snapshot={
            **draft, "nodes": [{"id": "trigger-1", "type": "trigger", "config": {}}],
        },
    )
    assert not o4.ok and o4.reason


def test_u1459_patch_monitor_gate_rows_skipped_not_blocking():
    draft = {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [],
        "edges": [],
    }
    out = patch_candidate(
        changes=[
            Change(param_key="approval_limit", from_value="500", to_value=800),
            Change(param_key="monitor.failure_rate.rate", from_value=0.5, to_value=0.3),
            Change(param_key="gate.run_error_rate", from_value=0.1, to_value=0.05),
        ],
        latest_draft=draft,
        base_version=1,
        graph_snapshot=draft,
    )
    assert out.ok, out.reason  # monitor/gate 行跳过不阻断图内参数
    assert len(out.diff) == 3
    skipped = [d for d in out.diff if d["skipped"]]
    assert len(skipped) == 2 and all("不自动" in (d["reason"] or "") for d in skipped)
    assert out.new_draft["variables"][0]["value"] == 800


# ---------------------------------------------------------------- U1460–U1470 REST 端点


def test_u1460_autoapply_idempotent_409():
    gid = _make_graph(1)
    _publish(gid)
    _configure_rollout(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1460"
    )
    r1 = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert r2.status_code == 409, r2.text  # 幂等（BP-D2）


def test_u1461_fail_closed_scope_gate_failed_not_published():
    gid = _make_graph(1)
    _publish(gid)
    cid = _inject_candidate(
        gid,
        1,
        [Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.8, node_id="ai_decision-1")],
        "refl-u1461",
    )
    resp = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},  # 只开变量类，候选却是节点类 ⇒ fail-closed
        headers=ADMIN,
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "failed"
    assert "未开启类别" in (run["reason"] or "")
    assert run["publish_version"] is None
    assert run["rollout_status"] is None
    # 未发布：发布版本列表仍只有 1
    assert client.get(f"/api/graphs/{gid}/rollout", headers=ADMIN).json()["status"] == "idle"


def test_u1462_full_chain_patch_shadow_gate_publish_canary():
    gid = _make_graph(1)
    _publish(gid)
    _configure_rollout(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1462"
    )
    resp = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    assert run["publish_version"] == 2
    assert run["rollout_status"] == "canary"
    stages = [s["stage"] for s in run["stages"]]
    assert stages == ["start", "patch", "shadow", "gate", "publish", "canary"]
    assert run["patch"]["diff"][0]["to"] == 800
    assert run["gate"]["skipped"] is True  # 无录制用例：skipped 不阻塞
    # 草稿已自动生效（值 500 → 800），灰度态 canary
    assert _draft_value(gid) == 800
    assert client.get(f"/api/graphs/{gid}/rollout", headers=ADMIN).json()["status"] == "canary"
    # 完整链日志可查（每圈证据）
    rows = client.get("/api/reflection/autoapply", headers=ADMIN).json()["items"]
    assert any(row["run_id"] == run["run_id"] for row in rows)


def test_u1463_processed_candidate_409():
    gid = _make_graph(1)
    _publish(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1463"
    )
    resp = client.put(
        f"/api/reflection/candidates/{cid}/decision",
        json={"status": "dismissed"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    r = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert r.status_code == 409, r.text  # 已处理候选不自动（U1463）


def test_u1464_golden_replay_blocked_failed_not_published():
    gid = _make_graph(1)
    _publish(gid)
    _configure_rollout(gid)
    _inject_mismatch_case(gid)  # 一条必不匹配用例 ⇒ 黄金回放 blocked
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1464"
    )
    resp = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "failed"
    assert "黄金回放未通过" in (run["reason"] or "")
    assert run["publish_version"] is None
    # 未发布：发布版本列表仍只有 1，灰度 idle
    assert client.get(f"/api/graphs/{gid}/rollout", headers=ADMIN).json()["status"] == "idle"


def test_u1465_abort_canary_rolls_back():
    gid = _make_graph(1)
    _publish(gid)
    _configure_rollout(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1465"
    )
    r = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    ab = client.post(f"/api/reflection/autoapply/{run_id}/abort", headers=ADMIN)
    assert ab.status_code == 200, ab.text
    run = ab.json()
    assert run["status"] == "aborted"
    assert run["rollout_status"] == "rolled_back"
    assert any(s["stage"] == "abort" and s["ok"] for s in run["stages"])
    # 灰度已被手动回滚
    assert client.get(f"/api/graphs/{gid}/rollout", headers=ADMIN).json()["status"] == "rolled_back"


def test_u1466_abort_404_and_idempotent():
    assert client.post("/api/reflection/autoapply/aa-999/abort", headers=ADMIN).status_code == 404
    # 已 aborted 的 run 再 abort 幂等返现状
    gid = _make_graph(1)
    _publish(gid)
    _configure_rollout(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1466"
    )
    r = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    run_id = r.json()["run_id"]
    assert client.post(f"/api/reflection/autoapply/{run_id}/abort", headers=ADMIN).status_code == 200
    again = client.post(f"/api/reflection/autoapply/{run_id}/abort", headers=ADMIN)
    assert again.status_code == 200 and again.json()["status"] == "aborted"


def test_u1467_list_detail_and_cross_tenant_isolation():
    gid = _make_graph(1)
    _publish(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1467"
    )
    r = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    detail = client.get(f"/api/reflection/autoapply/{run['run_id']}", headers=ADMIN)
    assert detail.status_code == 200 and detail.json()["candidate_id"] == cid
    # 跨租户：t2（admin-b）读 t1 的 run → 404，不泄漏存在性
    headers_b = _admin_b()
    assert client.get(f"/api/reflection/autoapply/{run['run_id']}", headers=headers_b).status_code == 404
    assert client.get("/api/reflection/autoapply", headers=headers_b).json()["items"] == []
    # viewer 只读：列表可看，启动/中止 403
    assert client.get("/api/reflection/autoapply", headers=VIEWER).status_code == 200
    assert (
        client.post(
            f"/api/reflection/candidates/{cid}/autoapply",
            json={"enabledScopes": ["graph_variable"]},
            headers=VIEWER,
        ).status_code
        == 403
    )


def test_u1468_config_projection():
    cfg = client.get("/api/reflection/autoapply/config", headers=ADMIN).json()
    assert cfg["availableScopes"] == ["graph_variable", "node_config"]
    assert cfg["structureAuto"] is False
    assert cfg["enabledScopes"] == []  # 缺省 fail-closed
    # 跑一次自动生效后，类别开关并入已开启集合
    gid = _make_graph(1)
    _publish(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1468"
    )
    assert (
        client.post(
            f"/api/reflection/candidates/{cid}/autoapply",
            json={"enabledScopes": ["graph_variable"]},
            headers=ADMIN,
        ).status_code
        == 201
    )
    cfg = client.get("/api/reflection/autoapply/config", headers=ADMIN).json()
    assert cfg["enabledScopes"] == ["graph_variable"]
    # 非法类别 422（fail-closed）
    gid2 = _make_graph(1)
    cid2 = _inject_candidate(
        gid2, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1468b"
    )
    assert (
        client.post(
            f"/api/reflection/candidates/{cid2}/autoapply",
            json={"enabledScopes": ["monitor_rule"]},
            headers=ADMIN,
        ).status_code
        == 422
    )


def test_u1469_dirty_draft_via_endpoint_failed_not_published():
    gid = _make_graph(1)
    _publish(gid)
    _update_draft_touched(gid)  # 人工改过 latest 草稿（approval_limit 500→600）
    cid = _inject_candidate(
        gid, 1,  # 候选基线=1 ≠ 当前草稿 ⇒ 脏草稿（人工已改）
        [Change(param_key="approval_limit", from_value="500", to_value=800)],
        "refl-u1469",
    )
    resp = client.post(
        f"/api/reflection/candidates/{cid}/autoapply",
        json={"enabledScopes": ["graph_variable"]},
        headers=ADMIN,
    )
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run["status"] == "failed"
    assert "草稿与候选基线不一致" in (run["reason"] or "")
    assert run["publish_version"] is None
    assert _draft_value(gid) == "600"  # 草稿未被自动覆盖（保留人工改动）


def test_u1470_reset_clears_autoapply_records():
    gid = _make_graph(1)
    _publish(gid)
    cid = _inject_candidate(
        gid, 1, [Change(param_key="approval_limit", from_value="500", to_value=800)], "refl-u1470"
    )
    assert (
        client.post(
            f"/api/reflection/candidates/{cid}/autoapply",
            json={"enabledScopes": ["graph_variable"]},
            headers=ADMIN,
        ).status_code
        == 201
    )
    assert client.get("/api/reflection/autoapply", headers=ADMIN).json()["items"]
    client.post("/api/demo/reset", headers=ADMIN)
    assert client.get("/api/reflection/autoapply", headers=ADMIN).json()["items"] == []

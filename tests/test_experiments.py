"""打包 BR（D77，docs/134）：A/B 实验对照判定（U1497–U1508，进程内 12 例）。

覆盖 docs/13：
- U1497 两比例 z 检验数学（已知数据集手算对拍；退化返 (None,None) 不炸）；
- U1498 arm_stats 与 gate 同口径（error 分母＝臂内全部、业务率分母＝有业务结果；
  cancelled 计入健康，is_healthy 同源；rate 经 business_rates 同一实现）；
- U1499 judge 三态（样本不足 collecting 不指 winner／|z|≤1.96 inconclusive／
  |z|>1.96 significant 且 winner＝低率臂）；
- U1500 required_samples 单调与边界（mde 越小所需越大；非法入参 None）；
- U1501 创建校验（graph 404／版本 422／rollout 非 idle 409／同图 running 409）
  与成功接线（canary stable=A/candidate=B/percent=trafficPercent、priorConfig 快照）；
- U1502 分流复用 RoutingStore.resolve 同一实现实证（hash_hit 预测两臂分落）＋
  无桶键事件 fail-safe 落 A；
- U1503 GET detail 活统计＋collecting 不指 winner＋splitActive 真/假；
- U1504 conclude adopt_b：verdict 冻结＋rollout full＋priorConfig 恢复；
  重复 conclude 409、viewer 403；
- U1505 conclude adopt_a：rolled_back＋priorConfig 恢复；guardrail 自动回滚后
  conclude 仍幂等可用；
- U1506 人可改判：verdict 指 A 而 action=adopt_b 仍执行，不一致照实落库；
- U1507 跨租户 404 不泄漏；reset 清实验档；
- U1508 start 显式版本校验（同传/相等/在版本列表内）＋缺省路径逐字不变。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.monitoring import BusinessOutcome
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import RunRecord
from atlas.routing import (
    ArmStats,
    CanaryRule,
    Experiment,
    RolloutConfig,
    RolloutError,
    arm_stats,
    evaluate_after_run,
    hash_hit,
    judge,
    required_samples,
    two_proportion_z,
)
from atlas.routing.gate import business_rates

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


# ---------- 公共助手 ----------

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
                 "approver": "",
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


def _publish_two_versions(v2_mark: str = "人工审批-v2") -> str:
    """建图 → v1 → 更新草稿（改名标记）→ v2，返回 graph_id。"""
    graph_id = client.post("/api/graphs", json=_human_graph()).json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/publish").status_code == 200
    store = tenant_registry.get("t1").graph_store
    store._graphs[graph_id] = _human_graph(v2_mark)  # 草稿更新后发内容不同的 v2
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 2
    return graph_id


def _body(graph_id: str, **overrides) -> dict:
    body = {
        "graphId": graph_id,
        "versionA": 1,
        "versionB": 2,
        "trafficPercent": 50,
        "primaryMetric": "run_error_rate",
        "minSamples": 30,
    }
    body.update(overrides)
    return body


def _create(graph_id: str, **overrides) -> dict:
    response = client.post("/api/experiments", json=_body(graph_id, **overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _inject_run(services, graph_id: str, version: int, *, healthy: bool = True, business=None):
    """直接向 monitoring 注入一条臂内运行（finished_at＝now，落实验窗口内）。"""
    nodes = [] if healthy else [
        NodeResult(node_id="tool-1", node_type="tool_call", status="failed")
    ]
    return services.monitoring.record_run(
        graph_id=graph_id, mode="sync", status="completed",
        started_at=datetime.now(timezone.utc).isoformat(), duration_ms=1.0,
        nodes=nodes, resolved_version=version, business=business,
    )


def _event_run(graph_id: str, order_key: str | None) -> dict:
    """POST 一次带 event 的运行并返回最新 RunRecord 投影。"""
    payload = {"amount": 10} if order_key is None else {"order_id": order_key, "amount": 10}
    inputs: dict = {"approvals": {"human-1": "approved"}}
    if order_key is not None:
        inputs["order_id"] = order_key
    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "api", "payload": payload}, "inputs": inputs},
    )
    assert response.status_code == 200, response.text
    items = client.get(
        "/api/monitoring/runs", params={"graph_id": graph_id, "limit": 1}
    ).json()["items"]
    assert items, "应有运行记录"
    return items[0]


# ---------- U1497 z 检验数学 ----------

def test_u1497_two_proportion_z_matches_hand_calculation():
    # pA=0.10(n=200) vs pB=0.20(n=200)：pooled p=0.15，se=√(0.15·0.85·0.01)≈0.0357071，
    # z=(0.20−0.10)/se≈2.8006，双侧 p≈0.0051（α=0.05 显著）。
    z, p_value = two_proportion_z(20, 200, 40, 200)
    assert z is not None and p_value is not None
    assert z == pytest.approx(2.8006, abs=1e-4)
    assert p_value == pytest.approx(0.0051, abs=1e-3)
    assert z > 0 and abs(z) > 1.96  # z 符号＝pB−pA 方向
    # 退化：任一臂 n=0／两臂全无事件（se=0）／两臂全事件（pooled=1，se=0）→ (None,None)
    assert two_proportion_z(0, 0, 5, 100) == (None, None)
    assert two_proportion_z(0, 100, 0, 100) == (None, None)
    assert two_proportion_z(100, 100, 100, 100) == (None, None)


# ---------- U1498 arm_stats 口径 ----------

def _arm_run(run_id: str, version: int | None, *, status: str = "completed",
             failed_node: bool = False, business: BusinessOutcome | None = None) -> RunRecord:
    now = datetime.now(timezone.utc).isoformat()
    nodes = [
        NodeResult(node_id="tool-1", node_type="tool_call", status="failed")
    ] if failed_node else []
    return RunRecord(
        id=run_id, graph_id="graph-exp", mode="sync", status=status,
        started_at=now, finished_at=now, duration_ms=1.0, nodes=nodes,
        resolved_version=version, business=business,
    )


def test_u1498_arm_stats_shares_gate_denominators():
    runs = [
        # 臂 B（version=2）：健康＋全额退（无升级无差异）
        _arm_run("run-b1", 2, business=BusinessOutcome(
            auto_refunded=True, expected_amount=299, refunded_amount=299)),
        # 臂 B：failed 节点（不健康）＋人工升级
        _arm_run("run-b2", 2, failed_node=True, business=BusinessOutcome(
            manual_escalated=True, expected_amount=100, refunded_amount=100)),
        # 臂 B：cancelled 计入健康、无业务结果（不入业务分母）
        _arm_run("run-b3", 2, status="cancelled"),
        # 臂 A（version=1）：不进 B 臂统计
        _arm_run("run-a1", 1),
    ]
    stats = arm_stats(runs, 2)
    assert stats.samples == 3  # error 分母＝臂内全部
    assert stats.errorCount == 1  # 仅 run-b2；cancelled 计入健康（is_healthy 同源）
    assert stats.errorRate == pytest.approx(1 / 3)
    assert stats.businessSamples == 2  # 业务率分母＝有业务结果
    assert stats.manualCount == 1 and stats.manualRate == pytest.approx(0.5)
    assert stats.diffCount == 0 and stats.diffRate == pytest.approx(0.0)
    # rate 与 business_rates 同一实现（BR-D3）
    arm = [run for run in runs if run.resolved_version == 2]
    manual_rate, diff_rate, biz_samples = business_rates(arm)
    assert stats.manualRate == manual_rate
    assert stats.diffRate == diff_rate
    assert stats.businessSamples == biz_samples


# ---------- U1499 judge 三态 ----------

def _exp(min_samples: int = 30, metric: str = "run_error_rate") -> Experiment:
    return Experiment(
        id="exp-t", graphId="graph-t", versionA=1, versionB=2,
        primaryMetric=metric,  # type: ignore[arg-type]
        minSamples=min_samples,
        createdAt=datetime.now(timezone.utc).isoformat(),
    )


def test_u1499_judge_three_states_and_lower_rate_wins():
    # 样本不足 → collecting 不指 winner
    verdict = judge(_exp(min_samples=30),
                    ArmStats(samples=10, errorCount=1),
                    ArmStats(samples=40, errorCount=4))
    assert verdict.status == "collecting"
    assert verdict.winner is None and verdict.z is None
    # 样本足但无差异（pA=pB=0.10，n=200）→ inconclusive
    verdict = judge(_exp(),
                    ArmStats(samples=200, errorCount=20),
                    ArmStats(samples=200, errorCount=20))
    assert verdict.status == "inconclusive"
    assert verdict.winner is None
    assert verdict.z == pytest.approx(0.0)
    # pA=0.10 vs pB=0.20（n=200）→ significant，winner＝低率臂 A
    verdict = judge(_exp(),
                    ArmStats(samples=200, errorCount=20),
                    ArmStats(samples=200, errorCount=40))
    assert verdict.status == "significant"
    assert verdict.winner == "A"
    assert verdict.rateA == pytest.approx(0.10) and verdict.rateB == pytest.approx(0.20)
    assert verdict.pValue is not None and verdict.pValue < 0.05
    # 反转：低率臂为 B → winner＝B
    verdict = judge(_exp(),
                    ArmStats(samples=200, errorCount=40),
                    ArmStats(samples=200, errorCount=20))
    assert verdict.status == "significant"
    assert verdict.winner == "B"


# ---------- U1500 required_samples ----------

def test_u1500_required_samples_monotonic_and_bounds():
    small_mde = required_samples(0.10, 0.02)
    large_mde = required_samples(0.10, 0.05)
    assert small_mde is not None and large_mde is not None
    assert small_mde > large_mde  # mde 越小所需样本越大
    assert required_samples(0.0, 0.05) is None  # baseline 出开区间
    assert required_samples(1.0, 0.05) is None
    assert required_samples(0.10, 0.0) is None  # mde≤0
    assert required_samples(0.10, -0.01) is None
    assert required_samples(0.99, 0.05) is None  # p̄=baseline+δ/2 ≥ 1


# ---------- U1501 创建校验与接线 ----------

def test_u1501_create_validation_and_canary_wiring():
    # graph 404
    missing = client.post("/api/experiments", json=_body("graph-no-such"))
    assert missing.status_code == 404
    graph_id = _publish_two_versions()
    # A==B 422
    assert client.post(
        "/api/experiments", json=_body(graph_id, versionB=1)
    ).status_code == 422
    # 版本未发布 422
    assert client.post(
        "/api/experiments", json=_body(graph_id, versionA=99)
    ).status_code == 422
    # rollout 非 idle 409
    busy = _publish_two_versions()
    client.put(f"/api/graphs/{busy}/rollout",
               json={"strategy": "progressive", "rules": [{"to": "canary", "percent": 100}]})
    assert client.post(f"/api/graphs/{busy}/rollout/start").status_code == 200
    assert client.post("/api/experiments", json=_body(busy)).status_code == 409
    # 成功：先 PUT 原配置（priorConfig 快照源）
    assert client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "progressive", "rules": [{"to": "canary", "percent": 80}]},
    ).status_code == 200
    configured = client.get(f"/api/graphs/{graph_id}/rollout").json()["config"]
    exp = _create(graph_id)
    assert exp["status"] == "running"
    assert exp["versionA"] == 1 and exp["versionB"] == 2
    assert exp["priorConfig"] == configured  # 启动前配置已快照（BR-D5）
    rollout = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert rollout["status"] == "canary"
    assert rollout["stable"] == 1 and rollout["candidate"] == 2
    assert rollout["config"]["rules"][0]["to"] == "canary"
    assert rollout["config"]["rules"][0]["percent"] == 50  # percent＝trafficPercent
    # 同图 running 互斥 409（BR-D6）
    assert client.post("/api/experiments", json=_body(graph_id)).status_code == 409


# ---------- U1502 分流复用实证 ----------

def test_u1502_traffic_reuses_routing_resolve_with_failsafe():
    graph_id = _publish_two_versions()
    _create(graph_id, trafficPercent=50)
    # 用路由层同一 hash_hit 预测分桶：命中 → candidate(B)，未命中 → stable(A)
    key_b = next(k for k in (f"exp-order-{i}" for i in range(500))
                 if hash_hit(graph_id, k, 50))
    key_a = next(k for k in (f"exp-order-{i}" for i in range(500))
                 if not hash_hit(graph_id, k, 50))
    assert _event_run(graph_id, key_b)["resolved_version"] == 2
    assert _event_run(graph_id, key_a)["resolved_version"] == 1
    # 无桶键（无 order_id/id）→ fail-safe 落 stable(A)，绝不推新版本
    assert _event_run(graph_id, None)["resolved_version"] == 1


# ---------- U1503 detail 活统计与 splitActive ----------

def test_u1503_detail_live_stats_collecting_and_split_active():
    graph_id = _publish_two_versions()
    exp = _create(graph_id)  # minSamples=30
    services = tenant_registry.get("t1")
    for _ in range(3):
        _inject_run(services, graph_id, 1)
    for _ in range(2):
        _inject_run(services, graph_id, 2)
    detail = client.get(f"/api/experiments/{exp['id']}").json()
    assert detail["armStats"]["A"]["samples"] == 3
    assert detail["armStats"]["B"]["samples"] == 2
    assert detail["verdict"]["status"] == "collecting"
    assert detail["verdict"]["winner"] is None  # collecting 期不指 winner
    assert detail["splitActive"] is True
    # 手动 rollback 后 splitActive 假
    assert client.post(
        f"/api/graphs/{graph_id}/rollout/rollback", json={"reason": "手动停"}
    ).status_code == 200
    detail = client.get(f"/api/experiments/{exp['id']}").json()
    assert detail["splitActive"] is False


# ---------- U1504 conclude adopt_b ----------

def test_u1504_conclude_adopt_b_promotes_and_restores_prior_config():
    graph_id = _publish_two_versions()
    assert client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "progressive", "rules": [{"to": "canary", "percent": 80}]},
    ).status_code == 200
    configured = client.get(f"/api/graphs/{graph_id}/rollout").json()["config"]
    exp = _create(graph_id)
    concluded = client.post(
        f"/api/experiments/{exp['id']}/conclude",
        json={"action": "adopt_b", "reason": "B 指标不差，全量"},
    )
    assert concluded.status_code == 200, concluded.text
    body = concluded.json()
    assert body["status"] == "concluded"
    assert body["conclusionAction"] == "adopt_b"
    assert body["conclusionReason"] == "B 指标不差，全量"
    assert body["concludedAt"] is not None
    assert body["verdict"] is not None and body["verdict"]["status"] == "collecting"
    rollout = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert rollout["status"] == "full"  # adopt_b → promote
    assert rollout["config"] == configured  # priorConfig 恢复（BR-D5）
    detail = client.get(f"/api/experiments/{exp['id']}").json()
    assert detail["verdict"] == body["verdict"]  # verdict 冻结，不再活算
    assert detail["splitActive"] is False
    # 重复 conclude 409
    assert client.post(
        f"/api/experiments/{exp['id']}/conclude", json={"action": "adopt_b"}
    ).status_code == 409
    # viewer 只读 403
    viewer = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    assert client.post(
        f"/api/experiments/{exp['id']}/conclude",
        headers={"Authorization": f"Bearer {viewer}"},
        json={"action": "adopt_a"},
    ).status_code == 403


# ---------- U1505 conclude adopt_a ----------

def test_u1505_conclude_adopt_a_rolls_back_and_idempotent_after_auto_rollback():
    # 手动路径：adopt_a → rolled_back＋priorConfig 恢复
    graph_id = _publish_two_versions()
    assert client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "progressive", "rules": [{"to": "canary", "percent": 80}]},
    ).status_code == 200
    configured = client.get(f"/api/graphs/{graph_id}/rollout").json()["config"]
    exp = _create(graph_id)
    assert client.post(
        f"/api/experiments/{exp['id']}/conclude", json={"action": "adopt_a"}
    ).status_code == 200
    rollout = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert rollout["status"] == "rolled_back"
    assert rollout["config"] == configured  # priorConfig 恢复
    # guardrail 自动回滚后 conclude adopt_a 仍幂等可用（实验 gate 随 priorConfig 携带）
    graph2 = _publish_two_versions()
    assert client.put(
        f"/api/graphs/{graph2}/rollout",
        json={
            "strategy": "progressive",
            "rules": [{"to": "canary", "percent": 100}],
            "gate": {
                "observeMinutes": 60,
                "autoRollback": True,
                "minSamples": 3,
                "metrics": [{"id": "run_error_rate", "threshold": 0.02}],
            },
        },
    ).status_code == 200
    configured2 = client.get(f"/api/graphs/{graph2}/rollout").json()["config"]
    exp2 = _create(graph2)
    services = tenant_registry.get("t1")
    for _ in range(3):  # candidate(B) 连续不健康 → 越阈自动回滚
        record = _inject_run(services, graph2, 2, healthy=False)
        evaluate_after_run(services, record)
    rolled = client.get(f"/api/graphs/{graph2}/rollout").json()
    assert rolled["status"] == "rolled_back"
    assert rolled["rollbackActor"] == "auto"
    done = client.post(
        f"/api/experiments/{exp2['id']}/conclude", json={"action": "adopt_a"}
    )
    assert done.status_code == 200, done.text  # rollback 幂等不炸
    assert done.json()["status"] == "concluded"
    assert client.get(f"/api/graphs/{graph2}/rollout").json()["config"] == configured2


# ---------- U1506 人可改判 ----------

def test_u1506_human_override_recorded_as_is():
    graph_id = _publish_two_versions()
    exp = _create(graph_id, minSamples=10)
    services = tenant_registry.get("t1")
    for _ in range(20):  # 臂 A 全健康（错误率 0）
        _inject_run(services, graph_id, 1, healthy=True)
    for index in range(20):  # 臂 B 半失败（错误率 0.5）→ 显著且 winner=A
        _inject_run(services, graph_id, 2, healthy=index >= 10)
    detail = client.get(f"/api/experiments/{exp['id']}").json()
    assert detail["verdict"]["status"] == "significant"
    assert detail["verdict"]["winner"] == "A"
    concluded = client.post(
        f"/api/experiments/{exp['id']}/conclude",
        json={"action": "adopt_b", "reason": "业务原因坚持上 B"},
    )
    assert concluded.status_code == 200, concluded.text
    body = concluded.json()
    assert body["conclusionAction"] == "adopt_b"
    assert body["verdict"]["winner"] == "A"  # 判定与人决不一致照实落库（BR-D4）
    detail = client.get(f"/api/experiments/{exp['id']}").json()
    assert detail["verdict"]["winner"] == "A"
    assert detail["conclusionAction"] == "adopt_b"
    assert client.get(f"/api/graphs/{graph_id}/rollout").json()["status"] == "full"


# ---------- U1507 跨租户与 reset ----------

def test_u1507_cross_tenant_isolation_and_reset():
    graph_id = _publish_two_versions()
    exp = _create(graph_id)
    t2 = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    t2_headers = {"Authorization": f"Bearer {t2}"}
    assert client.get(f"/api/experiments/{exp['id']}", headers=t2_headers).status_code == 404
    listed = client.get("/api/experiments", headers=t2_headers).json()["experiments"]
    assert all(item["id"] != exp["id"] for item in listed)
    assert client.post(
        f"/api/experiments/{exp['id']}/conclude",
        headers=t2_headers, json={"action": "adopt_a"},
    ).status_code == 404
    # t1 自身访问不受 t2 影响
    assert client.get(f"/api/experiments/{exp['id']}").status_code == 200
    # reset 清实验档
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get("/api/experiments").json()["experiments"] == []
    assert client.get(f"/api/experiments/{exp['id']}").status_code == 404


# ---------- U1508 start 显式版本 ----------

def test_u1508_start_explicit_versions_and_default_unchanged():
    services = tenant_registry.get("t1")
    store = services.routing_store
    store.configure("exp-start-1", RolloutConfig(rules=[CanaryRule(percent=50)]))
    with pytest.raises(RolloutError, match="须同时指定或同时省略"):
        store.start("exp-start-1", [1, 2], stable=1)  # 只传一个
    with pytest.raises(RolloutError, match="同一版本"):
        store.start("exp-start-1", [1, 2], stable=1, candidate=1)  # 相等
    with pytest.raises(RolloutError, match="不在该图发布版本列表内"):
        store.start("exp-start-1", [1, 2], stable=3, candidate=2)  # stable 不在
    with pytest.raises(RolloutError, match="不在该图发布版本列表内"):
        store.start("exp-start-1", [1, 2], stable=1, candidate=3)  # candidate 不在
    # 校验失败不落状态：仍 idle，显式两版可正常启动
    state = store.start("exp-start-1", [1, 2, 3], stable=2, candidate=3)
    assert state.stable == 2 and state.candidate == 3
    assert state.status == "canary"
    # 缺省路径逐字不变：取最新两发布版（versions[-2], versions[-1]）
    store.configure("exp-start-2", RolloutConfig(rules=[CanaryRule(percent=50)]))
    state = store.start("exp-start-2", [1, 2, 3])
    assert state.stable == 2 and state.candidate == 3

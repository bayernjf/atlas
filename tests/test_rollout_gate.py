"""M9 批 3（U58）：业务结果指标 + 灰度门控自动回滚（monitoring/business + routing/gate）。

覆盖 docs/13 U58：
① extract_business 四形状（refunded/human_review/金额回退与差异/无业务结果 None）；
② metrics business 段三率（全局/per_graph/per_version，样本 0 为 null）；
③ canary + autoRollback 越阈（run_error_rate / manual_escalation_rate /
  refund_amount_diff_rate，样本≥minSamples、观察窗内）→ 自动 rollback(actor=auto)
  + rollout_gate critical 告警带 action，此后新 event 全落 stable；
④ 健康运行/样本不足/autoRollback=false/非 candidate 运行均不回滚；
⑤ 全仓无自动 promote；
⑥ 回滚不产生补偿动作（只切流）；
⑦ 门控仅在真实运行 record_run 之后触发（API 端到端承载），routing→monitoring 单向。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.monitoring import BusinessOutcome, extract_business, summarize
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import RunRecord
from atlas.routing import (
    GateConfig,
    GateMetric,
    InternalRule,
    RolloutConfig,
    evaluate_after_run,
)

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


# ---------- ① extract_business 纯函数 ----------

def _graph_with_tool() -> dict:
    return {"nodes": [
        {"id": "trigger-1", "type": "trigger"},
        {"id": "tool-1", "type": "tool_call"},
    ]}


def test_extract_business_auto_refund_full_amount_no_diff():
    outputs = {
        "trigger-1": {"context": {"payload": {"order_id": "12345", "amount": 299}}},
        "tool-1": {"result": {"order_id": "12345", "status": "refunded"}, "action_status": "SUCCEEDED"},
    }
    outcome = extract_business(_graph_with_tool(), outputs)
    assert outcome is not None
    assert outcome.auto_refunded is True
    assert outcome.manual_escalated is False
    assert outcome.expected_amount == 299
    assert outcome.refunded_amount == 299  # demo shop 不回金额 → 回退 expected（全额退）
    assert outcome.amount_diff is False


def test_extract_business_manual_escalation():
    outputs = {
        "trigger-1": {"context": {"payload": {"order_id": "12346", "amount": 5000}}},
        "tool-1": {"result": {"order_id": "12346", "status": "human_review"}},
    }
    outcome = extract_business(_graph_with_tool(), outputs)
    assert outcome is not None
    assert outcome.manual_escalated is True and outcome.auto_refunded is False
    assert outcome.amount_diff is False


def test_extract_business_amount_diff_when_refund_returns_amount():
    outputs = {
        "trigger-1": {"context": {"payload": {"amount": 299}}},
        "tool-1": {"result": {"status": "refunded", "amount": 280}},
    }
    outcome = extract_business(_graph_with_tool(), outputs)
    assert outcome is not None
    assert outcome.refunded_amount == 280 and outcome.expected_amount == 299
    assert outcome.amount_diff is True


def test_extract_business_none_when_no_business_result_and_event_payload_fallback():
    # message/send 成功结果不含退款/人工终态 → None
    outputs = {
        "trigger-1": {"context": {"payload": {}}},
        "tool-1": {"result": {"status": "SENT"}, "action_status": "SUCCEEDED"},
    }
    assert extract_business(_graph_with_tool(), outputs) is None

    # trigger 无 amount 时回退入站 event.payload.amount（bool 金额被排除）
    outputs_review = {
        "trigger-1": {"context": {"payload": {"amount": True}}},
        "tool-1": {"result": {"status": "human_review"}},
    }
    outcome = extract_business(
        _graph_with_tool(), outputs_review, event_payload={"amount": 5000}
    )
    assert outcome is not None and outcome.manual_escalated is True
    assert outcome.expected_amount == 5000.0


# ---------- ② metrics business 段聚合 ----------

def _run(run_id: str, graph_id: str, version, business: BusinessOutcome | None) -> RunRecord:
    now = datetime.now(timezone.utc).isoformat()
    return RunRecord(
        id=run_id, graph_id=graph_id, mode="sync", status="completed",
        started_at=now, finished_at=now, duration_ms=1.0, nodes=[],
        resolved_version=version, business=business,
    )


def test_summarize_business_rates_and_grouping():
    runs = [
        _run("run-1", "graph-1", 2, BusinessOutcome(auto_refunded=True, expected_amount=299, refunded_amount=299)),
        _run("run-2", "graph-1", 2, BusinessOutcome(auto_refunded=True, expected_amount=128, refunded_amount=128)),
        _run("run-3", "graph-1", 2, BusinessOutcome(manual_escalated=True, expected_amount=5000, refunded_amount=5000)),
        _run("run-4", "graph-1", None, None),  # 草稿/手动运行无业务结果，不入分母
    ]
    report = summarize(runs)["business"]
    assert report["auto_refund_rate"] == pytest.approx(2 / 3)
    assert report["manual_escalation_rate"] == pytest.approx(1 / 3)
    assert report["refund_amount_diff_rate"] == 0
    per_version = {(p["graph_id"], p["resolved_version"]): p for p in report["per_version"]}
    assert per_version[("graph-1", 2)]["samples"] == 3
    assert per_version[("graph-1", 2)]["auto_refund_rate"] == pytest.approx(2 / 3)
    assert ("graph-1", None) not in per_version  # 无业务结果 run 不进 per_version


def test_summarize_business_null_when_no_samples():
    now = datetime.now(timezone.utc).isoformat()
    runs = [RunRecord(
        id="run-1", graph_id="graph-1", mode="sync", status="completed",
        started_at=now, finished_at=now, duration_ms=1.0, nodes=[],
    )]
    report = summarize(runs)["business"]
    assert report["auto_refund_rate"] is None
    assert report["manual_escalation_rate"] is None
    assert report["refund_amount_diff_rate"] is None
    assert report["per_graph"] == [] and report["per_version"] == []


# ---------- ③④⑤ services 级门控求值 ----------

def _start_canary(gate: GateConfig, *, graph_id: str = "graph-1", versions: list[int] | None = None):
    """配 internal 全量 candidate + 指定 gate，启动 canary；返 services/snapshot。"""
    services = tenant_registry.get("t1")
    config = RolloutConfig(rules=[InternalRule(tenants=["t1"])], gate=gate)
    services.routing_store.configure(graph_id, config)
    services.routing_store.start(graph_id, versions or [1, 2])
    return services


def _gate(**metrics_kwargs) -> GateConfig:
    metrics = [GateMetric(**item) for item in metrics_kwargs["metrics"]]
    return GateConfig(
        observeMinutes=60,
        autoRollback=metrics_kwargs.get("autoRollback", True),
        minSamples=metrics_kwargs.get("minSamples", 3),
        metrics=metrics,
    )


def _candidate_run(services, graph_id: str, *, version=2, healthy=True, business=None):
    nodes = [] if healthy else [NodeResult(node_id="tool-1", node_type="tool_call", status="failed")]
    status = "completed"
    record = services.monitoring.record_run(
        graph_id=graph_id, mode="sync", status=status,
        started_at=datetime.now(timezone.utc).isoformat(), duration_ms=1.0,
        nodes=nodes, resolved_version=version, business=business,
    )
    evaluate_after_run(services, record)
    return record


def test_gate_healthy_runs_never_rollback_or_promote():
    services = _start_canary(_gate(metrics=[{"id": "run_error_rate", "threshold": 0.02}]))
    for _ in range(5):
        _candidate_run(services, "graph-1", healthy=True)
    snap = services.routing_store.snapshot("graph-1")
    assert snap.status == "canary"  # 无自动 promote
    assert services.monitoring.list_alerts() == [] or all(
        a.rule_id != "rollout_gate" for a in services.monitoring.list_alerts()
    )


def test_gate_insufficient_samples_does_not_rollback():
    services = _start_canary(
        _gate(minSamples=3, metrics=[{"id": "run_error_rate", "threshold": 0.02}])
    )
    _candidate_run(services, "graph-1", healthy=False)
    _candidate_run(services, "graph-1", healthy=False)  # 仅 2 条 < minSamples 3
    assert services.routing_store.snapshot("graph-1").status == "canary"
    assert all(a.rule_id != "rollout_gate" for a in services.monitoring.list_alerts())


def test_gate_run_error_rate_breach_auto_rollbacks_with_action_alert():
    services = _start_canary(
        _gate(minSamples=3, metrics=[{"id": "run_error_rate", "threshold": 0.02}])
    )
    _candidate_run(services, "graph-1", healthy=True)
    _candidate_run(services, "graph-1", healthy=True)
    _candidate_run(services, "graph-1", healthy=False)  # 1/3 ≈ 33% > 2%

    snap = services.routing_store.snapshot("graph-1")
    assert snap.status == "rolled_back"
    assert snap.rollback_actor == "auto"
    gate_alerts = [a for a in services.monitoring.list_alerts() if a.rule_id == "rollout_gate"]
    assert len(gate_alerts) == 1
    alert = gate_alerts[0]
    assert alert.severity == "critical"
    assert alert.action == {
        "type": "rollback", "from_version": 2, "to_version": 1,
        "reason": alert.action["reason"], "actor": "auto",
    }
    assert "run_error_rate" in alert.action["reason"]


def test_gate_manual_escalation_rate_breach_auto_rollbacks():
    services = _start_canary(
        _gate(minSamples=2, metrics=[{"id": "manual_escalation_rate", "threshold": 0.10}])
    )
    review = BusinessOutcome(manual_escalated=True, expected_amount=5000, refunded_amount=5000)
    _candidate_run(services, "graph-1", business=review)
    _candidate_run(services, "graph-1", business=review)  # 2/2 = 100% > 10%

    snap = services.routing_store.snapshot("graph-1")
    assert snap.status == "rolled_back" and snap.rollback_actor == "auto"
    alert = next(a for a in services.monitoring.list_alerts() if a.rule_id == "rollout_gate")
    assert alert.action["from_version"] == 2 and alert.action["to_version"] == 1
    assert "manual_escalation_rate" in alert.message


def test_gate_amount_diff_rate_breach_auto_rollbacks():
    services = _start_canary(
        _gate(minSamples=2, metrics=[{"id": "refund_amount_diff_rate", "threshold": 0.005}])
    )
    diff = BusinessOutcome(auto_refunded=True, expected_amount=299, refunded_amount=280, amount_diff=True)
    _candidate_run(services, "graph-1", business=diff)
    _candidate_run(services, "graph-1", business=diff)  # 2/2 = 100% > 0.5%
    assert services.routing_store.snapshot("graph-1").status == "rolled_back"


def test_gate_no_rollback_when_disabled_or_not_candidate():
    # autoRollback=false
    services = _start_canary(
        _gate(minSamples=1, autoRollback=False, metrics=[{"id": "run_error_rate", "threshold": 0.02}])
    )
    _candidate_run(services, "graph-1", healthy=False)
    assert services.routing_store.snapshot("graph-1").status == "canary"

    # 非 candidate（resolved_version=None 草稿运行）不触发
    record = services.monitoring.record_run(
        graph_id="graph-1", mode="sync", status="completed",
        started_at=datetime.now(timezone.utc).isoformat(), duration_ms=1.0,
        nodes=[NodeResult(node_id="x", node_type="tool_call", status="failed")],
        resolved_version=None,
    )
    evaluate_after_run(services, record)
    assert services.routing_store.snapshot("graph-1").status == "canary"

    # stable 版本（v1）运行不触发
    _candidate_run(services, "graph-1", version=1, healthy=False)
    assert services.routing_store.snapshot("graph-1").status == "canary"


def test_gate_rollback_is_idempotent_no_duplicate_alert():
    services = _start_canary(
        _gate(minSamples=2, metrics=[{"id": "run_error_rate", "threshold": 0.02}])
    )
    _candidate_run(services, "graph-1", healthy=False)
    _candidate_run(services, "graph-1", healthy=False)
    assert services.routing_store.snapshot("graph-1").status == "rolled_back"
    # 回滚后再来 candidate 运行（窗内），不重复产 rollout_gate 告警（状态已 rolled_back 短路）
    _candidate_run(services, "graph-1", healthy=False)
    gate_alerts = [a for a in services.monitoring.list_alerts() if a.rule_id == "rollout_gate"]
    assert len(gate_alerts) == 1


# ---------- ③⑦ HTTP 端到端：真实 event 运行越阈自动回滚，回后新流量落 stable ----------

def _message_graph(tool: str) -> dict:
    return {
        "version": 1, "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "msg-1", "type": "tool_call", "name": "通知",
             "config": {"tool": tool, "params": json.dumps(
                 {"channel": "email", "to": ["ops@example.com"], "subject": "s", "body": "ok"})}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "msg-1"}],
    }


def _event_run(client, graph_id: str):
    return client.post(
        f"/api/graphs/{graph_id}/run",
        json={
            "event": {"channel": "webhook", "payload": {"order_id": "12345", "amount": 299}},
            "inputs": {"order_id": "12345"},
        },
    )


def test_http_candidate_failures_auto_rollback_then_new_events_go_stable():
    good = _message_graph("message/send")
    graph_id = client.post("/api/graphs", json=good).json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 1

    # v2 草稿把工具改成未注册适配器（运行期节点 failed → 不健康），发布 v2
    bad = _message_graph("nonexistent/tool")
    tenant_registry.get("t1").graph_store._graphs[graph_id] = bad
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 2

    # internal 租户全量 candidate + run_error_rate 门控（阈值 2%、样本 3）
    config = {
        "strategy": "progressive",
        "rules": [{"to": "internal", "tenants": ["t1"]}],
        "gate": {
            "observeMinutes": 60, "autoRollback": True, "minSamples": 3,
            "metrics": [{"id": "run_error_rate", "threshold": 0.02}],
        },
        "inFlightPolicy": "pin-to-version",
    }
    assert client.put(f"/api/graphs/{graph_id}/rollout", json=config).status_code == 200
    assert client.post(f"/api/graphs/{graph_id}/rollout/start").status_code == 200

    # 连续 3 次 event 全落 candidate=2（坏工具），第 3 次后越阈自动回滚
    for _ in range(3):
        res = _event_run(client, graph_id)
        assert res.status_code == 200, res.text

    snap = client.get(f"/api/graphs/{graph_id}/rollout").json()
    assert snap["status"] == "rolled_back"
    assert snap["rollbackActor"] == "auto"
    alerts = client.get("/api/alerts").json()["items"]
    gate_alert = next(a for a in alerts if a["rule_id"] == "rollout_gate")
    assert gate_alert["severity"] == "critical"
    assert gate_alert["action"]["type"] == "rollback"
    assert gate_alert["action"]["from_version"] == 2
    assert gate_alert["action"]["to_version"] == 1

    # 回滚后新 event 全落 stable=1（好工具成功，健康）；监控里最近一条 resolved_version=1
    after = _event_run(client, graph_id)
    assert after.status_code == 200
    latest = client.get(f"/api/monitoring/runs?graph_id={graph_id}").json()["items"][0]
    assert latest["resolved_version"] == 1


def test_put_draft_iterates_versions_on_same_graph_without_touching_v1():
    """M9 发布流闭环：PUT 覆盖 latest 草稿 → publish v2，v1 不可变快照保持原样。"""
    good = _message_graph("message/send")
    graph_id = client.post("/api/graphs", json=good).json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 1

    # 改草稿（坏工具）PUT 覆盖同 id，再发布 v2
    bad = _message_graph("nonexistent/tool")
    put_res = client.put(f"/api/graphs/{graph_id}", json=bad)
    assert put_res.status_code == 200, put_res.text
    assert client.post(f"/api/graphs/{graph_id}/publish").json()["releaseVersion"] == 2

    versions = client.get(f"/api/graphs/{graph_id}/versions").json()["items"]
    assert versions == [1, 2]
    # v1 冻结快照仍是好工具，latest 草稿是坏工具
    v1 = client.get(f"/api/graphs/{graph_id}?releaseVersion=1").json()
    latest = client.get(f"/api/graphs/{graph_id}").json()
    v1_tool = next(n for n in v1["nodes"] if n["id"] == "msg-1")["config"]["tool"]
    latest_tool = next(n for n in latest["nodes"] if n["id"] == "msg-1")["config"]["tool"]
    assert v1_tool == "message/send"
    assert latest_tool == "nonexistent/tool"

    # 不存在图（含跨租户不泄漏）→ 404
    assert client.put("/api/graphs/graph-999999", json=good).status_code == 404

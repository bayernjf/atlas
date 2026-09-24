# -*- coding: utf-8 -*-
"""D28 告警静默 / 未确认升级 / 值班轮换（docs/33 §5；U285–U299）。

覆盖三层：
- 纯函数（alerts.apply_escalation、silences.silence_matches/current_assignee/is_silence_active）；
- 进程内 MonitoringStore：静默命中抑制并计数、删除/过期恢复、读时惰性升级、值班指派/合并不指派/reset；
- REST（/api/monitoring/silences、/api/monitoring/on-call）：CRUD、active 过滤、中文 422、权限矩阵、
  轮换取模/空表 409、规则 escalation_ack_minutes 往返。
- PG（U299，integration）：docs/59 F-2（迁移 024）起静默/值班/assignee 落 PG、跨重启保留、suppressed_count 落库；
  读时惰性升级回写 severity/escalated_at。持久化细项另见 U652–U659。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.monitoring.alerts import Alert, RuleConfig, apply_escalation
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import MonitoringStore
from atlas.monitoring.silences import (
    OnCallEmpty,
    Silence,
    current_assignee,
    is_silence_active,
    silence_matches,
)

client = TestClient(app)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed_node_run(store: MonitoringStore, graph_id: str = "g1") -> None:
    """造一次节点失败运行：触发内置 node_failed（warning）告警。"""
    store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="completed",
        started_at=_now(),
        duration_ms=5.0,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="failed")],
    )


def _valid_rules(escalation: int | None = None) -> dict:
    return {
        "run_error": {"enabled": True},
        "node_failed": {"enabled": True},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
        "custom": [],
        "escalation_ack_minutes": escalation,
    }


# ---------- U285 silence_matches：规则/图/全局 + 过期 ----------


def test_u285_silence_matches_rule_graph_global_and_expiry():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    now = _now()

    rule = Silence(
        id="sil-1", rule_id="node_failed", graph_id=None, reason="r",
        created_by="a", created_at=now, expires_at=future,
    )
    assert silence_matches(rule, "node_failed", "g1", now) is True
    assert silence_matches(rule, "run_error", "g1", now) is False  # 规则不符

    graph = Silence(
        id="sil-2", rule_id=None, graph_id="g1", reason="r",
        created_by="a", created_at=now, expires_at=future,
    )
    assert silence_matches(graph, "any_rule", "g1", now) is True
    assert silence_matches(graph, "any_rule", "g2", now) is False  # 图不符

    global_sil = Silence(
        id="sil-3", rule_id=None, graph_id=None, reason="r",
        created_by="a", created_at=now, expires_at=future,
    )
    assert silence_matches(global_sil, "anything", "gx", now) is True

    expired = Silence(
        id="sil-4", rule_id=None, graph_id=None, reason="r",
        created_by=past, created_at=past, expires_at=past,
    )
    assert silence_matches(expired, "anything", "gx", now) is False
    assert is_silence_active(rule) is True
    assert is_silence_active(expired) is False


# ---------- U286 current_assignee：空表 / 取模 ----------


def test_u286_current_assignee_empty_and_modulo():
    from atlas.monitoring.silences import OnCallSchedule

    assert current_assignee(OnCallSchedule()) is None
    schedule = OnCallSchedule(members=["a", "b", "c"], index=4)
    assert current_assignee(schedule) == "b"  # 4 % 3 == 1


# ---------- U287 OpsStore：上限 100 淘汰最旧 + 创建时惰性清过期 ----------


def test_u287_ops_silence_limit_and_lazy_purge():
    store = MonitoringStore()
    # 先造一条已过期（duration 0 立即到期，仅存储层直传）
    store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=0, reason="old", created_by="a"
    )
    assert len(store.list_silences()) == 1
    # 再造一条活跃：创建时惰性清掉过期项
    store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=30, reason="keep", created_by="a"
    )
    assert [s.reason for s in store.list_silences()] == ["keep"]

    # 上限 100：再造 104 条活跃（含上一条共 105），淘汰最旧、保留最新 100
    for index in range(104):
        store.create_silence(
            rule_id=None, graph_id=None, duration_minutes=30,
            reason=f"s{index}", created_by="a",
        )
    active = store.list_silences()
    assert len(active) == 100
    assert "keep" not in [s.reason for s in active]  # 最旧被淘汰
    assert active[-1].reason == "s103"


# ---------- U288 apply_escalation：超时 open warning 升级 ----------


def test_u288_apply_escalation_promotes_open_warning():
    rules = RuleConfig(escalation_ack_minutes=5)
    first = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    alert = Alert(
        id="alt-1", rule_id="node_failed", graph_id="g1", severity="warning",
        message="m", first_seen=first, last_seen=_now(), last_run_id="run-1",
        status="open",
    )
    upgraded = apply_escalation(alert, rules, _now())
    assert upgraded is not alert
    assert upgraded.severity == "critical"
    assert upgraded.escalated_at is not None
    # 基准缺 first_seen 时回退 last_seen
    alert.first_seen = ""
    alert.last_seen = first
    upgraded2 = apply_escalation(alert, RuleConfig(escalation_ack_minutes=5), _now())
    assert upgraded2.severity == "critical"


# ---------- U289 apply_escalation：各护栏不升级 ----------


def test_u289_apply_escalation_guards():
    first = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    base = dict(
        id="alt-1", rule_id="node_failed", graph_id="g1", message="m",
        first_seen=first, last_seen=_now(), last_run_id="run-1",
    )
    now = _now()

    def warn(**overrides):
        payload = dict(severity="warning", status="open")
        payload.update(overrides)
        return Alert(**base, **payload)

    # critical 不再升级
    assert apply_escalation(warn(severity="critical"), RuleConfig(escalation_ack_minutes=1), now).severity == "critical"
    # acknowledged / resolved 不升级
    assert apply_escalation(warn(status="acknowledged"), RuleConfig(escalation_ack_minutes=1), now).severity == "warning"
    assert apply_escalation(warn(status="resolved"), RuleConfig(escalation_ack_minutes=1), now).severity == "warning"
    # 未配置 / 显式关闭不升级（返回原 warning 对象）
    assert apply_escalation(warn(), RuleConfig(escalation_ack_minutes=None), now).severity == "warning"
    assert apply_escalation(warn(), RuleConfig(), now).severity == "warning"
    # 未超时不升级
    recent_base = dict(base)
    recent_base["first_seen"] = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    ).isoformat()
    recent = Alert(**recent_base, severity="warning", status="open")
    assert apply_escalation(recent, RuleConfig(escalation_ack_minutes=5), now).severity == "warning"
    # 已升级幂等：不重复改 escalated_at
    already = warn(severity="critical", escalated_at=first)
    assert apply_escalation(already, RuleConfig(escalation_ack_minutes=1), now) is already


# ---------- U290 store：静默命中抑制 + 计数；图级/全局 ----------


def test_u290_store_suppresses_matching_alerts_and_counts():
    # 规则级静默命中
    store = MonitoringStore()
    silence = store.create_silence(
        rule_id="node_failed", graph_id=None, duration_minutes=30, reason="维护", created_by="admin-a"
    )
    _failed_node_run(store)
    assert store.list_alerts() == []
    assert store.list_silences()[0].suppressed_count == 1
    assert silence.id == "sil-1"

    # 无静默对照：产生 1 条 warning
    plain = MonitoringStore()
    _failed_node_run(plain)
    alerts = plain.list_alerts()
    assert len(alerts) == 1 and alerts[0].severity == "warning"

    # 图级静默：g1 命中、g2 照常
    graph_store = MonitoringStore()
    graph_store.create_silence(
        rule_id=None, graph_id="g1", duration_minutes=30, reason="图维护", created_by="admin-a"
    )
    _failed_node_run(graph_store, graph_id="g1")
    _failed_node_run(graph_store, graph_id="g2")
    remaining = graph_store.list_alerts()
    assert len(remaining) == 1 and remaining[0].graph_id == "g2"

    # 全局静默命中任意规则/图
    global_store = MonitoringStore()
    global_store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=30, reason="全局冻结", created_by="admin-a"
    )
    _failed_node_run(global_store, graph_id="any")
    assert global_store.list_alerts() == []


# ---------- U291 删除 / 过期后恢复告警 ----------


def test_u291_silence_delete_and_expiry_restores_alerts():
    store = MonitoringStore()
    silence = store.create_silence(
        rule_id="node_failed", graph_id=None, duration_minutes=30, reason="r", created_by="a"
    )
    _failed_node_run(store)
    assert store.list_alerts() == []
    # 提前解除后立即恢复
    assert store.delete_silence(silence.id) is True
    _failed_node_run(store)
    assert len(store.list_alerts()) == 1
    assert store.delete_silence("sil-missing") is False

    # 过期静默不参与匹配
    expired_store = MonitoringStore()
    expired_store.create_silence(
        rule_id="node_failed", graph_id=None, duration_minutes=0, reason="r", created_by="a"
    )
    _failed_node_run(expired_store)
    assert len(expired_store.list_alerts()) == 1


# ---------- U292 list/get 读时惰性升级回写 ----------


def test_u292_store_lazy_escalation_on_list_and_get():
    store = MonitoringStore()
    store._rules = RuleConfig(escalation_ack_minutes=1)
    _failed_node_run(store)
    fresh = store.list_alerts()[0]
    assert fresh.severity == "warning"  # 刚产生，未超时
    # 把 first_seen 拨到 2 分钟前
    store._alerts[0].first_seen = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).isoformat()
    listed = store.list_alerts()[0]
    assert listed.severity == "critical"
    assert listed.escalated_at is not None
    fetched = store.get_alert(listed.id)
    assert fetched is not None and fetched.severity == "critical"


# ---------- U293 值班：去重/指派/合并不指派/取模/reset ----------


def test_u293_oncall_assignment_merge_rotate_and_reset():
    store = MonitoringStore()
    schedule = store.set_oncall(members=["a", "b", " a ", "b"], updated_by="admin-a")
    assert schedule.members == ["a", "b"] and schedule.index == 0
    assert store.get_oncall().members == ["a", "b"]

    # 新告警指派当前值班人 a
    _failed_node_run(store)
    assert store.list_alerts()[0].assignee == "a"

    # 轮换取模：a -> b -> a
    assert store.rotate_oncall(updated_by="admin-a").index == 1
    assert store.get_oncall().members[store.get_oncall().index] == "b"
    assert store.rotate_oncall(updated_by="admin-a").index == 0

    # 改值班为 b 后，同键告警合并、不重新指派（仍 a）
    store.set_oncall(members=["b"], updated_by="admin-a")
    _failed_node_run(store)
    alerts = store.list_alerts()
    assert len(alerts) == 1 and alerts[0].assignee == "a"

    # 空表轮换抛 OnCallEmpty
    store.reset()
    with pytest.raises(OnCallEmpty):
        store.rotate_oncall(updated_by="admin-a")

    # reset 全清：静默 / 值班 / 告警 / 升级配置
    store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=30, reason="r", created_by="a"
    )
    store.set_oncall(members=["a"], updated_by="a")
    _failed_node_run(store)
    store.reset()
    assert store.list_silences() == []
    assert store.get_oncall().members == []
    assert store.list_alerts() == []
    assert store.get_rules().escalation_ack_minutes is None


# ============================ REST ============================


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


def _viewer_headers() -> dict:
    token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ---------- U294 静默 CRUD + active 过滤 ----------


def test_u294_silence_crud_http():
    created = client.post(
        "/api/monitoring/silences",
        json={"duration_minutes": 60, "reason": "发布窗口"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "sil-1" and body["active"] is True
    assert body["rule_id"] is None and body["graph_id"] is None
    assert body["created_by"] == "admin-a" and body["suppressed_count"] == 0
    assert body["expires_at"] > body["created_at"]

    client.post(
        "/api/monitoring/silences",
        json={"rule_id": "node_failed", "graph_id": "g1", "duration_minutes": 30, "reason": "节点维护"},
    )
    active = client.get("/api/monitoring/silences?active=true")
    assert active.status_code == 200 and len(active.json()["items"]) == 2
    assert all(item["active"] is True for item in active.json()["items"])
    inactive = client.get("/api/monitoring/silences?active=false")
    assert inactive.status_code == 200 and inactive.json()["items"] == []

    deleted = client.delete("/api/monitoring/silences/sil-1")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert len(client.get("/api/monitoring/silences?active=true").json()["items"]) == 1
    assert client.delete("/api/monitoring/silences/sil-1").status_code == 404


# ---------- U295 静默 body 校验 422 + 边界合法 ----------


def test_u295_silence_validation_422():
    def post(payload):
        return client.post("/api/monitoring/silences", json=payload)

    assert post({"duration_minutes": 0, "reason": "r"}).status_code == 422
    assert post({"duration_minutes": 10081, "reason": "r"}).status_code == 422
    assert post({"duration_minutes": True, "reason": "r"}).status_code == 422
    assert post({"duration_minutes": 60, "reason": "   "}).status_code == 422
    assert post({"duration_minutes": 60, "reason": "x" * 201}).status_code == 422
    assert post({"duration_minutes": 60, "reason": "r", "rule_id": 123}).status_code == 422
    assert client.get("/api/monitoring/silences?active=maybe").status_code == 422

    # 合法边界：1 / 10080 分钟、200 字原因
    assert post({"duration_minutes": 1, "reason": "x"}).status_code == 201
    assert post({"duration_minutes": 10080, "reason": "x" * 200}).status_code == 201


# ---------- U296 权限矩阵：viewer 只读 ----------


def test_u296_silence_oncall_permission_matrix():
    headers = _viewer_headers()
    assert client.get("/api/monitoring/silences", headers=headers).status_code == 200
    assert client.get("/api/monitoring/on-call", headers=headers).status_code == 200
    assert client.post(
        "/api/monitoring/silences", json={"duration_minutes": 60, "reason": "r"}, headers=headers
    ).status_code == 403
    assert client.delete("/api/monitoring/silences/sil-1", headers=headers).status_code == 403
    assert client.put("/api/monitoring/on-call", json={"members": ["a"]}, headers=headers).status_code == 403
    assert client.post("/api/monitoring/on-call/rotate", headers=headers).status_code == 403


# ---------- U297 值班轮换 HTTP：去重/取模/空表 409/校验 ----------


def test_u297_oncall_http_rotate_and_validation():
    initial = client.get("/api/monitoring/on-call")
    assert initial.status_code == 200
    assert initial.json()["members"] == [] and initial.json()["current"] is None

    # 空表轮换 409
    assert client.post("/api/monitoring/on-call/rotate").status_code == 409

    put = client.put("/api/monitoring/on-call", json={"members": ["a", "b", "a", "b "]})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["members"] == ["a", "b"] and body["index"] == 0 and body["current"] == "a"

    rotated = client.post("/api/monitoring/on-call/rotate").json()
    assert rotated["index"] == 1 and rotated["current"] == "b"
    rotated2 = client.post("/api/monitoring/on-call/rotate").json()
    assert rotated2["index"] == 0 and rotated2["current"] == "a"

    assert client.put("/api/monitoring/on-call", json={"members": []}).status_code == 422
    assert client.put("/api/monitoring/on-call", json={"members": ["a"] * 21}).status_code == 422
    assert client.put("/api/monitoring/on-call", json={"members": ["a", " "]}).status_code == 422
    assert client.put("/api/monitoring/on-call", json={"members": "x"}).status_code == 422

    # reset 后回空表，轮换再 409
    client.post("/api/demo/reset")
    assert client.post("/api/monitoring/on-call/rotate").status_code == 409


# ---------- U298 规则升级配置往返 ----------


def test_u298_rules_escalation_roundtrip():
    rules = client.get("/api/monitoring/rules")
    assert rules.status_code == 200 and rules.json()["escalation_ack_minutes"] is None

    ok = client.put("/api/monitoring/rules", json=_valid_rules(5))
    assert ok.status_code == 200, ok.text
    assert client.get("/api/monitoring/rules").json()["escalation_ack_minutes"] == 5

    assert client.put("/api/monitoring/rules", json=_valid_rules(0)).status_code == 422
    assert client.put("/api/monitoring/rules", json=_valid_rules(10081)).status_code == 422
    # null 显式关闭合法
    closed = client.put("/api/monitoring/rules", json=_valid_rules(None))
    assert closed.status_code == 200 and closed.json()["escalation_ack_minutes"] is None


# ---------- U299 PG integration：静默不 INSERT / assignee 关联 / 读时升级 ----------


@pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run monitoring PG integration",
)
def test_u299_pg_silence_assignee_escalation_persisted():
    from pathlib import Path

    from sqlalchemy import create_engine, text

    from atlas.storage.pg import PgMonitoringStore

    engine = create_engine(os.environ["DATABASE_URL"])
    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    tenant = f"pgops-{uuid.uuid4().hex[:8]}"
    store = PgMonitoringStore(engine, tenant)

    # 静默命中：不产 PG 告警，压下计数 +1
    store.set_oncall(members=["a", "b"], updated_by="admin-a")
    store.create_silence(
        rule_id="node_failed", graph_id=None, duration_minutes=30, reason="维护", created_by="admin-a"
    )
    _failed_node_run(store)
    with engine.connect() as conn:
        alert_count = conn.execute(
            text("SELECT count(*) FROM monitoring_alerts WHERE tenant_id = :t"), {"t": tenant}
        ).scalar_one()
    assert alert_count == 0
    assert store.list_silences(active=True)[0].suppressed_count == 1

    # 解除静默 + 值班 a（index=0）：新告警入库，assignee 落 PG 列
    store.delete_silence(store.list_silences()[0].id)
    _failed_node_run(store)
    alerts = store.list_alerts()
    assert len(alerts) == 1 and alerts[0].assignee == "a"

    # 升级：配置 1 分钟 + first_seen 拨到过去，读时惰性升级并回写 PG
    store.update_rules(_valid_rules(1))
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE monitoring_alerts SET first_seen = :past WHERE tenant_id = :t"
            ),
            {"past": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(), "t": tenant},
        )
    upgraded = store.list_alerts()[0]
    assert upgraded.severity == "critical" and upgraded.escalated_at is not None

    # docs/59 F-2：静默/值班/告警已落 PG，结尾清理本测试专用 tenant，避免残留
    store.reset()
    engine.dispose()

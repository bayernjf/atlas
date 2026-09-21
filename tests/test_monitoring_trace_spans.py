# -*- coding: utf-8 -*-
"""D28 Trace 时间线钻取后端（docs/33 §4；U270–U278）。

覆盖：
- 进程内 MonitoringStore：record_run 持久化 spans、get_run 取回、缺省 None、缺失返 None；
- REST：sync 真实运行落 spans、列表投影剔除 spans、trace 端点懒加载、历史无 spans 返 null、
  404/401、viewer 只读可访问；
- PG（U278，integration）：spans JSONB 往返、列表投影 spans=None、空对象归一 None。

debug 单步 / 录制回放 / subgraph 重入不经两个真实运行入口，天然不产 RunRecord.spans。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.monitoring.records import MonitoringStore
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)

ROOT_SPAN = {
    "traceId": "tr-root",
    "spanId": "sp-root",
    "parentSpanId": None,
    "name": "run g1",
    "kind": "run",
    "startedAt": "2026-09-21T00:00:00+00:00",
    "durationMs": 12.5,
    "status": "ok",
    "children": [
        {
            "traceId": "tr-root",
            "spanId": "sp-node",
            "parentSpanId": "sp-root",
            "name": "node tool_call-1",
            "kind": "node",
            "startedAt": "2026-09-21T00:00:00.001+00:00",
            "durationMs": 8.0,
            "status": "ok",
            "children": [],
        }
    ],
}


def _refund_graph() -> dict:
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "新退款",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
             "config": {"promptTemplate": "{{trigger-1.context.payload.reason}}"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "处理",
             "config": {"tool": "shop/process_refund"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def _run_sync(graph_id: str, order_id: str = "12345", amount: int = 299) -> None:
    resp = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": order_id, "reason": "商品破损", "amount": amount}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200, resp.text


def _latest_run(graph_id: str) -> dict:
    runs = client.get("/api/monitoring/runs", headers=DEFAULT_AUTH_HEADER).json()["items"]
    return next(run for run in runs if run["graph_id"] == graph_id)


# ---------- 进程内存储层 ----------


def test_u270_record_run_persists_spans_and_get_run_returns_it():
    store = MonitoringStore()
    now = datetime.now(timezone.utc).isoformat()
    record = store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at=now, duration_ms=12.5, nodes=[], spans=ROOT_SPAN,
    )
    fetched = store.get_run(record.id)
    assert fetched is not None
    assert fetched.spans == ROOT_SPAN
    # list_runs 返回同一对象（内存档不投影，投影在 API 层）
    assert store.list_runs()[0].spans == ROOT_SPAN


def test_u271_record_run_without_spans_is_none_and_missing_get_returns_none():
    store = MonitoringStore()
    now = datetime.now(timezone.utc).isoformat()
    record = store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at=now, duration_ms=1.0, nodes=[],
    )
    assert record.spans is None
    assert store.get_run(record.id) is not None
    assert store.get_run(record.id).spans is None
    assert store.get_run("run-does-not-exist") is None


# ---------- REST ----------


def test_u272_runs_list_omits_spans_key():
    graph_id = client.post(
        "/api/graphs", json=_refund_graph(), headers=DEFAULT_AUTH_HEADER
    ).json()["id"]
    _run_sync(graph_id, order_id="22702")
    latest = _latest_run(graph_id)
    assert "spans" not in latest, "列表投影必须剔除 spans（大 payload）"


def test_u273_trace_endpoint_returns_span_tree():
    graph_id = client.post(
        "/api/graphs", json=_refund_graph(), headers=DEFAULT_AUTH_HEADER
    ).json()["id"]
    _run_sync(graph_id, order_id="22703")
    latest = _latest_run(graph_id)
    resp = client.get(
        f"/api/monitoring/runs/{latest['id']}/trace", headers=DEFAULT_AUTH_HEADER
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == latest["id"]
    assert body["trace_id"] == latest["trace_id"]
    spans = body["spans"]
    assert spans is not None
    assert spans["traceId"] == latest["trace_id"]
    assert spans["name"]
    assert isinstance(spans.get("children"), list)
    assert len(spans["children"]) >= 1  # 至少含真实节点 span


def test_u274_trace_endpoint_legacy_run_returns_null_spans():
    # 直接在租户监控存储插一条无 spans 的历史记录（模拟迁移前/debug/回放数据）。
    from atlas.iam.deps import tenant_registry

    monitoring = tenant_registry.get("t1").monitoring
    now = datetime.now(timezone.utc).isoformat()
    legacy = monitoring.record_run(
        graph_id="g-legacy", mode="sync", status="completed",
        started_at=now, duration_ms=1.0, nodes=[],
    )
    resp = client.get(
        f"/api/monitoring/runs/{legacy.id}/trace", headers=DEFAULT_AUTH_HEADER
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == legacy.id
    assert body["spans"] is None


def test_u275_trace_endpoint_unknown_run_404():
    resp = client.get(
        "/api/monitoring/runs/run-ghost/trace", headers=DEFAULT_AUTH_HEADER
    )
    assert resp.status_code == 404


def test_u276_trace_endpoint_requires_auth():
    resp = client.get("/api/monitoring/runs/run-1/trace")
    assert resp.status_code == 401


def test_u277_viewer_can_read_trace():
    graph_id = client.post(
        "/api/graphs", json=_refund_graph(), headers=DEFAULT_AUTH_HEADER
    ).json()["id"]
    _run_sync(graph_id, order_id="22707")
    latest = _latest_run(graph_id)

    token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    resp = client.get(
        f"/api/monitoring/runs/{latest['id']}/trace",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["spans"] is not None


# ---------- PG 集成（U278） ----------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run monitoring PG integration",
)
def test_u278_pg_spans_roundtrip_and_list_projection():
    from pathlib import Path

    from sqlalchemy import create_engine, text

    from atlas.storage.pg import PgMonitoringStore

    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)

    # 幂等应用全部迁移（含 012 spans 列）。
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

    tenant = f"pgtrspans-{uuid.uuid4().hex[:8]}"
    store = PgMonitoringStore(engine, tenant)
    now = datetime.now(timezone.utc).isoformat()

    record = store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at=now, duration_ms=12.5, nodes=[], spans=ROOT_SPAN,
    )
    fetched = store.get_run(record.id)
    assert fetched is not None
    assert fetched.spans == ROOT_SPAN  # JSONB 往返保真

    listed = next(run for run in store.list_runs() if run.id == record.id)
    assert listed.spans is None  # 列表投影剔除 spans

    # 无 spans 记录：空对象 {} 归一为 None
    legacy = store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at=now, duration_ms=1.0, nodes=[],
    )
    assert store.get_run(legacy.id).spans is None

    engine.dispose()

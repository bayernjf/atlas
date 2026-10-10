"""打包 AG（docs/113）U1309、U1311–U1314：监控报表端点、导出与 PG 一致性。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.monitoring.records import MonitoringStore
from atlas.monitoring.reports import aggregate_runs
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)


def _simple_graph() -> dict:
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "新退款",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            # 打包 AJ（docs/121 §5 D-7）：本套件依赖「FAILED 也 completed」旧语义，显式 continue
            {"id": "tool_call-1", "type": "tool_call", "name": "处理",
             "config": {"tool": "shop/process_refund"}, "retry": {"on_error": "continue"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }


def _run_once(graph_id: str) -> None:
    resp = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": f"o{uuid.uuid4().hex[:6]}", "reason": "破损", "amount": 199}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200


# --- U1311：参数校验 ---------------------------------------------------------

def test_u1311_report_days_out_of_range():
    assert client.get("/api/monitoring/report?days=0", headers=DEFAULT_AUTH_HEADER).status_code == 422
    assert client.get("/api/monitoring/report?days=91", headers=DEFAULT_AUTH_HEADER).status_code == 422


def test_u1311_report_group_by_invalid():
    assert (
        client.get("/api/monitoring/report?group_by=bogus", headers=DEFAULT_AUTH_HEADER).status_code
        == 422
    )


# --- U1309：时间窗口（端到端 + 精确边界） ------------------------------------

def test_report_end_to_end_by_day():
    graph_id = client.post("/api/graphs", json=_simple_graph(), headers=DEFAULT_AUTH_HEADER).json()["id"]
    _run_once(graph_id)
    resp = client.get(
        f"/api/monitoring/report?graph_id={graph_id}&days=7&group_by=day",
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["since"] < body["until"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bucket = next(b for b in body["buckets"] if b["key"] == today)
    assert bucket["total"] >= 1
    assert bucket["completed"] >= 1


def test_report_by_version_buckets_manual_draft():
    graph_id = client.post("/api/graphs", json=_simple_graph(), headers=DEFAULT_AUTH_HEADER).json()["id"]
    _run_once(graph_id)
    resp = client.get(
        f"/api/monitoring/report?graph_id={graph_id}&days=7&group_by=version",
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    # 手动运行 resolved_version=None → manual-draft 桶
    assert any(b["key"] == "manual-draft" for b in resp.json()["buckets"])


def test_u1309_window_boundary_in_memory():
    store = MonitoringStore()
    store.record_run(
        graph_id="g1", mode="sync", status="completed",
        started_at="2026-10-08T10:00:00+00:00", duration_ms=100.0, nodes=[],
    )
    since = "2026-10-08T10:00:00+00:00"  # 恰好 since → 入选
    until = "2026-10-08T11:00:00+00:00"  # 恰好 until → 不入选
    picked = store.list_runs_for_report(None, since, until)
    assert len(picked) == 1
    # 边界外（早于 since）
    assert store.list_runs_for_report(None, "2026-10-08T10:00:01+00:00", until) == []


# --- U1313/U1314：导出 -------------------------------------------------------

def test_u1313_export_csv_has_bom_and_header():
    graph_id = client.post("/api/graphs", json=_simple_graph(), headers=DEFAULT_AUTH_HEADER).json()["id"]
    _run_once(graph_id)
    resp = client.get(
        f"/api/monitoring/report/export?graph_id={graph_id}&days=7&format=csv",
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    assert resp.content.startswith(b"\xef\xbb\xbf")
    assert "运行总数" in resp.text
    assert f'filename="run-report-{graph_id}.csv"' in resp.headers["content-disposition"]


def test_u1313_export_json():
    resp = client.get(
        "/api/monitoring/report/export?days=7&format=json",
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    assert "buckets" in resp.json()
    assert 'filename="run-report-all.json"' in resp.headers["content-disposition"]


def test_u1314_export_format_invalid():
    assert (
        client.get("/api/monitoring/report/export?format=bogus", headers=DEFAULT_AUTH_HEADER).status_code
        == 422
    )


# --- U1312：PG 一致性（集成通道，默认 skip） --------------------------------

@pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run PG report integration",
)
def test_u1312_pg_report_matches_in_memory_aggregation():
    from sqlalchemy import create_engine

    from atlas.storage.migrations import apply_pending
    from atlas.storage.pg import PgMonitoringStore

    engine = create_engine(os.environ["DATABASE_URL"])
    apply_pending(engine)
    tenant = f"pgreport-{uuid.uuid4().hex[:8]}"
    store = PgMonitoringStore(engine, tenant)

    base = "2026-10-08T"
    specs = [
        ("09:00:00", "completed", 100.0, 1),
        ("10:00:00", "error", 200.0, 1),
        ("11:00:00", "completed", 300.0, 2),
    ]
    for clock, status, duration, version in specs:
        ts = f"{base}{clock}+00:00"
        store.record_run(
            graph_id="g1", mode="sync", status=status,
            started_at=ts, duration_ms=duration, nodes=[], resolved_version=version,
        )

    pg_runs = store.list_runs_for_report(
        "g1", "2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00"
    )
    mem = MonitoringStore()
    for clock, status, duration, version in specs:
        ts = f"{base}{clock}+00:00"
        mem.record_run(
            graph_id="g1", mode="sync", status=status,
            started_at=ts, duration_ms=duration, nodes=[], resolved_version=version,
        )
    mem_runs = mem.list_runs_for_report(
        "g1", "2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00"
    )
    # 两档同数据 → 聚合一致（按天与按版本）
    assert aggregate_runs(pg_runs, "day") == aggregate_runs(mem_runs, "day")
    assert aggregate_runs(pg_runs, "version") == aggregate_runs(mem_runs, "version")

    engine.dispose()

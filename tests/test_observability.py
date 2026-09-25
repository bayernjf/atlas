# -*- coding: utf-8 -*-
"""就绪探针与 Prometheus 指标端点测试（docs/34 §五 P1；D11 进程内子集）。

/health 与 /ready 与 /metrics 均不鉴权（编排系统/Prometheus 拉取不带 token），
故本文件使用不带 Authorization 头的独立 TestClient。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.registry import STORAGE_BACKEND
from atlas.observability.health import check_ready
from atlas.observability.metrics_export import render_prometheus

client = TestClient(app)


# ---------- /api/health 存活探针 ----------

def test_health_is_public_and_ok() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------- /api/ready 就绪探针 ----------

def test_ready_memory_backend() -> None:
    ok, detail = check_ready()
    if STORAGE_BACKEND == "memory":
        assert ok is True
        assert detail == {"storage": "memory", "database": "n/a"}
    # PG 档（ATLAS_STORAGE_BACKEND=pg）下 check_ready 真实探库，结果取决于测试环境是否起 PG


def test_ready_endpoint_shape() -> None:
    resp = client.get("/api/ready")
    if STORAGE_BACKEND == "memory":
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["storage"] == "memory"
    else:
        # PG 档：库可达 200，不可达 503；两种状态码都合法，但 body 形状固定
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert body["status"] in ("ready", "not_ready")
        assert body["storage"] == "pg"


# ---------- /metrics Prometheus 端点 ----------

def test_metrics_is_public_and_prometheus_format() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "version=0.0.4" in resp.headers["content-type"]
    body = resp.text
    # 进程级指标恒在
    assert "# TYPE atlas_up gauge" in body
    assert "atlas_up 1" in body
    assert f'backend="{STORAGE_BACKEND}"' in body
    assert "# TYPE atlas_tenants_active gauge" in body


# ---------- docs/65 K-C：prod /metrics Bearer 闸门 ----------

def test_metrics_prod_fail_closed_without_token(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_METRICS_TOKEN", raising=False)
    resp = client.get("/metrics")
    assert resp.status_code == 404


def test_metrics_prod_requires_bearer(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_METRICS_TOKEN", "secret-token")
    resp = client.get("/metrics")
    assert resp.status_code == 401
    resp = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_metrics_prod_bearer_allowed(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_METRICS_TOKEN", "secret-token")
    resp = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200
    body = resp.text
    assert "# TYPE atlas_up gauge" in body
    assert "atlas_up 1" in body


def test_metrics_non_prod_stays_public(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.delenv("ATLAS_METRICS_TOKEN", raising=False)
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_reflects_run_snapshots() -> None:
    """跑一次真实图后，/metrics 应出现该租户的 runs 指标序列。"""
    # 先取一个已装配租户（conftest 的 admin 会话装配了 t1）
    from atlas.iam.deps import tenant_registry

    tenant_ids = tenant_registry.all_tenant_ids()
    assert tenant_ids, "conftest admin 会话应已装配至少一个租户"
    # 直接喂快照验证渲染，避免依赖运行结果的时序
    for tid in tenant_ids:
        snap = tenant_registry.get(tid).monitoring.snapshot_metrics()
        text = render_prometheus(storage_backend=STORAGE_BACKEND, tenant_snapshots=[(tid, snap)])
        assert f'tenant_id="{tid}"' in text
        # 有样本时成功率/分位才出现；无样本也不应报错
        if snap["total"]:
            assert "atlas_runs_total" in text


# ---------- render_prometheus 纯函数 ----------

def test_render_prometheus_basic_series() -> None:
    snapshots = [
        ("demo", {
            "total": 10, "healthy": 8, "unhealthy": 2, "success_rate": 0.8,
            "p50": 120.0, "p95": 480.0,
            "per_graph": [], "failed_nodes": [], "business": {},
            "tools": [
                {"tool": "shop/refund", "calls": 5, "failed": 1, "simulated": 1,
                 "error_codes": {}, "p50": 10.0, "p95": 20.0},
            ],
        }),
    ]
    text = render_prometheus(storage_backend="memory", tenant_snapshots=snapshots)
    assert 'atlas_runs_total{tenant_id="demo",status="healthy"} 8' in text
    assert 'atlas_runs_total{tenant_id="demo",status="unhealthy"} 2' in text
    assert 'atlas_runs_success_rate{tenant_id="demo"} 0.8' in text
    assert 'atlas_run_duration_ms{tenant_id="demo",quantile="0.5"} 120' in text
    assert 'atlas_run_duration_ms{tenant_id="demo",quantile="0.95"} 480' in text
    # calls=5 = success 3 + failed 1 + simulated 1
    assert 'atlas_tool_calls_total{tenant_id="demo",tool="shop/refund",status="success"} 3' in text
    assert 'atlas_tool_calls_total{tenant_id="demo",tool="shop/refund",status="failed"} 1' in text
    assert 'atlas_tool_calls_total{tenant_id="demo",tool="shop/refund",status="simulated"} 1' in text
    assert "atlas_tenants_active 1" in text
    assert 'atlas_storage_backend_info{backend="memory"} 1' in text


def test_render_prometheus_skips_none_values() -> None:
    """无样本时 success_rate/p50/p95 为 None，不应产出 NaN 序列。"""
    snapshots = [("empty", {
        "total": 0, "healthy": 0, "unhealthy": 0, "success_rate": None,
        "p50": None, "p95": None, "per_graph": [], "failed_nodes": [],
        "business": {}, "tools": [],
    })]
    text = render_prometheus(storage_backend="memory", tenant_snapshots=snapshots)
    assert 'tenant_id="empty"' not in text or "atlas_runs_success_rate" not in text.split('tenant_id="empty"')[0].split("\n")[-1]
    assert "NaN" not in text
    assert "atlas_runs_total" in text  # healthy/unhealthy 的 0 仍输出


def test_render_prometheus_escapes_label_values() -> None:
    snapshots = [('a"b\\c\n', {
        "total": 1, "healthy": 1, "unhealthy": 0, "success_rate": 1.0,
        "p50": 1.0, "p95": 1.0, "per_graph": [], "failed_nodes": [],
        "business": {}, "tools": [],
    })]
    text = render_prometheus(storage_backend="memory", tenant_snapshots=snapshots)
    assert 'tenant_id="a\\"b\\\\c\\n"' in text

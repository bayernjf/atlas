"""P2-1 split: Health, readiness, metrics and audit trail routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require, tenant_registry
from atlas.iam.principals import Principal
from atlas.observability.health import check_ready
from atlas.observability.metrics_export import render_prometheus
from atlas.security.bootstrap import demo_surface_enabled
from atlas.services.audit_query import audit_bounds as _audit_bounds, audit_cursor as _audit_cursor
from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Any
from atlas.api.main import (
    _metrics_auth_gate,
    logger,
)


router: APIRouter = APIRouter()


@router.get("/api/health")
def health() -> dict[str, object]:
    """存活探针：进程在跑即 200（不检查依赖）。

    demo_surface：演示面是否开启（docs/75 单一档位，prod 档 false）。
    供前端决定是否暴露演示专用入口（如「重置演示数据」，docs/101 D59）。
    """
    return {"status": "ok", "demo_surface": demo_surface_enabled()}


@router.get("/api/ready")
def ready() -> JSONResponse:
    """就绪探针：依赖（PG）可用才 200，否则 503，供 compose healthcheck/反代摘流。"""
    ok, detail = check_ready()
    detail = {"status": "ready" if ok else "not_ready", **detail}
    return JSONResponse(detail, status_code=200 if ok else 503)


@router.get("/metrics")
def prometheus_metrics(request: Request) -> Response:
    """Prometheus 文本指标拉取端点（docs/65 K-C：prod 走 Bearer token 闸门）。

    只暴露进程级与按租户聚合的计数/分位数；正式 OTel/Grafana 栈缓做 docs/14 D11。
    """
    gate = _metrics_auth_gate(request)
    if gate is not None:
        return gate
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for tenant_id in tenant_registry.all_tenant_ids():
        try:
            snapshots.append((tenant_id, tenant_registry.get(tenant_id).monitoring.snapshot_metrics()))
        except Exception as exc:  # 指标端点绝不因单租户快照失败而 500
            logger.warning("metrics snapshot failed for %s: %s", tenant_id, exc)
    body = render_prometheus(storage_backend=_main.STORAGE_BACKEND, tenant_snapshots=snapshots)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/api/audit/events")
def list_audit_events(
    principal: Principal = Depends(require("administer")),
    limit: int = 100,
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cursor: int | None = None,
) -> dict[str, Any]:
    """写操作审计事件（docs/35 §6 T6；docs/61 §4.2 补过滤与游标分页）：
    倒序（最新在前），admin only，只读本租户；limit 为页大小 clamp 1–500；
    action 路由动作前缀、actor 精确等值、since/until UTC ISO-8601 闭区间、
    cursor 取严格更早的一页（上一页最后一条的 seq）；响应补 nextCursor（无更多则 null）。"""
    bounded = max(1, min(int(limit), 500))
    action_prefix = action.strip() if action and action.strip() else None
    actor_exact = actor.strip() if actor and actor.strip() else None
    lo, hi = _audit_bounds(since, until)
    page = _audit_cursor(cursor)
    items = _main.services_for(principal).audit_store.list(
        limit=bounded,
        action_prefix=action_prefix,
        actor=actor_exact,
        since=lo,
        until=hi,
        cursor=page,
    )
    # 取满一页才可能有下一页；游标即本页最后一条的 seq（不足一页说明到底了）。
    next_cursor = items[-1]["seq"] if len(items) == bounded and items else None
    return {"items": items, "limit": bounded, "nextCursor": next_cursor}


@router.get("/api/audit/export")
def export_audit_events(
    principal: Principal = Depends(require("administer")),
    action: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    fmt: str | None = Query(default="jsonl", alias="format"),
) -> StreamingResponse:
    """导出审计为 JSONL 附件（admin only，正序旧→新）；受同一套过滤，**不受分页影响**
    （无 cursor，导出的是全部匹配行）；v1 仅支持 format=jsonl。"""
    if fmt != "jsonl":
        raise HTTPException(status_code=422, detail="仅支持 format=jsonl")
    action_prefix = action.strip() if action and action.strip() else None
    actor_exact = actor.strip() if actor and actor.strip() else None
    lo, hi = _audit_bounds(since, until)
    body = _main.services_for(principal).audit_store.export_jsonl(
        action_prefix=action_prefix, actor=actor_exact, since=lo, until=hi
    )
    headers = {"Content-Disposition": 'attachment; filename="atlas-audit.jsonl"'}
    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type="application/x-ndjson; charset=utf-8",
        headers=headers,
    )

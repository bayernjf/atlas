"""P2-1 split: Reflection reports, candidates and autoapply routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.reflection.autoapply import AUTO_SCOPES, abort_autoapply, run_autoapply
from fastapi import Depends, HTTPException, Request
from typing import Any
from atlas.api.main import (
    AutoApplyRequest,
    ReflectionDecisionRequest,
    _heavy_batch_guard,
    _publish_hook,
    _record_audit,
    _runtime_registry,
    _tenant_graph_resolver,
)


router: APIRouter = APIRouter()


@router.get("/api/reflection/reports")
def list_reflection_reports(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户反思 pass 的收尾记录（进程内 ring，新→旧；docs/88 §4／docs/12）。

    **纯只读**：反思无自动 apply／publish／promote，候选落草稿版本后由人走既有流程采纳。
    `limit` 非整数由 FastAPI 挡成 422，越界由 store 侧 clamp 到 1–200（不报错）。
    """
    services = _main.services_for(principal)
    return {"items": services.reflection_store.list_reports(graph_id=graph_id, limit=limit)}


@router.get("/api/reflection/candidates/{candidate_id}")
def get_reflection_candidate(
    candidate_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """单个反思候选详情；不存在或跨租户统一 404（候选表本就每租户一份，跨租户天然取不到）。"""
    services = _main.services_for(principal)
    candidate = services.reflection_store.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"反思候选不存在：{candidate_id}")
    return candidate


@router.put("/api/reflection/candidates/{candidate_id}/decision")
def put_reflection_decision(
    http_request: Request,
    candidate_id: str,
    body: ReflectionDecisionRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """登记/改判候选的人工处理标记（adopted/dismissed；docs/94 E-1/E-2/E-4）。

    **只记处理标记**：不触发任何 apply/publish/promote，采纳后的改图仍走既有人工流程
    （守 T22 唯一放量路径）。允许覆盖改判；候选不存在/跨租户统一 404；read 角色 403。
    """
    services = _main.services_for(principal)
    store = services.reflection_store
    if not store.record_decision(candidate_id, body.status):
        raise HTTPException(status_code=404, detail=f"反思候选不存在：{candidate_id}")
    candidate = store.get_candidate(candidate_id)
    _record_audit(
        services, principal, http_request,
        f"reflection.decide.{body.status}", 200,
    )
    return candidate if candidate is not None else {}


@router.post("/api/reflection/candidates/{candidate_id}/autoapply", status_code=201)
@_heavy_batch_guard
def start_candidate_autoapply(
    http_request: Request,
    candidate_id: str,
    body: AutoApplyRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """候选自动生效（docs/132 BP-D2/D4）：patch→影子→黄金回放→发布→canary，每步留证据。

    幂等：同一候选已有自动生效记录（任意状态）→ 409（BP-D2）；候选不存在 404；
    已处理候选（adopted/dismissed）→ 409；类别开关未开 → run 以 failed 留证（fail-closed，
    BP-D1）。返回 AutoApplyRun（含每圈证据）。read 角色 403。
    """
    services = _main.services_for(principal)
    if candidate_id in {
        row["candidate_id"] for row in services.autoapply_store.list(limit=200)
    }:
        raise HTTPException(
            status_code=409, detail=f"候选 {candidate_id} 已执行过自动生效（幂等）"
        )
    enabled_scopes = (
        set(body.enabledScopes)
        if body is not None and body.enabledScopes is not None
        else set(services.autoapply_enabled_scopes)
    )
    invalid = enabled_scopes - AUTO_SCOPES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=(
                f"不支持的自动生效类别：{sorted(invalid)}（v1 仅允许 {sorted(AUTO_SCOPES)}）"
            ),
        )
    services.autoapply_enabled_scopes |= enabled_scopes
    try:
        run = run_autoapply(
            services,
            tenant_id=principal.tenant_id,
            candidate_id=candidate_id,
            enabled_scopes=enabled_scopes,
            registry=_runtime_registry(services),
            graph_resolver=_tenant_graph_resolver(services),
            publish_hook=_publish_hook,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_audit(services, principal, http_request, "reflection.autoapply", 201)
    return run


@router.post("/api/reflection/autoapply/{run_id}/abort")
def abort_autoapply_run(
    http_request: Request,
    run_id: str,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """中止自动生效（docs/132 BP-D5）：canary 态＝手动回滚；pending 取消；终态幂等。"""
    services = _main.services_for(principal)
    run = abort_autoapply(services, tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"自动生效记录不存在：{run_id}")
    _record_audit(services, principal, http_request, "reflection.autoapply.abort", 200)
    return run


@router.get("/api/reflection/autoapply")
def list_autoapply_runs(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户自动生效记录（倒序；docs/132 BP-D6 每圈证据列表投影，不含大字段）。"""
    services = _main.services_for(principal)
    return {"items": services.autoapply_store.list(graph_id=graph_id, limit=limit)}


@router.get("/api/reflection/autoapply/config")
def get_autoapply_config(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """类别开关投影（docs/132 §3.4）：已开启类别／可自动类别／结构类恒不自动。

    **必须先于 ``/autoapply/{run_id}`` 注册**（静态段不被路径参数吞掉）。
    """
    services = _main.services_for(principal)
    return {
        "enabledScopes": sorted(services.autoapply_enabled_scopes),
        "availableScopes": sorted(AUTO_SCOPES),
        "structureAuto": False,
    }


@router.get("/api/reflection/autoapply/{run_id}")
def get_autoapply_run(
    run_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """单条自动生效记录详情（含 patch/shadow/gate/rollout 全证据；docs/132 §3.4）。"""
    services = _main.services_for(principal)
    run = services.autoapply_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"自动生效记录不存在：{run_id}")
    return run

"""P2-1 split: Experiment tracking and shadow-run comparison routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.routing import CanaryRule, ExperimentError, GateConfig, RolloutConfig, RolloutError, arm_stats, judge, primary_counts, required_samples
from fastapi import Depends, HTTPException
from typing import Any
from atlas.api.main import (
    ExperimentConcludeBody,
    ExperimentCreateBody,
    _experiment_body_or_422,
    _experiment_window,
    _parse_human_outcome,
)


router: APIRouter = APIRouter()


@router.get("/api/shadow-runs")
def list_shadow_runs(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户影子运行记录倒序（可按图过滤，limit 1–200，默认 50）。"""
    services = _main.services_for(principal)
    bounded = max(1, min(int(limit), 200))
    return {"items": services.shadow_store.list(graph_id=graph_id, limit=bounded)}


@router.get("/api/shadow-runs/{sid}")
def get_shadow_run(sid: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    services = _main.services_for(principal)
    run = services.shadow_store.get(sid)
    if run is None:
        raise HTTPException(status_code=404, detail=f"影子运行不存在：{sid}")
    return run


@router.post("/api/shadow-runs/{sid}/compare")
def compare_shadow_run(
    sid: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """补录/覆盖人工实际处理并重算对比（docs/33 §3.4）；action 非空，非法 422，不存在 404。"""
    services = _main.services_for(principal)
    body = payload or {}
    outcome = _parse_human_outcome(body.get("human_outcome"))
    if outcome is None:
        raise HTTPException(status_code=422, detail="human_outcome.action 不能为空")
    run = services.shadow_store.attach_outcome(sid, outcome)
    if run is None:
        raise HTTPException(status_code=404, detail=f"影子运行不存在：{sid}")
    return run


@router.post("/api/experiments")
def create_experiment(
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """创建即启动（docs/134 §2.4）：快照原配置→写纯 canary 实验配置→显式两版 start→落库。

    graph 404；A==B／版本非该图发布版 422；rollout 非 idle 409；同图已有 running 实验 409（BR-D6）。
    """
    payload: ExperimentCreateBody = _experiment_body_or_422(body, ExperimentCreateBody, "实验创建请求")
    services = _main.services_for(principal)
    graph_id = payload.graphId
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    if payload.versionA == payload.versionB:
        raise HTTPException(
            status_code=422,
            detail=f"versionA 与 versionB 不可为同一版本（v{payload.versionA}）",
        )
    versions = services.graph_store.list_versions(graph_id)
    for label, version in (("versionA", payload.versionA), ("versionB", payload.versionB)):
        if version not in versions:
            raise HTTPException(
                status_code=422, detail=f"{label} 版本 v{version} 不在该图发布版本列表内"
            )
    snapshot = services.routing_store.snapshot(graph_id)
    if snapshot.status != "idle":
        raise HTTPException(
            status_code=409,
            detail=f"rollout 当前为 {snapshot.status} 态：实验须从 idle 开启（BR-D6）",
        )
    running = services.experiment_store.running_for_graph(graph_id)
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=f"该图已有进行中的实验 {running.id}：同图同一时刻至多一个 running 实验（BR-D6）",
        )
    prior = snapshot.config
    config = RolloutConfig(
        rules=[CanaryRule(percent=payload.trafficPercent)],
        gate=prior.gate if prior is not None else GateConfig(autoRollback=False),
    )
    services.routing_store.configure(graph_id, config)
    try:
        services.routing_store.start(
            graph_id, versions, stable=payload.versionA, candidate=payload.versionB
        )
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    exp = services.experiment_store.create(
        graph_id=graph_id,
        version_a=payload.versionA,
        version_b=payload.versionB,
        traffic_percent=payload.trafficPercent,
        primary_metric=payload.primaryMetric,
        min_samples=payload.minSamples,
        prior_config=prior,
        created_by=principal.username,
    )
    return exp.model_dump()


@router.get("/api/experiments")
def list_experiments(
    graphId: str | None = None,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """实验列表（轻投影：不含臂统计，避免逐实验扫 ring；createdAt 倒序）。"""
    experiments = _main.services_for(principal).experiment_store.list(graph_id=graphId)
    return {"experiments": [exp.model_dump() for exp in experiments]}


@router.get("/api/experiments/{exp_id}")
def get_experiment(
    exp_id: str,
    mde: float = 0.05,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """实验详情＋两臂 armStats＋verdict（running 活算建议／concluded 取冻结）＋splitActive＋requiredSamples 指导。"""
    services = _main.services_for(principal)
    exp = services.experiment_store.get(exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"实验不存在：{exp_id}")
    window = _experiment_window(services, exp)
    stats_a = arm_stats(window, exp.versionA)
    stats_b = arm_stats(window, exp.versionB)
    verdict = (
        exp.verdict
        if exp.status == "concluded" and exp.verdict is not None
        else judge(exp, stats_a, stats_b)
    )
    snapshot = services.routing_store.snapshot(exp.graphId)
    x_a, n_a = primary_counts(stats_a, exp.primaryMetric)
    baseline = (x_a / n_a) if n_a else None
    projection = exp.model_dump()
    projection["armStats"] = {"A": stats_a.model_dump(), "B": stats_b.model_dump()}
    projection["verdict"] = verdict.model_dump()
    projection["splitActive"] = (
        snapshot.status == "canary" and snapshot.candidate == exp.versionB
    )
    projection["requiredSamples"] = (
        required_samples(baseline, mde) if baseline is not None else None
    )
    return projection


@router.post("/api/experiments/{exp_id}/conclude")
def conclude_experiment(
    exp_id: str,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """冻结 verdict＋人决落库：adopt_b→promote／adopt_a→rollback（幂等同既有），恢复 priorConfig（BR-D4/D5）。

    不存在/跨租户 404；已终态 409；rollout 侧冲突 409（未落库可重试）；人可改判，verdict 与 action 不一致照实记录。
    """
    payload: ExperimentConcludeBody = _experiment_body_or_422(
        body, ExperimentConcludeBody, "实验 conclude 请求"
    )
    services = _main.services_for(principal)
    exp = services.experiment_store.get(exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"实验不存在：{exp_id}")
    if exp.status == "concluded":
        raise HTTPException(status_code=409, detail=f"实验 {exp_id} 已终态，不可重复 conclude")
    window = _experiment_window(services, exp)
    verdict = judge(exp, arm_stats(window, exp.versionA), arm_stats(window, exp.versionB))
    graph_id = exp.graphId
    snapshot = services.routing_store.snapshot(graph_id)
    try:
        if payload.action == "adopt_b":
            if not (snapshot.status == "full" and snapshot.candidate == exp.versionB):
                services.routing_store.promote(graph_id)
        else:
            services.routing_store.rollback(
                graph_id,
                actor="manual",
                reason=f"实验 conclude：{payload.action}（verdict={verdict.status}）",
            )
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if exp.priorConfig is not None:
        services.routing_store.configure(graph_id, exp.priorConfig)
    try:
        concluded = services.experiment_store.conclude(
            exp_id, verdict=verdict, action=payload.action, reason=payload.reason
        )
    except ExperimentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if concluded is None:  # pragma: no cover - 并发 reset 竞态兜底
        raise HTTPException(status_code=404, detail=f"实验不存在：{exp_id}")
    return concluded.model_dump()

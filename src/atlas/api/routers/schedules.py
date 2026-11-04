"""P2-1 split: Schedule listing, enable/disable, run-now and cron preview.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require, tenant_registry
from atlas.iam.principals import Principal
from atlas.llm.metering import usage_context
from atlas.reflection import run_pass as run_reflection_pass
from atlas.scheduling.cron import CronExpressionError, next_fire_utc, validate_cron
from atlas.scheduling.models import schedule_projection, slot_key, tz_of
from atlas.services.schedule_pure import no_schedule_detail as _no_schedule_detail
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from typing import Any
from atlas.api.main import (
    CronPreviewRequest,
    MAX_CRON_PREVIEW_HITS,
    RunNowRequest,
    ScheduleEnabledRequest,
    _first_run_schedule,
    _record_audit,
    _schedule_is_busy,
    _start_pinned_run,
    schedule_store,
)


router: APIRouter = APIRouter()


@router.get("/api/schedules")
def list_schedules(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """本租户的调度登记（含下次触发时刻，UTC 口径）；纯只读，不改任何状态。

    docs/77 R7 enrich：每条 schedule 附带最近一次 run 的状态（handler 层查 run_store，
    零迁移、零 store 结构改动；run 只按 graph_id 匹配，不区分 schedule/manual mode）。
    """
    now = datetime.now(timezone.utc)
    records = schedule_store().list_tenant(principal.tenant_id)
    # 一次性拉所有 run 按 graph_id 分组，避免 N+1
    services = tenant_registry.get(principal.tenant_id)
    all_runs = services.run_store.list(status=None, limit=200)
    runs_by_graph: dict[str, list[dict]] = {}
    for run in all_runs:
        gid = run.get("graphId", "")
        runs_by_graph.setdefault(gid, []).append(run)
    items: list[dict[str, Any]] = []
    for record in records:
        proj = schedule_projection(record, now)
        graph_runs = runs_by_graph.get(record.graph_id, [])
        # 反思项不挂 lastRun*：反思 pass 不产 run（它不写 run_store），硬挂一条同图的跑图
        # run 会让人以为"这条反思调度跑出过那个运行"（docs/88 §3 P-4）。
        if graph_runs and record.action != "reflect":
            latest = graph_runs[0]  # list 已按 startedAt DESC，第一条即最近
            proj["lastRunId"] = latest.get("runId")
            proj["lastRunStatus"] = latest.get("status")
            proj["lastRunStartedAt"] = latest.get("startedAt")
        items.append(proj)
    return {"items": items}


@router.post("/api/schedules/{graph_id}/enabled")
def set_schedule_enabled(
    http_request: Request,
    graph_id: str,
    request: ScheduleEnabledRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """开/关一条调度（跨重启保留）。关只停未来触发，不取消在途运行（docs/68 §6.5）。"""
    services = _main.services_for(principal)
    record = schedule_store().set_enabled(
        principal.tenant_id, graph_id, request.schedule_id, request.enabled
    )
    if record is None:
        raise HTTPException(
            status_code=404, detail=_no_schedule_detail(graph_id, request.schedule_id)
        )
    _record_audit(
        services, principal, http_request,
        "schedule.enable" if request.enabled else "schedule.disable", 200,
    )
    return schedule_projection(record, datetime.now(timezone.utc))


@router.post("/api/schedules/{graph_id}/run-now")
def run_schedule_now(
    http_request: Request,
    graph_id: str,
    request: RunNowRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按钉版立刻跑一次（联调与验收入口）：**不写认领表、不占槽位**（docs/68 §2.4）。

    打包 ZN 起 body 用 `scheduleId` 指定调度；要跑反思传 `{"scheduleId": "__reflect__"}`
    （返回收尾报告摘要，`candidateId` 可为 null）。反思项**不做 busy 判定**——它不产运行、
    不与在途运行争用。缺省 body（null／无）跑该图第一条 run 调度。
    """
    services = _main.services_for(principal)
    store = schedule_store()
    sid = request.schedule_id if request is not None else None
    record = (
        store.get(principal.tenant_id, graph_id, sid)
        if sid is not None
        else _first_run_schedule(principal.tenant_id, graph_id)
    )
    if record is None:
        raise HTTPException(status_code=404, detail=_no_schedule_detail(graph_id, sid))
    sid = record.schedule_id
    now = datetime.now(timezone.utc)
    if record.action == "reflect":
        # docs/129 打包 AU：与调度派发同口径——反思摘要器的 LLM 调用计用量。
        with usage_context(principal.tenant_id, store=services.usage_store):
            report = run_reflection_pass(
                services, services.reflection_store,
                tenant_id=principal.tenant_id, graph_id=graph_id,
                base_version=record.version,
            )
        # 同 run-now 的口径：记"最近一次真实执行"，不是槽位认领（不占槽）。
        store.note_fired(principal.tenant_id, graph_id, sid, now)
        _record_audit(services, principal, http_request, "schedule.run_now", 200)
        return {
            "candidateId": report.candidate_id,
            "graphId": graph_id,
            "baseVersion": report.base_version,
            "status": report.status,
        }
    if _schedule_is_busy(record):
        raise HTTPException(
            status_code=409,
            detail=f"图 {graph_id} 仍有运行在执行中（running/suspended），本次立即运行已跳过",
        )
    run_id = _start_pinned_run(
        services, principal.tenant_id, record.graph_id, record.version,
        {"source": "schedule-run-now", "slot": slot_key(now), "cron": record.cron},
    )
    # 记的是"最近一次真实运行"，不是槽位认领：run-now 不占槽，但运营要看得见它跑过。
    store.note_fired(principal.tenant_id, graph_id, sid, now)
    _record_audit(services, principal, http_request, "schedule.run_now", 200)
    return {
        "runId": run_id, "graphId": record.graph_id, "version": record.version,
        "scheduleId": sid,
    }


@router.post("/api/schedules/cron-preview")
def preview_schedule_cron(
    request: CronPreviewRequest, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """cron 预演：这个表达式合不合法、接下来三个 UTC 槽位是几点（不落库、不占槽位）。

    刻意**不在前端复刻一个 cron 解析器**：两份实现对"日与周取并集""7＝周日"这类边角
    迟早分叉，分叉的表现就是画布里看着绿、保存时才 422。合法与非法都回 200——请求本身
    格式正确，"表达式不合法"是答案不是错误（与保存期 422 的分工：这里只预演，不拦保存）。
    """
    cron = request.cron.strip()
    if not cron:
        raise HTTPException(status_code=422, detail="请先填写 Cron 表达式（5 个字段：分 时 日 月 周）")
    cursor = datetime.now(timezone.utc)
    tz = tz_of(request.timeZone)  # 非法 tz 在保存期被 DSL 拦，这里回落 UTC 只做预演
    try:
        spec = validate_cron(cron, now=cursor, tz=tz)
    except CronExpressionError as exc:
        return {"valid": False, "message": str(exc), "nextFireAt": [], "timeZone": request.timeZone}
    hits: list[str] = []
    while len(hits) < MAX_CRON_PREVIEW_HITS:
        upcoming = next_fire_utc(spec, cursor, tz=tz)
        if upcoming is None:
            break
        hits.append(upcoming.isoformat())
        cursor = upcoming
    return {"valid": True, "message": "", "nextFireAt": hits, "timeZone": request.timeZone}

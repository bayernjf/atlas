"""P2-1 split: Monitoring report/rules/alerts/silences/on-call routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import csv
import io
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.monitoring import RUN_RING_SIZE
from atlas.monitoring.reports import aggregate_runs
from atlas.monitoring.silences import OnCallEmpty, current_assignee, is_silence_active
from fastapi import Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from typing import Any, Literal
from atlas.api.main import (
    _alert_channel_payload,
    _mask_alert_secret,
    _normalize_optional_id,
    _parse_future_expires_at,
    _report_window,
)


router: APIRouter = APIRouter()


@router.get("/api/monitoring/metrics")
def monitoring_metrics(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """运行指标聚合：租户内全局 + 按图 + 失败节点 Top（04 §5.13/§5.14）。"""
    return _main.services_for(principal).monitoring.snapshot_metrics()


@router.get("/api/monitoring/runs")
def monitoring_runs(
    graph_id: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """最近运行（新→旧，默认 50、上限 200；04 §5.13；按租户分区）。
    注：本端点保留 RUN_RING_SIZE 手动门禁（docs/64 J-3b 的 le= 批不含此处——
    语义上限为环形缓冲大小，且手动校验保证 detail 为可读字符串）。"""
    if limit < 1 or limit > RUN_RING_SIZE:
        raise HTTPException(status_code=422, detail=f"limit 必须是 1-{RUN_RING_SIZE} 之间的整数")
    runs = _main.services_for(principal).monitoring.list_runs(graph_id=graph_id, limit=limit)
    # docs/33 §4：列表投影剔除 spans（大 payload，仅 trace 端点按需返回）。
    return {
        "items": [
            {key: value for key, value in run.model_dump().items() if key != "spans"}
            for run in runs
        ]
    }


@router.get("/api/monitoring/runs/{run_id}/trace")
def monitoring_run_trace(
    run_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """docs/33 §4：单运行 span 树懒加载；历史/debug/回放无 spans 记录返 null，不存在 404。"""
    run = _main.services_for(principal).monitoring.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"id": run.id, "trace_id": run.trace_id, "spans": run.spans}


@router.get("/api/monitoring/report")
def monitoring_report(
    graph_id: str | None = None,
    days: int = 7,
    group_by: Literal["day", "version"] = "day",
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """docs/113：窗口内运行按天/版本聚合；read、按租户。"""
    if days < 1 or days > 90:
        raise HTTPException(status_code=422, detail="days 必须是 1-90 之间的整数")
    since, until = _report_window(days)
    runs = _main.services_for(principal).monitoring.list_runs_for_report(graph_id, since, until)
    return {
        "since": since,
        "until": until,
        "group_by": group_by,
        "buckets": aggregate_runs(runs, group_by),
    }


@router.get("/api/monitoring/report/export")
def monitoring_report_export(
    graph_id: str | None = None,
    days: int = 7,
    group_by: Literal["day", "version"] = "day",
    format: Literal["csv", "json"] = "csv",
    principal: Principal = Depends(require("read")),
):
    """docs/113：报表导出 CSV（UTF-8 BOM，中文表头）/JSON；read。"""
    if days < 1 or days > 90:
        raise HTTPException(status_code=422, detail="days 必须是 1-90 之间的整数")
    since, until = _report_window(days)
    runs = _main.services_for(principal).monitoring.list_runs_for_report(graph_id, since, until)
    buckets = aggregate_runs(runs, group_by)
    scope = graph_id or "all"
    if format == "json":
        return JSONResponse(
            {"since": since, "until": until, "group_by": group_by, "buckets": buckets},
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="run-report-{scope}.json"'
            },
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "分组",
            "运行总数",
            "成功",
            "失败",
            "取消",
            "成功率",
            "平均耗时(ms)",
            "P50耗时(ms)",
            "P95耗时(ms)",
        ]
    )
    for bucket in buckets:
        writer.writerow(
            [
                bucket["key"],
                bucket["total"],
                bucket["completed"],
                bucket["error"],
                bucket["cancelled"],
                bucket["success_rate"],
                bucket["duration_avg_ms"],
                bucket["duration_p50_ms"],
                bucket["duration_p95_ms"],
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="run-report-{scope}.csv"'
        },
    )


@router.get("/api/monitoring/rules")
def monitoring_get_rules(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    return _main.services_for(principal).monitoring.get_rules().model_dump()


@router.put("/api/monitoring/rules")
def monitoring_update_rules(
    raw: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """全量替换本租户规则配置；校验失败聚合为中文 422（admin only，04 §5.14）。"""
    try:
        rules = _main.services_for(principal).monitoring.update_rules(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rules.model_dump()


@router.get("/api/monitoring/alert-channel")
def monitoring_get_alert_channel(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """告警外部通知配置 + 最近投递状态；从未投递 lastDelivery 为 null（docs/52）。"""
    return _alert_channel_payload(_main.services_for(principal).monitoring)


@router.put("/api/monitoring/alert-channel")
def monitoring_update_alert_channel(
    raw: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """整体替换本租户告警通知配置；校验失败聚合为中文 422（admin only，docs/52）。

    secret 出体已脱敏（``***``＋尾 4）：前端未改密钥直接保存会把脱敏占位原样提交，
    落库前归一化为库内现存明文（与当前 GET 投影逐字相等才认，不做 startswith 宽匹配，
    避免误伤真以此开头的新密钥）；空串仍是清空（docs/125 §3.2，#3）。
    """
    monitoring = _main.services_for(principal).monitoring
    submitted_secret = raw.get("secret")
    if isinstance(submitted_secret, str):
        current_secret = monitoring.get_alert_channel().secret
        if submitted_secret == _mask_alert_secret(current_secret):
            raw = {**raw, "secret": current_secret}
    try:
        monitoring.update_alert_channel(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _alert_channel_payload(monitoring)


@router.get("/api/alerts")
def list_alerts(
    status: str | None = None, principal: Principal = Depends(require("read"))
) -> dict[str, list[dict[str, Any]]]:
    """告警列表（新→旧，可按 open/acknowledged/resolved 过滤；04 §5.13；按租户分区）。"""
    if status is not None and status not in ("open", "acknowledged", "resolved"):
        raise HTTPException(
            status_code=422,
            detail="status 只允许 open、acknowledged、resolved",
        )
    alerts = _main.services_for(principal).monitoring.list_alerts(status=status)
    return {"items": [alert.model_dump() for alert in alerts]}


@router.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    monitoring = _main.services_for(principal).monitoring
    alert = monitoring.acknowledge_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"告警不存在：{alert_id}")
    if alert is False:
        raise HTTPException(status_code=409, detail="该告警已确认或已关闭，不能重复确认")
    return alert.model_dump()


@router.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    monitoring = _main.services_for(principal).monitoring
    alert = monitoring.resolve_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"告警不存在：{alert_id}")
    if alert is False:
        raise HTTPException(status_code=409, detail="该告警已关闭，重复提交不生效")
    return alert.model_dump()


@router.post("/api/monitoring/silences", status_code=201)
def create_silence(
    body: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.1：创建静默（administer）；duration 1-10080 分钟、reason 非空≤200。"""
    errors: list[str] = []
    rule_id = _normalize_optional_id(body.get("rule_id"), "rule_id", errors)
    graph_id = _normalize_optional_id(body.get("graph_id"), "graph_id", errors)
    duration = body.get("duration_minutes")
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 10080:
        errors.append("duration_minutes 必须是 1-10080 的整数")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        errors.append("reason 必须是非空且不超过 200 字的字符串")
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    monitoring = _main.services_for(principal).monitoring
    silence = monitoring.create_silence(
        rule_id=rule_id,
        graph_id=graph_id,
        duration_minutes=duration,
        reason=reason.strip(),
        created_by=principal.username,
    )
    return {**silence.model_dump(), "active": True}


@router.get("/api/monitoring/silences")
def list_silences(
    active: str | None = None, principal: Principal = Depends(require("read"))
) -> dict[str, list[dict[str, Any]]]:
    """docs/33 §5.1：静默列表（read），?active=true|false 过滤，每条带 active 计算字段。"""
    active_filter: bool | None = None
    if active is not None:
        if active not in ("true", "false"):
            raise HTTPException(status_code=422, detail="active 只允许 true 或 false")
        active_filter = active == "true"
    monitoring = _main.services_for(principal).monitoring
    items = monitoring.list_silences(active_filter)
    return {"items": [{**s.model_dump(), "active": is_silence_active(s)} for s in items]}


@router.delete("/api/monitoring/silences/{silence_id}")
def delete_silence(
    silence_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.1：提前解除（administer）；不存在 404。"""
    monitoring = _main.services_for(principal).monitoring
    if not monitoring.delete_silence(silence_id):
        raise HTTPException(status_code=404, detail=f"静默规则不存在：{silence_id}")
    return {"id": silence_id, "deleted": True}


@router.put("/api/monitoring/silences/{silence_id}")
def update_silence(
    silence_id: str, body: dict[str, Any],
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """docs/60 §4.1：编辑静默可变字段（administer）；不存在/已过期 404，校验失败 422。"""
    errors: list[str] = []
    updates: dict[str, Any] = {}
    if "reason" in body:
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
            errors.append("reason 必须是非空且不超过 200 字的字符串")
        else:
            updates["reason"] = reason.strip()
    if "rule_id" in body:
        updates["rule_id"] = _normalize_optional_id(body.get("rule_id"), "rule_id", errors)
    if "graph_id" in body:
        updates["graph_id"] = _normalize_optional_id(body.get("graph_id"), "graph_id", errors)
    if "expires_at" in body:
        expires = _parse_future_expires_at(body.get("expires_at"), errors)
        if expires is not None:
            updates["expires_at"] = expires
    if updates.get("rule_id") is not None and updates.get("graph_id") is not None:
        errors.append("rule_id 与 graph_id 不得同时指定")
    if not updates and not errors:
        errors.append("至少提供一个可更新字段：reason / rule_id / graph_id / expires_at")
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    monitoring = _main.services_for(principal).monitoring
    updated = monitoring.update_silence(silence_id, **updates)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"静默规则不存在或已过期：{silence_id}")
    return {**updated.model_dump(), "active": is_silence_active(updated)}


@router.get("/api/monitoring/on-call")
def get_on_call(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    """docs/33 §5.3：值班表（read），current 为当前值班人（空表 null）。"""
    schedule = _main.services_for(principal).monitoring.get_oncall()
    return {**schedule.model_dump(), "current": current_assignee(schedule)}


@router.put("/api/monitoring/on-call")
def update_on_call(
    body: dict[str, Any], principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    """docs/33 §5.3：设置轮值表（administer）；members 1-20 个非空串，去重保序、重置 index=0。"""
    members = body.get("members")
    if not isinstance(members, list) or not 1 <= len(members) <= 20:
        raise HTTPException(status_code=422, detail="members 必须是 1-20 个用户名字符串组成的数组")
    if any(not isinstance(m, str) or not m.strip() for m in members):
        raise HTTPException(status_code=422, detail="members 每项必须是非空字符串")
    # docs/60 §4.2：可选 rotationIntervalDays（1-365 或 null）；未提供按整体替换置空（不自动）
    rotation_interval_days: int | None = None
    if "rotationIntervalDays" in body:
        raw = body.get("rotationIntervalDays")
        if raw is None:
            rotation_interval_days = None
        elif isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 365:
            rotation_interval_days = raw
        else:
            raise HTTPException(
                status_code=422, detail="rotationIntervalDays 必须是 1-365 的整数或 null"
            )
    schedule = _main.services_for(principal).monitoring.set_oncall(
        members=members, updated_by=principal.username,
        rotation_interval_days=rotation_interval_days,
    )
    return {**schedule.model_dump(), "current": current_assignee(schedule)}


@router.post("/api/monitoring/on-call/rotate")
def rotate_on_call(principal: Principal = Depends(require("administer"))) -> dict[str, Any]:
    """docs/33 §5.3：手动轮换（administer）；空表 409。"""
    monitoring = _main.services_for(principal).monitoring
    try:
        schedule = monitoring.rotate_oncall(updated_by=principal.username)
    except OnCallEmpty as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**schedule.model_dump(), "current": current_assignee(schedule)}

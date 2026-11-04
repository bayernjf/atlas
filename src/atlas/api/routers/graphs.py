"""P2-1 split: Graph CRUD, release gates, rollout, versions and shadow runs.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import asyncio
import json
import threading
from atlas.api.sse import SSEBridge
from atlas.graph.diff import diff_graph, diff_summary
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import _tool_permissions
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.recording import extract_shadow_events, preset_all_approvals, preset_all_wait_events, report_to_csv
from atlas.routing import RolloutError
from atlas.services.error_safety import safe_persisted_error, unexpected_stream_error as _unexpected_stream_error
from atlas.storage.memory import ApprovalBroker
from atlas.tracing import Tracer
from atlas.versioning.publish import publish as publish_graph_version
from atlas.versioning.upgrades import apply_subgraph_upgrades, subgraph_upgrade_plan
from fastapi import Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Any, Literal
from atlas.api.main import (
    PublishGraphRequest,
    PublishGraphResponse,
    SaveGraphResponse,
    SubgraphUpgradesApplyRequest,
    _derive_schedule_on_publish,
    _heavy_batch_guard,
    _load_graph_or_404,
    _parse_human_outcome,
    _release_gate_for_draft,
    _rollout_config_or_422,
    _rollout_projection,
    _runtime_registry,
    _secret_provider,
    _tenant_graph_resolver,
    logger,
)


router: APIRouter = APIRouter()


@router.post("/api/graphs", response_model=SaveGraphResponse)
def save_graph(
    raw: dict[str, Any], principal: Principal = Depends(require("operate"))
) -> SaveGraphResponse:
    services = _main.services_for(principal)
    graph = parse_graph(raw)
    graph_id = services.graph_store.save(raw)
    return SaveGraphResponse(id=graph_id, version=graph.version)


@router.get("/api/graphs")
def list_graphs(principal: Principal = Depends(require("read"))) -> dict[str, list[dict[str, Any]]]:
    """列出已保存图（subgraph 节点选择器数据源，04 §5.7；按租户分区，04 §5.14）。"""
    return {"items": _main.services_for(principal).graph_store.list()}


@router.get("/api/graphs/{graph_id}")
def get_graph(
    graph_id: str,
    releaseVersion: int | None = None,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    raw = _main.services_for(principal).graph_store.get(graph_id, releaseVersion)
    if raw is None:
        # 跨租户访问同样 404，不泄漏资源存在性（04 §5.14）
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return raw


@router.put("/api/graphs/{graph_id}")
def update_graph(
    graph_id: str,
    raw: dict[str, Any],
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """覆盖已存图的 latest 草稿（M9 发布流：同一 graph 迭代多版本，不新建 id）。

    已发布版本不可变、不受影响；图不存在（含跨租户）404。校验与 POST /api/graphs 一致。
    """
    services = _main.services_for(principal)
    graph = parse_graph(raw)
    try:
        services.graph_store.update_draft(graph_id, raw)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{exc.args[0]}") from exc
    return {"id": graph_id, "version": graph.version}


@router.post("/api/graphs/{graph_id}/release-gate")
@_heavy_batch_guard
def run_graph_release_gate(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """发布前批量回放门禁：只跑门禁不发布（M9，03 `release_gate`；D26 部分取回）。

    对当前 latest 草稿逐例重跑 graph_id 匹配的录制用例并比对；无草稿/图不存在 404。
    """
    services = _main.services_for(principal)
    report = _release_gate_for_draft(services, graph_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在或无待发布草稿：{graph_id}")
    # D26 报告 v1：每次门禁运行沉淀（manual，含 skipped），响应纯超集带报告 id（03 release_report）
    return services.report_store.record(graph_id=graph_id, trigger="manual", report=report)


@router.get("/api/graphs/{graph_id}/release-reports")
def list_graph_release_reports(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """本图批量回放报告历史（倒序摘要，不含 cases；D26 报告 v1，03 release_report）。

    图在本租户不存在（无草稿且无发布版）→ 404，跨租户不泄漏存在性（照 rollout 端点）。
    """
    services = _main.services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return {"items": services.report_store.list_summary(graph_id)}


@router.get("/api/release-reports")
def list_all_release_reports(
    limit: int = 100, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """跨图批量回放报告看板（docs/28 §2.4）：倒序摘要，不含 cases，read 角色。

    limit 默认 100、上限 200（非整数 query 由 FastAPI 422）；报告两档：内存 ring 100 /
    PG 表 `release_reports`（迁移 022 已落库，PG 档不做 ring 淘汰），原「不 PG 化」口径过时
    （docs/61 §6 H5 勘误）。
    """
    services = _main.services_for(principal)
    bounded = max(1, min(limit, 200))
    return {"items": services.report_store.list_all_summary(limit=bounded)}


@router.get("/api/graphs/{graph_id}/release-reports/{report_id}")
def get_graph_release_report(
    graph_id: str, report_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """报告详情（含 cases 逐例结果；D26 报告 v1，03 release_report）。

    图不存在、报告不属于该图或不存在 → 404（跨租户/跨图不泄漏存在性）。
    """
    services = _main.services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    report = services.report_store.get(graph_id, report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"发布报告不存在：{graph_id}/{report_id}"
        )
    return report


@router.get("/api/graphs/{graph_id}/release-reports/{report_id}/export")
def export_graph_release_report(
    graph_id: str,
    report_id: str,
    format: Literal["csv", "json"] = "csv",
    principal: Principal = Depends(require("read")),
):
    """导出报告为 CSV/JSON 下载（D26 报告导出；read 角色；404 口径同详情端点，03 release_report）。"""
    services = _main.services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    report = services.report_store.get(graph_id, report_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail=f"发布报告不存在：{graph_id}/{report_id}"
        )
    if format == "json":
        return JSONResponse(
            report,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{report_id}.json"'
            },
        )
    # UTF-8 BOM 让 Excel 直接识别中文表头（零新依赖，stdlib csv 渲染见 reports.report_to_csv）。
    csv_text = report_to_csv(report)
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.csv"'},
    )


@router.post("/api/graphs/{graph_id}/shadow-runs", status_code=201)
def create_shadow_run(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """影子运行：旁路跑完整决策链，读透传/写短路，不进生产观测面（docs/33 §3.4）。

    预置全部 human_approval 节点 approved 秒过；不写 run_store/RunRecord、不触发告警与灰度
    门控、不产 tool_metric（loader shadow 短路）；独立 tracer（trace_id 入记录）与独立审批
    broker（不污染租户 pending）；异常也沉淀 status=error 记录。
    """
    services = _main.services_for(principal)
    graph = _load_graph_or_404(services, graph_id, None)
    body = payload or {}
    raw_inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    # approvals 是运行控制键（不进全局变量、不回记入记录 inputs）。
    inputs = dict(raw_inputs)
    presets = preset_all_approvals(graph, resolver=_tenant_graph_resolver(services))
    if presets:
        inputs["approvals"] = {**presets, **(inputs.get("approvals") or {})}
    # 事件等待无法在影子中被信号放行：预置空 payload 秒过（docs/47 §3.3）。
    # 打包 ZJ（D47）：递归下潜子图，键路径限定（"sub-1/wait-1"），子图内 wait 同样秒过。
    event_presets = preset_all_wait_events(graph, resolver=_tenant_graph_resolver(services))
    if event_presets:
        inputs["waitEvents"] = {**event_presets, **(inputs.get("waitEvents") or {})}
    outcome = _parse_human_outcome(body.get("human_outcome"))

    registry = _runtime_registry(services)
    tool_permissions = _tool_permissions(registry)
    node_index = {node.id: node for node in graph.nodes}
    top_events: list[tuple[str, dict[str, Any]]] = []

    def shadow_emit(event: dict[str, Any]) -> None:
        # 只收顶层 node_end（子图内部事件带 subgraphPath，与监控 tool_calls 同口径排除）。
        if event.get("type") == "node_end" and not event.get("subgraphPath"):
            output = event.get("output")
            if isinstance(output, dict):
                top_events.append((event["node_id"], output))

    tracer = Tracer(graph_id=graph_id)
    shadow_broker = ApprovalBroker()  # 独立 broker：预置秒过且不污染租户 pending
    status = "completed"
    error: str | None = None
    try:
        _main.run_graph(
            graph,
            inputs=inputs,
            registry=registry,
            approval_broker=shadow_broker,
            graph_id=graph_id,
            graph_resolver=_tenant_graph_resolver(services),
            emit=shadow_emit,
            tracer=tracer,
            shadow=True,
            tenant_id=principal.tenant_id,
            secret_provider=_secret_provider,
            memory_repo=services.memory_store,
        )
    except Exception as exc:  # 影子异常也沉淀记录，绝不影响生产链路
        status = "error"
        error = safe_persisted_error(exc, context="shadow run")

    decisions, intents = extract_shadow_events(top_events, node_index, tool_permissions)
    return services.shadow_store.add(
        graph_id=graph_id,
        trace_id=tracer.trace_id,
        decisions=decisions,
        tool_intents=intents,
        inputs=raw_inputs or None,
        status=status,
        error=error,
        human_outcome=outcome,
    )


@router.post("/api/graphs/{graph_id}/shadow-runs/stream")
async def create_shadow_run_stream(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> StreamingResponse:
    """影子运行 SSE：实时下发 node_start/node_end，终帧携带完整 ShadowRun（docs/114）。

    影子纪律与沉淀形状同同步版 /shadow-runs：READ 透传/写短路、预置审批与 wait 秒过、
    独立 broker/tracer、不写 run_store/RunRecord、不触发告警灰度、不产 tool_metric；
    业务异常也沉淀 status=error 记录（HTTP 不报错），统一在 result 终帧表达，不设
    event:error。边界（docs/125 §3.1）：worker 暴毙或连沉淀都失败（未发生沉淀）时，
    生成器合成 event:error 安全终帧，不伪造 ShadowRun、不挂死连接。
    """
    services = _main.services_for(principal)
    graph = _load_graph_or_404(services, graph_id, None)
    body = payload or {}
    raw_inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    inputs = dict(raw_inputs)
    presets = preset_all_approvals(graph, resolver=_tenant_graph_resolver(services))
    if presets:
        inputs["approvals"] = {**presets, **(inputs.get("approvals") or {})}
    event_presets = preset_all_wait_events(graph, resolver=_tenant_graph_resolver(services))
    if event_presets:
        inputs["waitEvents"] = {**event_presets, **(inputs.get("waitEvents") or {})}
    outcome = _parse_human_outcome(body.get("human_outcome"))

    registry = _runtime_registry(services)
    tool_permissions = _tool_permissions(registry)
    node_index = {node.id: node for node in graph.nodes}
    shadow_store = services.shadow_store
    tenant_id = principal.tenant_id
    graph_resolver = _tenant_graph_resolver(services)
    loop = asyncio.get_running_loop()

    async def event_stream():
        bridge = SSEBridge(loop)
        top_events: list[tuple[str, dict[str, Any]]] = []

        def shadow_emit(event: dict[str, Any]) -> None:
            if event.get("type") == "node_end" and not event.get("subgraphPath"):
                output = event.get("output")
                if isinstance(output, dict):
                    top_events.append((event["node_id"], output))
            bridge.offer_threadsafe(event)

        tracer = Tracer(graph_id=graph_id)
        shadow_broker = ApprovalBroker()
        status = "completed"
        error: str | None = None

        def worker() -> None:
            nonlocal status, error
            try:
                _main.run_graph(
                    graph,
                    inputs=inputs,
                    registry=registry,
                    approval_broker=shadow_broker,
                    graph_id=graph_id,
                    graph_resolver=graph_resolver,
                    emit=shadow_emit,
                    tracer=tracer,
                    shadow=True,
                    tenant_id=tenant_id,
                    secret_provider=_secret_provider,
                    memory_repo=services.memory_store,
                )
            except Exception as exc:  # 影子业务异常沉淀 error 记录，不影响生产链路
                status = "error"
                error = safe_persisted_error(exc, context="shadow run stream")
            try:
                decisions, intents = extract_shadow_events(
                    top_events, node_index, tool_permissions
                )
                record = shadow_store.add(
                    graph_id=graph_id,
                    trace_id=tracer.trace_id,
                    decisions=decisions,
                    tool_intents=intents,
                    inputs=raw_inputs or None,
                    status=status,
                    error=error,
                    human_outcome=outcome,
                )
            except Exception:
                # 收尾（extract/沉淀）本身暴毙：无法给 result，只走基础设施 error 通道。
                logger.exception(
                    "shadow run stream finalization failed (graph=%s trace=%s)",
                    graph_id,
                    tracer.trace_id,
                )
                bridge.offer_threadsafe({"__error__": _unexpected_stream_error()})
                return
            bridge.offer_threadsafe({"__result__": record})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        bridge.get(), _main.SSE_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # worker 暴毙且无积压帧：合成 error 终帧，不让连接永久悬挂（#1）。
                    if not thread.is_alive() and not bridge.has_pending:
                        logger.error(
                            "shadow run stream worker died without terminal frame "
                            "(graph=%s trace=%s)",
                            graph_id,
                            tracer.trace_id,
                        )
                        yield (
                            "event: error\ndata: "
                            + json.dumps(
                                {"detail": _unexpected_stream_error()},
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                        break
                    yield ": keepalive\n\n"
                    continue
                if "__result__" in event:
                    yield (
                        "event: result\ndata: "
                        + json.dumps(event["__result__"], ensure_ascii=False)
                        + "\n\n"
                    )
                    break
                if "__error__" in event:
                    yield (
                        "event: error\ndata: "
                        + json.dumps({"detail": event["__error__"]}, ensure_ascii=False)
                        + "\n\n"
                    )
                    break
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # 断连不是取消（docs/24 §3）：close 后 worker 的 offer 是廉价 no-op，
            # run_graph 不中断；生成器退出即释放，不占工作线程（#2/#4）。
            bridge.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/graphs/{graph_id}/publish", response_model=PublishGraphResponse)
def publish_graph(
    graph_id: str,
    request: PublishGraphRequest | None = None,
    principal: Principal = Depends(require("operate")),
) -> PublishGraphResponse:
    """发布 latest 草稿为不可变版本（M6，docs/20 §4.1 / ADR T19）。

    M9：可选 body ``{"gate": true}``——先跑发布前批量回放门禁，blocked → 409
    带完整 GateReport 且不产新版本；total=0（skipped）不阻塞（03 `release_gate`）。
    """
    services = _main.services_for(principal)
    if request is not None and request.gate:
        report = _release_gate_for_draft(services, graph_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
        # D26 报告 v1：发布门禁沉淀（publish-gate，通过/blocked/skipped 均沉淀，409 报告体同样带 id）
        report = services.report_store.record(
            graph_id=graph_id, trigger="publish-gate", report=report
        )
        if report["blocked"]:
            raise HTTPException(
                status_code=409,
                detail={"message": "发布门禁未通过，已拦截发布（存在不匹配用例）", "report": report},
            )
    try:
        release_version = publish_graph_version(services.graph_store, graph_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{exc.args[0]}") from exc
    # docs/68 §1 D-4：调度不是用户另填的一张表，是发布的派生物（同处撤销已删掉定时节点的调度）。
    _derive_schedule_on_publish(services, principal.tenant_id, graph_id, release_version)
    return PublishGraphResponse(id=graph_id, releaseVersion=release_version)


@router.get("/api/graphs/{graph_id}/subgraph-upgrades")
def subgraph_upgrades(
    graph_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """发布前子图版本升级体检（docs/28 §5.2 ⑪，read）：纯只读、不产版本、不阻断。

    返 ``{items: [{node_id, sub_id, from_version, to_version, first_pin}]}``；
    草稿不存在 404。v1 只扫顶层 subgraph、只对已发布版本号（不检测子图草稿 dirty）。
    """
    plan = subgraph_upgrade_plan(_main.services_for(principal).graph_store, graph_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return {"items": plan}


@router.post("/api/graphs/{graph_id}/subgraph-upgrades")
def apply_subgraph_upgrades_endpoint(
    graph_id: str,
    request: SubgraphUpgradesApplyRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """把草稿顶层 subgraph 引用显式升级到体检目标版本并存回草稿（打包 ZU2，D21 动作侧）。

    升级 = 把 ``config.graphId`` 改写为 ``sub_id@to_version``（与发布期钉版同一目标），
    不产新版本、不动已发布版本；升级后建议重跑发布门禁回归再发布。草稿不存在 404；
    指定 node_id 不在升级清单（非 subgraph / 无版本变化）→ 422。空清单幂等返空。
    """
    services = _main.services_for(principal)
    try:
        applied = apply_subgraph_upgrades(
            services.graph_store, graph_id, request.node_ids
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"节点 {exc.args[0]} 不在子图升级清单中（非子图节点或无版本变化）",
        ) from exc
    if applied is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return {"applied": applied}


@router.get("/api/graphs/{graph_id}/versions")
def list_graph_versions(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, list[int]]:
    """已发布版本号列表（升序；未发布过 → 空列表）（M6）。"""
    return {"items": _main.services_for(principal).graph_store.list_versions(graph_id)}


@router.get("/api/graphs/{graph_id}/diff")
def graph_diff(
    graph_id: str,
    from_version: int | None = Query(default=None, alias="fromVersion"),
    to_version: int | None = Query(default=None, alias="toVersion"),
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """两版 graph 配置结构差异（B3，D26 子集，read）：纯只读、不产版本、不阻断。

    - toVersion 缺省＝latest 草稿；给定＝该发布版本快照。
    - fromVersion 缺省＝最近发布版本；该图从未发布时基线取空图（首次发布前全为新增）。
    目标快照缺失（图/版本不存在）→ 404，跨租户不泄漏存在性。
    """
    store = _main.services_for(principal).graph_store
    to_raw = (
        store.get(graph_id) if to_version is None else store.get(graph_id, to_version)
    )
    if to_raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 或目标版本不存在：{graph_id}")
    if from_version is None:
        versions = store.list_versions(graph_id)
        from_raw = store.get(graph_id, versions[-1]) if versions else {}
        from_label: int | None = versions[-1] if versions else None
    else:
        from_raw = store.get(graph_id, from_version)
        from_label = from_version
        if from_raw is None:
            raise HTTPException(status_code=404, detail=f"基线版本不存在：{from_version}")
    diff = diff_graph(from_raw, to_raw)
    return {
        "graphId": graph_id,
        "fromVersion": from_label,
        "toVersion": to_version,  # None 表示 latest 草稿
        "summary": diff_summary(diff),
        "diff": diff,
    }


@router.get("/api/graphs/{graph_id}/rollout")
def get_rollout(
    graph_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """灰度配置与运行态投影（M9，03 `rollout_config`；从未配置返 idle 默认态）。

    图在本租户不存在（无草稿且无发布版）→ 404，跨租户不泄漏存在性（04 §5.14）。
    """
    services = _main.services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    state = services.routing_store.snapshot(graph_id)
    return _rollout_projection(state)


@router.put("/api/graphs/{graph_id}/rollout")
def put_rollout(
    graph_id: str,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """存灰度配置（只存不启动；非法形状 422 中文；M9，04 §5.16）。"""
    config = _rollout_config_or_422(body)
    state = _main.services_for(principal).routing_store.configure(graph_id, config)
    return _rollout_projection(state)


@router.post("/api/graphs/{graph_id}/rollout/start")
def start_rollout(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """idle→canary：取最新两个发布版（stable/candidate）；未配置/版本不足/非 idle → 409。"""
    services = _main.services_for(principal)
    versions = services.graph_store.list_versions(graph_id)
    try:
        state = services.routing_store.start(graph_id, versions)
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@router.post("/api/graphs/{graph_id}/rollout/promote")
def promote_rollout(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """canary→full：唯一放量路径，仅手动（不存在任何自动 promote）。"""
    try:
        state = _main.services_for(principal).routing_store.promote(graph_id)
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@router.post("/api/graphs/{graph_id}/rollout/rollback")
def rollback_rollout(
    graph_id: str,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """任意态→rolled_back（幂等；candidate 撤流、stable 接新流量；M9 门控自动回滚走同一函数）。"""
    reason = (body or {}).get("reason") or "manual rollback"
    try:
        state = _main.services_for(principal).routing_store.rollback(
            graph_id, actor="manual", reason=str(reason)
        )
    except RolloutError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _rollout_projection(state)


@router.post("/api/graphs/{graph_id}/rollout/reset")
def reset_rollout(
    graph_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """任意非 idle 态 → idle：**保留灰度配置**，清运行字段（stable/candidate/计数/回滚痕迹）。

    O-4 修复（docs/135 §6.6）：rolled_back 原为终态，回滚/实验 conclude 后该图无法再开
    灰度/实验；reset 提供显式复位路径。幂等：idle（含从未配置）返现状 200。
    图在本租户不存在 → 404（照 GET rollout 的校验，跨租户不泄漏存在性）。
    """
    services = _main.services_for(principal)
    if services.graph_store.get(graph_id) is None and not services.graph_store.list_versions(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    state = services.routing_store.reset_graph(graph_id)
    return _rollout_projection(state)

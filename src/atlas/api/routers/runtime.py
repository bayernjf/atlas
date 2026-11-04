"""P2-1 split: Compile/run endpoints, runs, tasks, waits, approvals runtime, debug.

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
import time
import uuid
from atlas.api.sse import SSEBridge
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import WaitAlreadySignaled, WaitTokenNotFound
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.cycles.state import cycle_registry
from atlas.debug import DebugController, DebugStopped
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend, compile_graph, runtime_error_meta
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.llm.metering import usage_context
from atlas.monitoring import extract_business, extract_node_results
from atlas.routing import evaluate_after_run
from atlas.services.error_safety import safe_run_error as _safe_run_error, unexpected_stream_error as _unexpected_stream_error
from atlas.storage.recovery import RESOLVE_FRAME_NOT_CLAIMED, RESOLVE_FRAME_NOT_FOUND, RESOLVE_OK, interruption_view
from atlas.tracing import Tracer
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Any
from atlas.api.main import (
    CompileResponse,
    InterruptionResolveRequest,
    ResumeDebugRequest,
    RunGraphResponse,
    _PUBLIC_URL,
    _SYNC_RUN_MAX_CONCURRENT,
    _SYNC_RUN_POOL,
    _cycle_session_or_404,
    _graph_version,
    _load_graph_or_404,
    _parse_wait_event_key,
    _parse_wait_payload,
    _record_audit,
    _resolve_event_version,
    _run_saved_graph_impl,
    _runtime_registry,
    _secret_provider,
    _sync_run_slots,
    _tenant_graph_resolver,
    _tool_call_from_event,
    _validate_debug,
    logger,
)


router: APIRouter = APIRouter()


@router.get("/api/runs/{run_id}/cycle")
def get_run_cycle(
    run_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """圈会话投影（read 档，只读）：圈序号/累计 token/累计墙钟/终止原因/各圈 run。

    非圈运行（`cycleGroupId` 为空）或会话已被 ring 淘汰 ⇒ 404 `CYCLE_NOT_FOUND`。
    **零新错误码**：404 走既有纯文本 detail 惯例（同 `/api/runs/{id}/trace` 的 404 族）。
    """
    session = _cycle_session_or_404(run_id, principal.tenant_id)
    return session.public_view()


@router.post("/api/runs/{run_id}/cycle/stop")
def stop_run_cycle(
    run_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """停**整条**圈会话（operate；C-D8）：已终态 ⇒ 200 幂等（状态照实回，不覆写历史）。

    与 `POST /api/runs/{id}/cancel` 的分工：cancel 停**这一圈**，本端点停**整条会话**
    （此后不再开新圈；已在跑的那一圈由 cancel 收尾）。
    """
    session = _cycle_session_or_404(run_id, principal.tenant_id)
    stopped = cycle_registry().stop(session.group_id) or session
    return stopped.public_view()


@router.post("/api/graphs/{graph_id}/compile", response_model=CompileResponse)
def compile_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> CompileResponse:
    services = _main.services_for(principal)
    graph = _load_graph_or_404(services, graph_id, (payload or {}).get("releaseVersion"))
    # D78：compile 端点与 run 端点同构注入 registry——prod 且演示面关闭时
    # `_resolve_registry(None)` 会 raise（loader.py:409-412），缺注入即 500。
    compile_graph(
        graph,
        graph_id=graph_id,
        graph_resolver=_tenant_graph_resolver(services),
        registry=_runtime_registry(services),
    )

    incoming = {edge.target for edge in graph.edges}
    outgoing = {edge.source for edge in graph.edges}
    return CompileResponse(
        id=graph_id,
        nodes=[{"id": node.id, "type": node.type, "name": node.name} for node in graph.nodes],
        edges=[{"id": edge.id, "source": edge.source, "target": edge.target} for edge in graph.edges],
        entrypoints=[node.id for node in graph.nodes if node.id not in incoming],
        terminals=[node.id for node in graph.nodes if node.id not in outgoing],
    )


@router.post("/api/graphs/{graph_id}/run", response_model=RunGraphResponse)
async def run_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> RunGraphResponse:
    """同步跑一次图（O-5 彻底解耦：async 端点 + 专用 run 线程池，挂起不占 uvicorn
    线程池；信号量保留 429 语义；逻辑在 `_run_saved_graph_impl`）。"""
    if not _sync_run_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"同步运行并发已达上限 {_SYNC_RUN_MAX_CONCURRENT}：请稍后重试或改用 /run/stream（SSE）",
        )
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _SYNC_RUN_POOL,
            _run_saved_graph_impl,
            graph_id,
            payload,
            principal,
        )
    finally:
        _sync_run_slots.release()


@router.post("/api/graphs/{graph_id}/run/stream")
async def run_saved_graph_stream(
    graph_id: str,
    payload: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> StreamingResponse:
    """SSE：node_start/node_end/run_end 实时推送到画布（08 §7.3 验收 5）。

    run_graph 在后台 daemon 线程执行、帧经有界 SSEBridge 实时下发（真流式；端点
    async 化，等待不占工作线程，#2/#4）；human_approval 节点依赖 node_start 在阻塞前
    到达，前端凭 token 调决策端点放行。租户服务 bundle 在请求线程解析后显式透传
    worker（06 §6.12，无线程上下文变量）。worker 暴毙或收尾二次异常时由生成器合成
    安全 error 终帧，连接不悬挂（#1，docs/125 §3.1）；断连只关桥接、不取消 run_graph。
    """
    services = _main.services_for(principal)
    body = payload or {}
    # M9：event 在请求线程解析（Principal 不进 worker），钉住的版本随闭包透传后台线程。
    release_version, resolved_version = _resolve_event_version(services, principal, graph_id, body)
    graph = _load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    inputs = body.get("inputs")
    debug = body.get("debug")
    graph_view = {"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]}
    debug_session = None
    if debug is not None:
        breakpoints = _validate_debug(graph, debug)
        debug_session = services.debug_broker.create(graph_id=graph_id, breakpoints=breakpoints)
        if any(
            node.type == "wait" and node.config.get("waitType") == "event"
            for node in graph.nodes
        ):
            raise HTTPException(status_code=422, detail="事件等待不支持单步调试")

    # worker 启动前固定当前租户的分区对象，避免跨租户串用
    registry = _runtime_registry(services)
    approval_broker = services.approval_broker
    approval_notifier = EmailApprovalNotifier(
        services.message_service, _PUBLIC_URL, principal.tenant_id
    )
    graph_resolver = _tenant_graph_resolver(services)
    monitoring = services.monitoring
    run_store = services.run_store
    run_id = uuid.uuid4().hex
    frame_sink = _main._frame_sink_for(principal.tenant_id, run_store, run_id)
    # B 包（docs/27 §4.1）：worker 启动前登记取消事件（请求线程，确保端点可达时句柄已在）。
    cancellation_broker = services.cancellation_broker
    cancel_event = cancellation_broker.register(run_id)
    # M10：真实运行（非 debug）建 tracer 并显式透传 worker（06 §6.12 后台线程不靠全局）；
    # debug 单步会话显式传 None 不埋点（04 §5.13/§5.15，SSE 帧保持旧形状）。
    tracer: Tracer | None = (
        Tracer(graph_id=graph_id, graph_version=_graph_version(graph_id, release_version))
        if debug is None
        else None
    )
    loop = asyncio.get_running_loop()

    async def event_stream():
        bridge = SSEBridge(loop)

        def emit(event: dict[str, Any]) -> None:
            bridge.offer_threadsafe(event)

        debug_controller = (
            DebugController(debug_session, emit, is_cancelled=cancel_event.is_set)
            if debug_session is not None
            else None
        )
        # debug 会话不是真实运行，全程不埋点（04 §5.13）
        monitored = debug_controller is None
        collected: dict[str, Any] = {}
        tool_calls: list[dict[str, Any]] = []

        def recording_emit(event: dict[str, Any]) -> None:
            # A 包（docs/27 §10.1）：监控/运行产出只收顶层 node_end；带 subgraphPath 的
            # 子图内部节点不进 collected（子图结果归在 subgraph 节点），但仍转发 SSE 上屏。
            if event.get("type") == "node_end" and not event.get("subgraphPath"):
                collected[event["node_id"]] = event.get("output")
            # docs/28 §4.1 ⑧：另册收集顶层 tool_metric（子层已被命名空间 wrapper 白名单吞掉）。
            elif event.get("type") == "tool_metric" and not event.get("subgraphPath"):
                tool_calls.append(_tool_call_from_event(event))
            emit(event)

        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()

        def _finalize_run(
            kind: str,
            *,
            result: dict[str, Any] | None = None,
            safe_error: str | None = None,
        ) -> None:
            """终态收尾收口：finish/record_run/灰度评估（docs/125 §3.1）。

            仅真实运行（非 debug）有收尾；任一步抛错由 worker 捕获记日志并转安全
            error 终帧，不让异常裸逃出 worker 线程。
            """
            if not monitored:
                return
            duration_ms = (time.monotonic() - started) * 1000
            if kind == "completed":
                assert result is not None
                run_store.finish(
                    run_id=run_id, status="completed",
                    outputs=result["outputs"], trace=result["trace"],
                )
                record = monitoring.record_run(
                    graph_id=graph_id,
                    mode="stream",
                    status="completed",
                    started_at=started_at,
                    duration_ms=duration_ms,
                    nodes=extract_node_results(graph_view, result["outputs"]),
                    trace_id=tracer.trace_id if tracer is not None else "",
                    resolved_version=resolved_version,
                    business=extract_business(
                        graph_view, result["outputs"], event_payload=event_payload
                    ),
                    tool_calls=tool_calls,
                    spans=result["traceTree"],
                )
                evaluate_after_run(services, record)
            elif kind == "cancelled":
                # 用户主动急停：不进灰度门控评估、不算失败。
                run_store.finish(run_id=run_id, status="cancelled")
                monitoring.record_run(
                    graph_id=graph_id,
                    mode="stream",
                    status="cancelled",
                    started_at=started_at,
                    duration_ms=duration_ms,
                    nodes=extract_node_results(graph_view, collected),
                    trace_id=tracer.trace_id if tracer is not None else "",
                    resolved_version=resolved_version,
                    tool_calls=tool_calls,
                    spans=tracer.to_tree() if tracer is not None else None,
                )
            else:  # failed
                run_store.finish(
                    run_id=run_id, status="failed",
                    error=safe_error,
                )
                record = monitoring.record_run(
                    graph_id=graph_id,
                    mode="stream",
                    status="error",
                    started_at=started_at,
                    duration_ms=duration_ms,
                    nodes=extract_node_results(graph_view, collected),
                    error=safe_error,
                    trace_id=tracer.trace_id if tracer is not None else "",
                    resolved_version=resolved_version,
                    tool_calls=tool_calls,
                    spans=tracer.to_tree() if tracer is not None else None,
                )
                evaluate_after_run(services, record)

        def worker() -> None:
            if monitored:
                run_store.begin(
                    run_id=run_id, graph_id=graph_id, mode="stream",
                    resolved_version=resolved_version,  # O-2：带 event 的路由版本落投影
                )
            # 各异常分支只决定终帧种类；收尾经 _finalize_run 整体保护，任何路径恰好一帧。
            terminal: dict[str, Any]
            try:
                # docs/129 打包 AU：worker 线程内显式设 usage 上下文（contextvars
                # 不跨线程继承，tracing 同款纪律；无此设置 ⇒ 流式运行的 LLM 调用不计）。
                with usage_context(
                    principal.tenant_id,
                    run_id=run_id,
                    graph_id=graph_id,
                    store=services.usage_store,
                ):
                    result = _main.run_graph(
                        graph,
                        inputs=inputs,
                        registry=registry,
                        approval_broker=approval_broker,
                        event_wait_broker=services.event_wait_broker,
                        approval_notifier=approval_notifier,
                        graph_id=graph_id,
                        emit=recording_emit if monitored else emit,
                        graph_resolver=graph_resolver,
                        debug_controller=debug_controller,
                        frame_sink=frame_sink,
                        resume_claim=_main._resume_claim_for(),
                        tracer=tracer,
                        graph_version=tracer.graph_version if tracer is not None else None,
                        is_cancelled=cancel_event.is_set,
                        _block_subgraph_suspend=(_main.STORAGE_BACKEND == "pg"),
                        tenant_id=principal.tenant_id,
                        secret_provider=_secret_provider,
                        memory_repo=services.memory_store,
                    )
            except RunSuperseded as exc:
                # docs/62 §2 D-4：输家零终态写——不 finish、不 record_run、不清帧，
                # 只发让位帧让前端停手（赢家的流会继续出结果）。
                logger.info("流式运行让位：%s", exc)
                terminal = {"__superseded__": exc.node_id}
            except WaitTimeoutSuspend as exc:
                # docs/136 打包 BS：挂起节点超时且 onTimeout="suspend"——run 保持 suspended
                # （不 finish、不 record_run 终态、不清帧），发提示帧让前端展示"超时仍挂起待决"。
                logger.info("流式运行超时保持挂起：%s", exc)
                terminal = {"__wait_timeout_suspended__": exc.node_id}
            except DebugStopped as exc:
                terminal = {"__stopped__": exc.node_id}
            except RunCancelled as exc:
                # 协作式急停：必须排在 except Exception 前（RunCancelled 是 Exception 子类）。
                node_id = exc.node_id
                try:
                    _finalize_run("cancelled")
                except Exception:
                    logger.exception(
                        "run %s cancellation finalization failed", run_id
                    )
                    terminal = {"__error__": _unexpected_stream_error()}
                else:
                    terminal = {"__cancelled__": node_id}
            except Exception as exc:  # 运行期异常经 SSE error 帧下发，不静默吞线程
                err_meta = runtime_error_meta(exc)
                if err_meta["errorCode"] == "RUNTIME_UNEXPECTED":
                    # 未知异常：细节只进服务端日志，任何面向客户的面都不带内部串（docs/101 D58）。
                    logger.exception("run %s failed with unexpected error", run_id, exc_info=exc)
                safe_error = _safe_run_error(exc)
                error_payload = {
                    "message": safe_error,
                    "code": err_meta["errorCode"],
                    "params": err_meta["errorParams"],
                }
                try:
                    _finalize_run("failed", safe_error=safe_error)
                except Exception:
                    # 收尾二次异常：不再有可信 error 帧，落日志后发固定安全终帧（#1）。
                    logger.exception("run %s failure finalization failed", run_id)
                    terminal = {"__error__": _unexpected_stream_error()}
                else:
                    terminal = {"__error__": error_payload}
            else:
                try:
                    _finalize_run("completed", result=result)
                except Exception:
                    logger.exception("run %s completion finalization failed", run_id)
                    terminal = {"__error__": _unexpected_stream_error()}
                else:
                    terminal = {"__result__": result}
            finally:
                cancellation_broker.unregister(run_id)
            bridge.offer_threadsafe(terminal)

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
                            "run stream worker died without terminal frame "
                            "(run=%s graph=%s)",
                            run_id,
                            graph_id,
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
                    # M10：终帧为 run_end 超集（traceId/spanId/graphVersion）；完整 traceTree
                    # 只留在进程内返回值，不进 SSE（04 §5.15）。
                    result_payload = {
                        key: value
                        for key, value in event["__result__"].items()
                        if key != "traceTree"
                    }
                    if tracer is not None:
                        result_payload.setdefault("spanId", tracer.root.span_id)
                        if tracer.graph_version is not None:
                            result_payload.setdefault("graphVersion", tracer.graph_version)
                    yield (
                        "event: result\ndata: "
                        + json.dumps(
                            {"id": graph_id, **result_payload}, ensure_ascii=False
                        )
                        + "\n\n"
                    )
                    break
                if "__stopped__" in event:
                    yield (
                        "event: stopped\ndata: "
                        + json.dumps(
                            {"type": "stopped", "node_id": event["__stopped__"],
                             "reason": "user_stop"},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    break
                if "__superseded__" in event:
                    yield (
                        "event: superseded\ndata: "
                        + json.dumps(
                            {
                                "type": "superseded",
                                "node_id": event["__superseded__"],
                                "reason": "resumed_by_other_process",
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    break
                if "__wait_timeout_suspended__" in event:
                    # docs/136 打包 BS：挂起节点超时且 onTimeout="suspend"——run 未终态
                    # （保持 suspended 待人工恢复），流在此结束，前端据此展示"超时仍挂起"。
                    yield (
                        "event: wait_timeout_suspended\ndata: "
                        + json.dumps(
                            {
                                "type": "wait_timeout_suspended",
                                "node_id": event["__wait_timeout_suspended__"],
                                "reason": "timeout_kept_suspended",
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    break
                if "__cancelled__" in event:
                    yield (
                        "event: cancelled\ndata: "
                        + json.dumps(
                            {
                                "type": "cancelled",
                                "node_id": event["__cancelled__"],
                                "reason": "user_cancel",
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    break
                if "__error__" in event:
                    yield (
                        "event: error\ndata: "
                        + json.dumps(
                            {"detail": event["__error__"]}, ensure_ascii=False
                        )
                        + "\n\n"
                    )
                    break
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # 断连不是取消（docs/24 §3）：close 后 worker 的 offer 是廉价 no-op，
            # run_graph 不中断；生成器退出即释放，不占工作线程（#2/#4）。
            bridge.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/runs")
def list_runs(
    status: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户运行列表（新→旧；status 缺省=全部）（M5b，docs/24 §4）。"""
    if status is not None and status not in {
        "running", "suspended", "completed", "failed", "interrupted", "cancelled",
    }:
        raise HTTPException(status_code=422, detail="非法的 status 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    return {"items": _main.services_for(principal).run_store.list(status=status, limit=limit)}


@router.get("/api/runs/{run_id}")
def get_run(
    run_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单运行详情：状态/产出/轨迹/挂起信息；跨租户或不存在 → 404（docs/24 §4）。"""
    run = _main.services_for(principal).run_store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    return run


@router.post("/api/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """协作式急停（docs/27 §4.1）：置位本租户该 run 的取消事件，下一节点边界生效，
    不在 wait/approval/tool 阻塞中点强杀。运行中（含调试流）200，重复取消幂等 200；
    已结束且无注册句柄 409；run 不存在/跨租户 404。"""
    services = _main.services_for(principal)
    if services.cancellation_broker.cancel(run_id):
        return {"run_id": run_id, "cancelled": True}
    if services.run_store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    raise HTTPException(status_code=409, detail="运行已结束，无法取消")


@router.get("/api/tasks")
def list_tasks(
    state: str | None = None,
    assignee: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """本租户任务信封列表（新→旧；state/assignee 可过滤）（M7，docs/20 §4.2 / 12 任务端点）。"""
    if state is not None and state not in {
        "pending", "accepted", "running", "done", "failed", "timeout",
    }:
        raise HTTPException(status_code=422, detail="非法的 state 过滤值")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")
    tasks = _main.services_for(principal).task_store.list(state=state)
    if assignee:
        tasks = [task for task in tasks if task.assignee == assignee]
    return {"items": [task.model_dump() for task in tasks[:limit]]}


@router.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """单任务信封详情；跨租户或不存在 → 404（M7）。"""
    task = _main.services_for(principal).task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.model_dump()


@router.get("/api/waits")
def list_waits(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户 pending 的事件等待（进程内 broker，重启即失，docs/47 §4）。"""
    return {"items": _main.services_for(principal).event_wait_broker.list_pending()}


@router.get("/api/interruptions")
def list_interruptions(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户挂起帧的只读投影（docs/76 打包 Q，解 14 D42）。

    存在的理由：`resumed_at` 此前只在启动恢复路径被读一次，崩在续跑途中的 run 除了重启
    翻日志无处可查。**本端点不重放、不清帧、不改任何状态**——`claimed_suspended` 只是把
    D36 那颗雷显形，怎么处理仍未决定。

    投影本体在 `atlas.storage.recovery.interruption_view`（MCP 面 `atlas_list_interruptions`
    锚同一份，只差 resumeToken 收窄）。
    """
    run_store = _main.services_for(principal).run_store
    return interruption_view(
        backend=_main.STORAGE_BACKEND,
        tenant_id=principal.tenant_id,
        engine=_main.get_pg_backend().engine if _main.STORAGE_BACKEND == "pg" else None,
        run_status_of=lambda run_id: (run_store.get(run_id) or {}).get("status"),
        include_resume_token=True,
    )


@router.post("/api/interruptions/{resume_token}/resolve")
def resolve_interruption(
    resume_token: str,
    body: InterruptionResolveRequest,
    http_request: Request,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """人工认领后崩溃帧的收敛（docs/96 打包 BL，解 docs/14 D36 人工收敛半边）。

    唯一动作 abandon：把「帧已认领、run 却永久停在 suspended」的卡死运行在一个
    PG 事务内置 `interrupted` 终态并删帧。**不重放**——下游 create_refund 等非
    幂等，安全重放要幂等键＋逐节点检查点（docs/14 D56）。承重的是事务谓词，
    下面的折算只读状态给错误码，不承担互斥。
    """
    if body.action != "abandon":
        _record_audit(_main.services_for(principal), principal, http_request,
                      "interruption.resolve", 422)
        raise HTTPException(
            status_code=422,
            detail={"code": "INTERRUPTION_ACTION_UNSUPPORTED",
                    "message": f"不支持的收敛动作：{body.action}（v1 仅支持 abandon）",
                    "params": {"action": body.action}},
        )
    if _main.STORAGE_BACKEND != "pg":
        _record_audit(_main.services_for(principal), principal, http_request,
                      "interruption.resolve", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "INTERRUPTIONS_NOT_PERSISTED",
                    "message": "内存档挂起帧不跨进程持久化，无人工收敛能力"},
        )
    code, info = _main.abandon_claimed_suspended_frame(
        _main.get_pg_backend().engine,
        tenant_id=principal.tenant_id,
        token=resume_token,
    )
    status_code = 200
    if code == RESOLVE_OK:
        payload = {"resumeToken": resume_token, "runId": info.get("run_id"),
                   "resolved": "interrupted", "frameDeleted": True}
    elif code == RESOLVE_FRAME_NOT_FOUND:
        status_code = 404
        payload = {"code": "INTERRUPTION_FRAME_NOT_FOUND",
                   "message": f"挂起帧不存在或不属于当前租户：{resume_token}"}
    elif code == RESOLVE_FRAME_NOT_CLAIMED:
        status_code = 409
        payload = {"code": "INTERRUPTION_FRAME_NOT_CLAIMED",
                   "message": "该帧尚未被认领，重启恢复仍可能驱动它，不能人工了结"}
    else:
        status_code = 409
        payload = {"code": "INTERRUPTION_RUN_NOT_SUSPENDED",
                   "message": "该帧已认领但其运行不处于 suspended 状态",
                   "params": {"status": info.get("status")}}
    _record_audit(_main.services_for(principal), principal, http_request,
                  "interruption.resolve", status_code)
    if code != RESOLVE_OK:
        raise HTTPException(status_code=status_code, detail=payload)
    return payload


@router.post("/api/waits/events")
def signal_wait_event(
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 eventKey 广播信号，释放同 key 全部等待；无 pending 则入排队 ring（docs/55）。

    返回 released=本次释放等待数、queued=是否因无消费者进入 per-key 排队。
    """
    data = body or {}
    event_key = _parse_wait_event_key(data)
    payload = _parse_wait_payload(data)
    services = _main.services_for(principal)
    result = services.event_wait_broker.signal_key(event_key, payload)
    _record_audit(services, principal, http_request, "wait.signal_event", 200)
    return {"released": result["released"], "queued": result["queued"]}


@router.post("/api/waits/{token}/signal")
def signal_wait_token(
    token: str,
    http_request: Request,
    body: dict[str, Any] | None = None,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """按 token 直投信号：未知/已取走 404，重复信号 409。"""
    data = body or {}
    payload = _parse_wait_payload(data)
    services = _main.services_for(principal)
    try:
        services.event_wait_broker.signal_token(token, payload)
    except WaitTokenNotFound:
        _record_audit(services, principal, http_request, "wait.signal_token", 404)
        raise HTTPException(
            status_code=404,
            detail={"code": "WAIT_TOKEN_NOT_FOUND",
                    "message": f"等待不存在或已清理：{token}"},
        )
    except WaitAlreadySignaled:
        _record_audit(services, principal, http_request, "wait.signal_token", 409)
        raise HTTPException(
            status_code=409,
            detail={"code": "WAIT_ALREADY_SIGNALED",
                    "message": "该等待已有信号，重复提交不生效"},
        )
    _record_audit(services, principal, http_request, "wait.signal_token", 200)
    return {"token": token, "released": True}


@router.get("/api/debug")
def list_debug_pauses(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户活动调试暂停（04 §5.12；进程内，重启即失）。"""
    return {"items": _main.services_for(principal).debug_broker.list_pending()}


@router.post("/api/debug/{token}/resume")
def resume_debug(
    token: str,
    request: ResumeDebugRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, str]:
    broker = _main.services_for(principal).debug_broker
    session = broker.get_session(token)
    if session is None:
        raise HTTPException(status_code=404, detail=f"调试暂停不存在或已恢复：{token}")
    # 变量覆盖在 resolve（首决）前校验暂存；action=stop 忽略覆盖（§4.3）。
    if request.globals is not None and request.action != "stop":
        try:
            session.apply_overrides(token, request.globals)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not session.resolve(token, request.action):
        raise HTTPException(status_code=409, detail="该调试暂停已恢复，重复提交不生效")
    return {"token": token, "action": request.action}

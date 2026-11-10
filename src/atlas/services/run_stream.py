"""SSE run streaming, extracted from api/routers/runtime.py (service layer batch 2).

`prepare_stream_run` / `stream_saved_graph_events` carry the full existing
semantics of ``POST /api/graphs/{graph_id}/run/stream`` (daemon worker, bounded
SSEBridge fan-out, `_finalize_run` terminal accounting, heartbeat/dead-worker
guard, terminal-frame formatting) verbatim. Every dependency the route module
owns as module state — and that tests monkeypatch through ``atlas.api.main`` —
arrives via the :class:`StreamRunDeps` bundle instead of a global import, so
patching ``atlas.api.main.run_graph`` / ``SSE_HEARTBEAT_SECONDS`` etc. keeps
working: the router reads the current module globals when building the bundle.

Split across two entry points because the prelude must run on the request
thread: `prepare_stream_run` fixes the tenant partition objects and registers
the cancellation handle **before** the endpoint returns the StreamingResponse
(B 包 docs/27 §4.1：端点可达时句柄必须已在), while `stream_saved_graph_events`
is the async generator Starlette drains after the response starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from atlas.api.sse import SSEBridge
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.debug import DebugController, DebugStopped
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend, runtime_error_meta
from atlas.iam.principals import Principal
from atlas.iam.registry import TenantServices
from atlas.llm.metering import usage_context
from atlas.monitoring import extract_business, extract_node_results
from atlas.services.error_safety import safe_run_error as _safe_run_error
from atlas.services.error_safety import unexpected_stream_error as _unexpected_stream_error
from atlas.tracing import Tracer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamRunDeps:
    """Process-level dependencies the runtime router injects per request.

    - ``run_graph``: the graph executor; tests patch ``atlas.api.main.run_graph``.
    - ``frame_sink_for``: frame sink factory; tests patch ``_frame_sink_for``.
    - ``resume_claim_for``: claim-check factory for suspended frames.
    - ``evaluate_after_run``: M9 rollout gate (the runtime router's own
      ``atlas.routing`` import — NOT main's attribute — is what the stream path
      used before the split, so the router passes its own binding).
    - ``runtime_registry_of`` / ``tenant_graph_resolver_of``: per-tenant
      adapters (main's ``_runtime_registry`` / ``_tenant_graph_resolver``).
    - ``tool_call_from_event`` / ``graph_version``: main helpers the stream
      path imports from ``atlas.api.main``.
    - ``secret_provider`` / ``storage_backend`` / ``public_url``: process
      singletons owned by main, read at request time.
    - ``heartbeat_seconds``: SSE keepalive interval; tests patch
      ``atlas.api.main.SSE_HEARTBEAT_SECONDS`` before the request.
    """

    run_graph: Callable[..., Any]
    frame_sink_for: Callable[..., Any]
    resume_claim_for: Callable[[], Callable[[str], bool] | None]
    evaluate_after_run: Callable[..., Any]
    runtime_registry_of: Callable[[TenantServices], Any]
    tenant_graph_resolver_of: Callable[[TenantServices], Any]
    tool_call_from_event: Callable[..., Any]
    graph_version: Callable[..., Any]
    secret_provider: Any
    storage_backend: str
    public_url: str
    heartbeat_seconds: float


@dataclass(frozen=True)
class StreamRunContext:
    """Per-request artifacts fixed on the request thread, consumed by the stream."""

    graph_id: str
    graph: Any
    services: TenantServices
    tenant_id: str
    resolved_version: int | None
    event_payload: dict[str, Any]
    inputs: dict[str, Any] | None
    debug_session: Any | None
    graph_view: dict[str, Any]
    registry: Any
    approval_broker: Any
    approval_notifier: Any
    graph_resolver: Any
    monitoring: Any
    run_store: Any
    run_id: str
    frame_sink: Any
    cancel_event: Any
    tracer: Tracer | None
    loop: asyncio.AbstractEventLoop
    deps: StreamRunDeps


def prepare_stream_run(
    principal: Principal,
    services: TenantServices,
    *,
    graph_id: str,
    graph: Any,
    event_payload: dict[str, Any],
    inputs: dict[str, Any] | None,
    resolved_version: int | None,
    debug_session: Any | None,
    deps: StreamRunDeps,
) -> StreamRunContext:
    """请求线程侧预装配（端点函数体内同步执行，先于 StreamingResponse 返回）。

    worker 启动前固定当前租户的分区对象，避免跨租户串用；取消事件在请求线程
    登记（B 包 docs/27 §4.1，确保端点可达时句柄已在）；真实运行（非 debug）建
    tracer 并显式透传 worker（06 §6.12 后台线程不靠全局）；debug 单步会话显式
    传 None 不埋点（04 §5.13/§5.15，SSE 帧保持旧形状）。
    """
    registry = deps.runtime_registry_of(services)
    approval_broker = services.approval_broker
    approval_notifier = EmailApprovalNotifier(
        services.message_service, deps.public_url, principal.tenant_id
    )
    graph_resolver = deps.tenant_graph_resolver_of(services)
    monitoring = services.monitoring
    run_store = services.run_store
    run_id = uuid.uuid4().hex
    frame_sink = deps.frame_sink_for(principal.tenant_id, run_store, run_id)
    # B 包（docs/27 §4.1）：worker 启动前登记取消事件（请求线程，确保端点可达时句柄已在）。
    cancellation_broker = services.cancellation_broker
    cancel_event = cancellation_broker.register(run_id)
    # M10：真实运行（非 debug）建 tracer 并显式透传 worker；debug_session 仅在
    # debug 非 None 时创建，故 `debug_session is None` ⟺ 原 `debug is None` 分支。
    tracer: Tracer | None = (
        Tracer(graph_id=graph_id, graph_version=deps.graph_version(graph_id, resolved_version))
        if debug_session is None
        else None
    )
    return StreamRunContext(
        graph_id=graph_id,
        graph=graph,
        services=services,
        tenant_id=principal.tenant_id,
        resolved_version=resolved_version,
        event_payload=event_payload,
        inputs=inputs,
        debug_session=debug_session,
        graph_view={"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]},
        registry=registry,
        approval_broker=approval_broker,
        approval_notifier=approval_notifier,
        graph_resolver=graph_resolver,
        monitoring=monitoring,
        run_store=run_store,
        run_id=run_id,
        frame_sink=frame_sink,
        cancel_event=cancel_event,
        tracer=tracer,
        loop=asyncio.get_running_loop(),
        deps=deps,
    )


async def stream_saved_graph_events(ctx: StreamRunContext) -> AsyncIterator[str]:
    """SSE 帧生成器：worker 在后台 daemon 线程执行 run_graph，帧经有界桥接下发。

    语义逐字承接 runtime 路由的内联实现：human_approval 节点依赖 node_start 在
    阻塞前到达，前端凭 token 调决策端点放行。worker 暴毙或收尾二次异常时由生成
    器合成安全 error 终帧，连接不悬挂（#1，docs/125 §3.1）；断连只关桥接、不取消
    run_graph（docs/24 §3）。
    """
    deps = ctx.deps
    services = ctx.services
    graph_id = ctx.graph_id
    run_id = ctx.run_id
    bridge = SSEBridge(ctx.loop)

    def emit(event: dict[str, Any]) -> None:
        bridge.offer_threadsafe(event)

    debug_controller = (
        DebugController(ctx.debug_session, emit, is_cancelled=ctx.cancel_event.is_set)
        if ctx.debug_session is not None
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
            tool_calls.append(deps.tool_call_from_event(event))
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
            ctx.run_store.finish(
                run_id=run_id, status="completed",
                outputs=result["outputs"], trace=result["trace"],
            )
            record = ctx.monitoring.record_run(
                graph_id=graph_id,
                mode="stream",
                status="completed",
                started_at=started_at,
                duration_ms=duration_ms,
                nodes=extract_node_results(ctx.graph_view, result["outputs"]),
                trace_id=ctx.tracer.trace_id if ctx.tracer is not None else "",
                resolved_version=ctx.resolved_version,
                business=extract_business(
                    ctx.graph_view, result["outputs"], event_payload=ctx.event_payload
                ),
                tool_calls=tool_calls,
                spans=result["traceTree"],
            )
            deps.evaluate_after_run(services, record)
        elif kind == "cancelled":
            # 用户主动急停：不进灰度门控评估、不算失败。
            ctx.run_store.finish(run_id=run_id, status="cancelled")
            ctx.monitoring.record_run(
                graph_id=graph_id,
                mode="stream",
                status="cancelled",
                started_at=started_at,
                duration_ms=duration_ms,
                nodes=extract_node_results(ctx.graph_view, collected),
                trace_id=ctx.tracer.trace_id if ctx.tracer is not None else "",
                resolved_version=ctx.resolved_version,
                tool_calls=tool_calls,
                spans=ctx.tracer.to_tree() if ctx.tracer is not None else None,
            )
        else:  # failed
            ctx.run_store.finish(
                run_id=run_id, status="failed",
                error=safe_error,
            )
            record = ctx.monitoring.record_run(
                graph_id=graph_id,
                mode="stream",
                status="error",
                started_at=started_at,
                duration_ms=duration_ms,
                nodes=extract_node_results(ctx.graph_view, collected),
                error=safe_error,
                trace_id=ctx.tracer.trace_id if ctx.tracer is not None else "",
                resolved_version=ctx.resolved_version,
                tool_calls=tool_calls,
                spans=ctx.tracer.to_tree() if ctx.tracer is not None else None,
            )
            deps.evaluate_after_run(services, record)

    def worker() -> None:
        if monitored:
            ctx.run_store.begin(
                run_id=run_id, graph_id=graph_id, mode="stream",
                resolved_version=ctx.resolved_version,  # O-2：带 event 的路由版本落投影
            )
        # 各异常分支只决定终帧种类；收尾经 _finalize_run 整体保护，任何路径恰好一帧。
        terminal: dict[str, Any]
        try:
            # docs/129 打包 AU：worker 线程内显式设 usage 上下文（contextvars
            # 不跨线程继承，tracing 同款纪律；无此设置 ⇒ 流式运行的 LLM 调用不计）。
            with usage_context(
                ctx.tenant_id,
                run_id=run_id,
                graph_id=graph_id,
                store=services.usage_store,
            ):
                result = deps.run_graph(
                    ctx.graph,
                    inputs=ctx.inputs,
                    registry=ctx.registry,
                    approval_broker=ctx.approval_broker,
                    event_wait_broker=services.event_wait_broker,
                    approval_notifier=ctx.approval_notifier,
                    graph_id=graph_id,
                    emit=recording_emit if monitored else emit,
                    graph_resolver=ctx.graph_resolver,
                    debug_controller=debug_controller,
                    frame_sink=ctx.frame_sink,
                    resume_claim=deps.resume_claim_for(),
                    tracer=ctx.tracer,
                    graph_version=ctx.tracer.graph_version if ctx.tracer is not None else None,
                    is_cancelled=ctx.cancel_event.is_set,
                    _block_subgraph_suspend=(deps.storage_backend == "pg"),
                    tenant_id=ctx.tenant_id,
                    secret_provider=deps.secret_provider,
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
            ctx.services.cancellation_broker.unregister(run_id)
        bridge.offer_threadsafe(terminal)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while True:
            try:
                event = await asyncio.wait_for(bridge.get(), deps.heartbeat_seconds)
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
                if ctx.tracer is not None:
                    result_payload.setdefault("spanId", ctx.tracer.root.span_id)
                    if ctx.tracer.graph_version is not None:
                        result_payload.setdefault("graphVersion", ctx.tracer.graph_version)
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

"""Synchronous run orchestration, extracted from api/main.py (Tier0 review P2).

`run_saved_graph` / `run_sync_cycle` carry the full existing semantics of the
sync run entrypoint (cancel handles, superseded yielding, error accounting
with rollout gating, monitoring, frame sinks, usage context). Every dependency
that main.py owns as module-level state — and that tests monkeypatch through
``atlas.api.main`` — arrives via the :class:`SyncRunDeps` bundle instead of a
global import, so patching ``atlas.api.main.run_graph`` etc. keeps working:
main's thin wrapper reads the current module globals when building the bundle.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel

from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.cycles.supervisor import CycleOutcome, cycle_config, run_cycle_session
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend
from atlas.iam.principals import Principal
from atlas.iam.registry import TenantServices
from atlas.llm.metering import usage_context
from atlas.monitoring import extract_business, extract_node_results
from atlas.services import graph_release
from atlas.services.error_safety import safe_run_error as _safe_run_error
from atlas.tracing import Tracer

logger = logging.getLogger(__name__)


class RunGraphResponse(BaseModel):
    id: str
    status: str
    outputs: dict[str, Any]
    trace: list[str]
    # 打包 W（docs/84）：实际执行完成的运行携带种子；cancelled/suspended 早退响应为 None。
    rng_seed: int | None = None


@dataclass(frozen=True)
class SyncRunDeps:
    """Process-level dependencies main.py injects per call (patch seams preserved).

    - ``run_graph``: the graph executor; tests patch ``atlas.api.main.run_graph``.
    - ``frame_sink_for``: frame sink factory; tests patch ``_frame_sink_for``.
    - ``resume_claim_for``: claim-check factory for suspended frames.
    - ``evaluate_after_run``: M9 rollout gate; tests patch it on main.
    - ``runtime_registry_of`` / ``tenant_graph_resolver_of``: per-tenant adapters
      (main's ``_runtime_registry`` / ``_tenant_graph_resolver``).
    - ``secret_provider`` / ``public_url`` / ``storage_backend``: process
      singletons owned by main, read at call time.
    """

    services_for: Callable[[Principal], TenantServices]
    run_graph: Callable[..., Any]
    frame_sink_for: Callable[..., Any]
    resume_claim_for: Callable[[], Callable[[str], bool] | None]
    evaluate_after_run: Callable[..., Any]
    runtime_registry_of: Callable[[TenantServices], Any]
    tenant_graph_resolver_of: Callable[[TenantServices], Any]
    secret_provider: Any
    storage_backend: str
    public_url: str


def run_saved_graph(
    graph_id: str,
    payload: dict[str, Any] | None,
    principal: Principal,
    *,
    deps: SyncRunDeps,
) -> RunGraphResponse:
    """同步跑一次图（v1 也承载自走循环，见 `run_sync_cycle` 与 docs/130 C-D10）。

    **圈启用面**：图级 `loop` 配置存在且 `enabled!==false` ⇒ 本入口按圈会话驱动
    （每圈一次真实运行、各自 run_id，返回**最后一圈**的结果）；缺省配置走原路径逐字不变。
    """
    services = deps.services_for(principal)
    body = payload or {}
    release_version, resolved_version = graph_release.resolve_event_version(
        services, principal, graph_id, body
    )
    graph = graph_release.load_graph_or_404(services, graph_id, release_version)
    event_payload = (body.get("event") or {}).get("payload") or {}
    if body.get("debug") is not None:
        raise HTTPException(status_code=422, detail="单步调试仅支持流式运行 /run/stream")

    payloads: dict[str, dict[str, Any]] = {}

    def _run_once(step) -> Any:
        outcome, response = run_sync_cycle(
            services,
            principal,
            graph,
            graph_id=graph_id,
            release_version=release_version,
            resolved_version=resolved_version,
            event_payload=event_payload,
            inputs=step.inputs,
            cycle_seq=step.seq if step.group_id else 0,
            cycle_group_id=step.group_id,
            payloads=payloads,
            deps=deps,
        )
        return outcome

    if cycle_config(graph) is None:
        # 现状路径：不建会话、不发圈号（打包 BN C-D2 的零行为变化承诺）
        outcome, response = run_sync_cycle(
            services,
            principal,
            graph,
            graph_id=graph_id,
            release_version=release_version,
            resolved_version=resolved_version,
            event_payload=event_payload,
            inputs=body.get("inputs"),
            cycle_seq=0,
            cycle_group_id="",
            payloads=payloads,
            deps=deps,
        )
        return response if response is not None else RunGraphResponse(
            id=graph_id, **payloads[outcome.run_id]
        )

    session = run_cycle_session(
        services,
        graph,
        tenant_id=principal.tenant_id,
        graph_id=graph_id,
        inputs=dict(body.get("inputs") or {}),
        run_once=_run_once,
    )
    # `runs` 是 public_view 投影（每项是 dict），取最后一圈的 runId 去拿原始 run 结果
    last_run_id = session["runs"][-1]["runId"]
    return RunGraphResponse(id=graph_id, **payloads[last_run_id])


def run_sync_cycle(
    services: TenantServices,
    principal: Principal,
    graph: Any,
    *,
    graph_id: str,
    release_version: int | None,
    resolved_version: int | None,
    event_payload: dict[str, Any],
    inputs: dict[str, Any] | None,
    cycle_seq: int,
    cycle_group_id: str,
    payloads: dict[str, dict[str, Any]],
    deps: SyncRunDeps,
) -> tuple[Any, RunGraphResponse | None]:
    """跑**一圈**（= 一次真实运行），返回 `(CycleOutcome, 非 None 的 HTTP 响应 | None)`。

    本函数是同步入口的**全部**既有语义（cancel 句柄、superseded 让位、异常记账与
    灰度评估、监控埋点、frame_sink、usage 上下文）逐字搬运——圈能力只加在"调用几次"
    这一层，不改任何一圈内部的语义（docs/130 C-D1：一圈＝一次真实运行）。

    `cycle_seq`／`cycle_group_id` 缺省 0/''＝非圈运行（与迁移 050 的列缺省同义）。
    `payloads` 由调用方持有，用于把每圈的原始 run 结果带给 HTTP 层（返回最后一圈）。
    """
    graph_view = {"nodes": [{"id": node.id, "type": node.type} for node in graph.nodes]}
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    monitoring = services.monitoring
    run_id = uuid.uuid4().hex
    tracer = Tracer(
        graph_id=graph_id,
        graph_version=graph_release.graph_version(graph_id, release_version),
    )
    services.run_store.begin(
        run_id=run_id, graph_id=graph_id, mode="sync",
        cycle_seq=cycle_seq, cycle_group_id=cycle_group_id,
        resolved_version=resolved_version,  # O-2：带 event 的路由版本落投影（无 event 恒 None）
    )
    frame_sink = deps.frame_sink_for(principal.tenant_id, services.run_store, run_id)
    # docs/28 §4.1 ⑧：同步入口无 SSE，构造同形收集 emit（只收顶层 tool_metric，其余忽略）。
    tool_calls: list[dict[str, Any]] = []

    def _metric_collect(event: dict[str, Any]) -> None:
        if event.get("type") == "tool_metric" and not event.get("subgraphPath"):
            tool_calls.append(graph_release.tool_call_from_event(event))

    # 打包 O（docs/69 §1 D-1）：同步入口此前从不登记取消句柄（只有流式路径注册），
    # 在途急停恒落 409「运行已结束」。与流式同构——请求线程注册，finally 注销覆盖全部出口。
    cancellation_broker = services.cancellation_broker
    cancel_event = cancellation_broker.register(run_id)
    try:
        with usage_context(
            principal.tenant_id,
            run_id=run_id,
            graph_id=graph_id,
            store=services.usage_store,
        ):
            result = deps.run_graph(
                graph,
                inputs=inputs,
                registry=deps.runtime_registry_of(services),
                approval_broker=services.approval_broker,
                event_wait_broker=services.event_wait_broker,
                approval_notifier=EmailApprovalNotifier(
                    services.message_service, deps.public_url, principal.tenant_id
                ),
                graph_id=graph_id,
                graph_resolver=deps.tenant_graph_resolver_of(services),
                emit=_metric_collect,
                frame_sink=frame_sink,
                resume_claim=deps.resume_claim_for(),
                tracer=tracer,
                graph_version=tracer.graph_version,
                is_cancelled=cancel_event.is_set,
                _block_subgraph_suspend=(deps.storage_backend == "pg"),
                tenant_id=principal.tenant_id,
                secret_provider=deps.secret_provider,
                memory_repo=services.memory_store,
            )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：输家停止驱动——不写 run 终态、不记监控、不进门控评估，
        # 帧与终态都归赢家；本进程如实返回"仍在挂起"的形状（刻意不新增 run 状态/错误码）。
        logger.info("同步运行让位：%s", exc)
        return (
            CycleOutcome(run_id=run_id, status="suspended", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="suspended",
                outputs={},
                trace=[f"{exc.node_id}: superseded by another process"],
            ),
        )
    except WaitTimeoutSuspend as exc:
        # docs/136 打包 BS：挂起节点超时且 onTimeout="suspend"——run 保持 suspended
        # （不 finish、不记监控终态、不清帧、不进 evaluate_after_run），等人工后续恢复。
        logger.info("同步运行超时保持挂起：%s", exc)
        return (
            CycleOutcome(run_id=run_id, status="suspended", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="suspended",
                outputs={},
                trace=[f"{exc.node_id}: wait timeout kept suspended"],
            ),
        )
    except RunCancelled as exc:
        # 打包 O：同步入口的协作式急停与流式同形——用户主动 ⇒ 记 cancelled 且
        # **不调 evaluate_after_run**（不算失败、不进 M9 灰度门控）。
        # 必须排在 except Exception 之前（RunCancelled 是 Exception 子类）。
        services.run_store.finish(run_id=run_id, status="cancelled")
        monitoring.record_run(
            graph_id=graph_id,
            mode="sync",
            status="cancelled",
            started_at=started_at,
            duration_ms=(time.monotonic() - started) * 1000,
            nodes=[],
            trace_id=tracer.trace_id,
            resolved_version=resolved_version,
            tool_calls=tool_calls,
            spans=tracer.to_tree(),
        )
        return (
            CycleOutcome(run_id=run_id, status="cancelled", stop_run=True),
            RunGraphResponse(
                id=graph_id,
                status="cancelled",
                outputs={},
                trace=[f"{exc.node_id}: cancelled by user"],
            ),
        )
    except Exception as exc:
        services.run_store.finish(
            run_id=run_id, status="failed", error=_safe_run_error(exc)
        )
        record = monitoring.record_run(
            graph_id=graph_id,
            mode="sync",
            status="error",
            started_at=started_at,
            duration_ms=(time.monotonic() - started) * 1000,
            nodes=[],
            error=_safe_run_error(exc),
            trace_id=tracer.trace_id,
            resolved_version=resolved_version,
            tool_calls=tool_calls,
            spans=tracer.to_tree(),
        )
        deps.evaluate_after_run(services, record)  # M9：异常运行同样计入 candidate 门控
        raise
    finally:
        # 打包 O：句柄必须随请求退出而注销——留着会让「已结束再取消」从 409 翻成 200
        # （tests/test_run_cancel.py::test_cancel_finished_run_returns_409 守着这条不变量）。
        cancellation_broker.unregister(run_id)
    services.run_store.finish(
        run_id=run_id, status="completed",
        outputs=result["outputs"], trace=result["trace"],
    )
    record = monitoring.record_run(
        graph_id=graph_id,
        mode="sync",
        status="completed",
        started_at=started_at,
        duration_ms=(time.monotonic() - started) * 1000,
        nodes=extract_node_results(graph_view, result["outputs"]),
        trace_id=tracer.trace_id,
        resolved_version=resolved_version,
        business=extract_business(graph_view, result["outputs"], event_payload=event_payload),
        tool_calls=tool_calls,
        spans=result["traceTree"],
    )
    deps.evaluate_after_run(services, record)  # M9：灰度门控越阈自动回滚
    payloads[run_id] = result
    return CycleOutcome(
        run_id=run_id,
        status="completed",
        outputs=result.get("outputs", {}),
        variables={},
    ), None

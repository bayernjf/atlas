"""A2A 真实 run 执行器（docs/90 A-02）。

最小闭环只接**非交互图**：图内出现 `human_approval` / `wait`（含子图）时在启动前
拒绝，避免 A2A 请求线程被永久挂起。真实 run 记录写入租户 `run_store`，与平台
`/api/runs` 共用同一运行台账；v1 不提供 A2A 续跑/取消，交互图仍走平台会话。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from atlas.a2a.bot_runtime import BotRuntime
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.harness.registry import AdapterRegistry
from atlas.harness.runtime import build_base_registry, build_runtime_registry, resolve_database_client
from atlas.iam.deps import tenant_registry
from atlas.monitoring.metrics import extract_node_results
from atlas.security.bootstrap import demo_surface_enabled
from atlas.tracing.tracer import Tracer

INTERACTIVE_NODE_TYPES = frozenset({"human_approval", "wait"})
MAX_SUBGRAPH_DEPTH = 5


class A2ARunError(Exception):
    """可预期失败：调用方参数/图不满足真实 run 契约。"""

    def __init__(self, code: str, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


@dataclass(frozen=True)
class GraphRunResult:
    run_id: str
    graph_id: str
    status: str  # completed | input-required | failed
    outputs: dict[str, Any]
    trace: list[str]
    error: str | None
    wall_seconds: float
    agent_id: str = ""
    runtime_id: str = ""
    trace_id: str = ""


def _filter_registry(registry: AdapterRegistry, adapter_ids: frozenset[str]) -> AdapterRegistry:
    """只保留 Bot 声明的适配器；空集不裁剪（保持旧调用方全量可见）。"""
    if not adapter_ids:
        return registry
    filtered = AdapterRegistry()
    for item in registry.list_adapters():
        if item["id"] in adapter_ids:
            filtered.register(registry.get(item["id"]))
    return filtered


def _load_graph(services: Any, graph_id: str, release_version: int | None) -> dict[str, Any]:
    raw = services.graph_store.get(graph_id, release_version)
    if raw is None:
        suffix = f" release_version={release_version}" if release_version is not None else ""
        raise A2ARunError(
            "GRAPH_NOT_FOUND",
            f"graph not found in tenant={services.tenant_id}: {graph_id}{suffix}",
            data={"graphId": graph_id, "releaseVersion": release_version},
        )
    if not isinstance(raw, dict):
        raise A2ARunError("GRAPH_INVALID", f"graph payload is not an object: {graph_id}")
    return raw


def _assert_non_interactive(
    services: Any,
    raw: dict[str, Any],
    *,
    depth: int = 0,
    seen: tuple[str, ...] = (),
) -> None:
    raw_nodes = raw.get("nodes")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        node_id = node.get("id")
        if node_type in INTERACTIVE_NODE_TYPES:
            raise A2ARunError(
                "INTERACTIVE_GRAPH_UNSUPPORTED",
                (
                    "A2A v1 runs non-interactive graphs only; node "
                    f"{node_id!r} is {node_type!r}. Resolve approvals in the platform session."
                ),
                data={"nodeId": node_id, "nodeType": node_type},
            )
        if node_type != "subgraph":
            continue
        raw_config = node.get("config")
        config = raw_config if isinstance(raw_config, dict) else {}
        child_id = config.get("graphId")
        if not isinstance(child_id, str) or not child_id:
            continue
        if child_id in seen:
            raise A2ARunError("GRAPH_INVALID", f"subgraph cycle detected at {child_id!r}")
        if depth >= MAX_SUBGRAPH_DEPTH:
            raise A2ARunError("GRAPH_INVALID", "subgraph nesting exceeds A2A v1 limit")
        child_raw = services.graph_store.get(child_id)
        if child_raw is None:
            raise A2ARunError("GRAPH_NOT_FOUND", f"subgraph not found: {child_id}")
        _assert_non_interactive(
            services, child_raw, depth=depth + 1, seen=(*seen, child_id)
        )


def execute_graph_run(
    *,
    tenant_id: str,
    graph_id: str,
    inputs: dict[str, Any] | None = None,
    release_version: int | None = None,
    run_id: str | None = None,
    bot_runtime: BotRuntime | None = None,
    task_id: str = "",
) -> GraphRunResult:
    """在当前进程内真实执行一张已保存图，并写入租户 run_store / monitoring。

    `bot_runtime` 非空时：run 记录带 `agent_id`，执行注册表只装配该 Bot 的
    `adapter_ids`，结果带 `runtime_id`；A2A task 可据此串到具体 Bot。
    `task_id` 非空时：Task / RunRecord / root span attrs 三处同键，形成
    task→bot→run→span 的可观测关联链（T7）。
    """
    services = tenant_registry.get(tenant_id)
    raw = _load_graph(services, graph_id, release_version)
    _assert_non_interactive(services, raw)

    run_id = run_id or f"a2a-{uuid.uuid4().hex}"
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()
    agent_id = bot_runtime.agent_id if bot_runtime is not None else ""
    runtime_id = bot_runtime.runtime_id if bot_runtime is not None else ""
    graph_version = str(release_version) if release_version is not None else None
    tracer = Tracer(graph_id=graph_id, graph_version=graph_version)
    tracer.root.attrs.update(
        {
            "taskId": task_id,
            "agentId": agent_id,
            "runtimeId": runtime_id,
            "runId": run_id,
        }
    )
    graph_view = {
        "nodes": [
            {"id": node.get("id"), "type": node.get("type", "unknown")}
            for node in raw.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        ]
    }

    def _record(status: Literal["completed", "error", "cancelled"], *, outputs: dict[str, Any], nodes: list, error: str | None, spans: dict | None) -> None:
        services.monitoring.record_run(
            graph_id=graph_id,
            mode="a2a",
            status=status,
            started_at=started_at,
            duration_ms=(time.time() - started) * 1000,
            nodes=nodes,
            error=error,
            trace_id=tracer.trace_id,
            resolved_version=release_version,
            tool_calls=[],
            spans=spans,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            runtime_id=runtime_id,
        )

    services.run_store.begin(
        run_id=run_id,
        graph_id=graph_id,
        mode="a2a",
        agent_id=agent_id,
    )
    try:
        demo_surface = demo_surface_enabled()
        base = build_base_registry(demo_surface, resolve_database_client(demo_surface))
        registry = build_runtime_registry(services, base, secret_provider=None)
        if bot_runtime is not None:
            registry = _filter_registry(registry, bot_runtime.adapter_ids)
        result = run_graph(
            parse_graph(raw),
            inputs=inputs or {},
            registry=registry,
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=graph_id,
            graph_version=graph_version,
            tenant_id=tenant_id,
            memory_repo=services.memory_store,
            tracer=tracer,
        )
    except Exception as exc:  # noqa: BLE001 - run 失败必须落运行台账，不向调用方抛栈
        message = f"{type(exc).__name__}: {exc}"[:500]
        services.run_store.finish(run_id=run_id, status="failed", error=message)
        _record(
            "error",
            outputs={},
            nodes=[],
            error=message,
            spans=tracer.finish("error"),
        )
        return GraphRunResult(
            run_id=run_id,
            graph_id=graph_id,
            status="failed",
            outputs={},
            trace=[],
            error=message,
            wall_seconds=round(time.time() - started, 3),
            agent_id=agent_id,
            runtime_id=runtime_id,
            trace_id=tracer.trace_id,
        )

    status = result.get("status")
    outputs = result.get("outputs") or {}
    trace = result.get("trace") or []
    if not isinstance(outputs, dict):
        outputs = {"outputs": outputs}
    if not isinstance(trace, list):
        trace = []
    spans = result.get("traceTree") or tracer.finish("ok" if status == "completed" else "error")
    nodes = extract_node_results(graph_view, outputs)
    if status == "completed":
        services.run_store.finish(
            run_id=run_id, status="completed", outputs=outputs, trace=trace
        )
        _record("completed", outputs=outputs, nodes=nodes, error=None, spans=spans)
        task_status = "completed"
        error = None
    elif status == "failed":
        # 图级 globalErrorHandler 的 mark_only/route_to_subgraph 会以 failed 返回，
        # 而不是抛异常。必须保留 outputs/trace/global_error，不能误报成“unknown status”。
        global_error = result.get("global_error")
        if isinstance(global_error, dict):
            code = str(global_error.get("code") or "RUN_FAILED")
            detail = str(global_error.get("detail") or "")
            error = f"{code}: {detail}"[:500] if detail else code[:500]
        else:
            error = "run failed"
        services.run_store.finish(
            run_id=run_id, status="failed", outputs=outputs, trace=trace, error=error
        )
        _record("error", outputs=outputs, nodes=nodes, error=error, spans=spans)
        task_status = "failed"
    elif status == "suspended":
        # A2A v1 不接管挂起/续跑；子图内审批等交互节点可能在运行中才暴露。
        # A2A 没有续跑路径，挂起图在此终止；台账记 interrupted，而非 finish 不接受的非终态 suspended。
        error = "run suspended: A2A v1 has no resume path; resolve in the platform session"
        services.run_store.finish(
            run_id=run_id,
            status="interrupted",
            outputs=outputs,
            trace=trace,
            error=error,
        )
        _record("error", outputs=outputs, nodes=nodes, error=error, spans=spans)
        task_status = "input-required"
    else:
        message = f"unexpected run status: {status!r}"[:500]
        services.run_store.finish(run_id=run_id, status="failed", error=message)
        _record("error", outputs=outputs, nodes=nodes, error=message, spans=spans)
        task_status = "failed"
        error = message
    return GraphRunResult(
        run_id=run_id,
        graph_id=graph_id,
        status=task_status,
        outputs=outputs,
        trace=trace,
        error=error,
        wall_seconds=round(time.time() - started, 3),
        agent_id=agent_id,
        runtime_id=runtime_id,
        trace_id=tracer.trace_id,
    )

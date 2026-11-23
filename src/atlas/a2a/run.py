"""A2A 真实 run 执行器（docs/90 A-02）。

最小闭环只接**非交互图**：图内出现 `human_approval` / `wait`（含子图）时在启动前
拒绝，避免 A2A 请求线程被永久挂起。真实 run 记录写入租户 `run_store`，与平台
`/api/runs` 共用同一运行台账；v1 不提供 A2A 续跑/取消，交互图仍走平台会话。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.harness.runtime import build_base_registry, build_runtime_registry, resolve_database_client
from atlas.iam.deps import tenant_registry
from atlas.security.bootstrap import demo_surface_enabled

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
    nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
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
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
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
) -> GraphRunResult:
    """在当前进程内真实执行一张已保存图，并写入租户 run_store。"""
    services = tenant_registry.get(tenant_id)
    raw = _load_graph(services, graph_id, release_version)
    _assert_non_interactive(services, raw)

    run_id = run_id or f"a2a-{uuid.uuid4().hex}"
    started = time.time()
    services.run_store.begin(run_id=run_id, graph_id=graph_id, mode="a2a")
    try:
        demo_surface = demo_surface_enabled()
        base = build_base_registry(demo_surface, resolve_database_client(demo_surface))
        registry = build_runtime_registry(services, base, secret_provider=None)
        result = run_graph(
            parse_graph(raw),
            inputs=inputs or {},
            registry=registry,
            approval_broker=services.approval_broker,
            event_wait_broker=services.event_wait_broker,
            graph_id=graph_id,
            graph_version=str(release_version) if release_version is not None else None,
            tenant_id=tenant_id,
            memory_repo=services.memory_store,
        )
    except Exception as exc:  # noqa: BLE001 - run 失败必须落运行台账，不向调用方抛栈
        message = f"{type(exc).__name__}: {exc}"[:500]
        services.run_store.finish(run_id=run_id, status="failed", error=message)
        return GraphRunResult(
            run_id=run_id,
            graph_id=graph_id,
            status="failed",
            outputs={},
            trace=[],
            error=message,
            wall_seconds=round(time.time() - started, 3),
        )

    status = result.get("status")
    outputs = result.get("outputs") or {}
    trace = result.get("trace") or []
    if not isinstance(outputs, dict):
        outputs = {"outputs": outputs}
    if not isinstance(trace, list):
        trace = []
    if status == "completed":
        services.run_store.finish(
            run_id=run_id, status="completed", outputs=outputs, trace=trace
        )
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
        task_status = "failed"
    elif status == "suspended":
        # A2A v1 不接管挂起/续跑；子图内审批等交互节点可能在运行中才暴露。
        services.run_store.finish(run_id=run_id, status="suspended")
        task_status = "input-required"
        error = "run suspended: A2A v1 has no resume path; resolve in the platform session"
    else:
        message = f"unexpected run status: {status!r}"[:500]
        services.run_store.finish(run_id=run_id, status="failed", error=message)
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
    )

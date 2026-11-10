"""Background pinned-version run worker, extracted from api/main.py (batch 2).

`run_background_cycle` / `run_background_worker` carry the full existing
semantics of webhook / inbound / schedule triggered runs (pinned-version load,
run begin/finish accounting, superseded/timeout suspension, failure recording,
cycle-session driving) verbatim. Every dependency that main.py owns as
module-level state — and that tests monkeypatch through ``atlas.api.main`` —
arrives via the :class:`BackgroundRunDeps` bundle instead of a global import:
main's thin wrappers read the current module globals when building the bundle,
so patching ``atlas.api.main.run_graph`` etc. keeps working.

The wrapper signature (six positional parameters + ``mode``/``run_id``
keywords) is the `WorkerFn` contract consumed by
:mod:`atlas.services.webhook_runtime` and the scheduler runtime — keep main's
wrappers shape-compatible.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from atlas.cycles.supervisor import CycleOutcome, cycle_config, run_cycle_session
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend
from atlas.iam.registry import TenantServices
from atlas.llm.metering import usage_context
from atlas.routing import TriggerEvent
from atlas.services.error_safety import safe_run_error as _safe_run_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackgroundRunDeps:
    """Process-level dependencies main.py injects per call (patch seams preserved).

    Same bundle discipline as :class:`atlas.services.run_orchestration.SyncRunDeps`:
    main builds it from its current module globals on every call, so test
    monkeypatches on ``atlas.api.main`` keep taking effect.

    - ``run_graph``: the graph executor; tests patch ``atlas.api.main.run_graph``.
    - ``frame_sink_for``: frame sink factory; tests patch ``_frame_sink_for``.
    - ``resume_claim_for``: claim-check factory for suspended frames.
    - ``runtime_registry_of`` / ``tenant_graph_resolver_of``: per-tenant adapters
      (main's ``_runtime_registry`` / ``_tenant_graph_resolver``).
    - ``secret_provider`` / ``storage_backend``: process singletons owned by
      main, read at call time.
    """

    run_graph: Callable[..., Any]
    frame_sink_for: Callable[..., Any]
    resume_claim_for: Callable[[], Callable[[str], bool] | None]
    runtime_registry_of: Callable[[TenantServices], Any]
    tenant_graph_resolver_of: Callable[[TenantServices], Any]
    secret_provider: Any
    storage_backend: str


def run_background_cycle(
    services: TenantServices, tenant_id: str, graph_id: str, version: int,
    event: TriggerEvent, *, mode: str, run_id: str | None,
    cycle_seq: int = 0, cycle_group_id: str = "", inputs: dict[str, Any] | None = None,
    agent_id: str = "", deps: BackgroundRunDeps,
) -> CycleOutcome:
    """后台跑**一圈**（= 一次真实运行）；返回 `CycleOutcome` 供圈层判定。

    与同步入口同构的分工：圈层只管"跑几次"，一圈内部的 begin/finish/让位/失败记账
    语义逐字保持（docs/130 C-D1）。`cycle_seq`／`cycle_group_id` 缺省 0/''＝非圈运行。
    """
    run_store = services.run_store
    own_run_id = run_id
    if own_run_id is None:
        own_run_id = uuid.uuid4().hex
        run_store.begin(
            run_id=own_run_id, graph_id=graph_id, mode=mode,
            cycle_seq=cycle_seq, cycle_group_id=cycle_group_id,
            agent_id=agent_id,
        )
    try:
        raw = services.graph_store.get(graph_id, version)
        if raw is None:
            run_store.finish(
                run_id=own_run_id, status="failed",
                error=f"已发布版本不存在：{graph_id}@{version}",
            )
            return CycleOutcome(run_id=own_run_id, status="failed", stop_run=True)
        graph = parse_graph(raw)
        with usage_context(
            tenant_id, run_id=own_run_id, graph_id=graph_id, store=services.usage_store
        ):
            result = deps.run_graph(
                graph,
                inputs=inputs if inputs is not None else {"event": event.model_dump()},
                registry=deps.runtime_registry_of(services),
                approval_broker=services.approval_broker,
                event_wait_broker=services.event_wait_broker,
                graph_id=graph_id,
                graph_resolver=deps.tenant_graph_resolver_of(services),
                frame_sink=deps.frame_sink_for(tenant_id, run_store, own_run_id),
                resume_claim=deps.resume_claim_for(),
                _block_subgraph_suspend=(deps.storage_backend == "pg"),
                graph_version=version,
                tenant_id=tenant_id,
                secret_provider=deps.secret_provider,
                memory_repo=services.memory_store,
            )
        run_store.finish(
            run_id=own_run_id, status="completed",
            outputs=result["outputs"], trace=result["trace"],
        )
        return CycleOutcome(
            run_id=own_run_id, status="completed", outputs=result.get("outputs", {})
        )
    except RunSuperseded as exc:
        # docs/62 §2 D-4：后台运行同样受认领门约束——输家不落 failed
        # （否则把赢家的执行记录成失败），只让位并留日志。
        logger.info("后台运行让位 graph=%s@%s：%s", graph_id, version, exc)
        return CycleOutcome(run_id=own_run_id, status="suspended", stop_run=True)
    except WaitTimeoutSuspend as exc:
        # docs/136 打包 BS：后台运行挂起节点超时且 onTimeout="suspend"——run 保持
        # suspended（不 finish、不清帧），等人工后续恢复。
        logger.info("后台运行超时保持挂起 graph=%s@%s：%s", graph_id, version, exc)
        return CycleOutcome(run_id=own_run_id, status="suspended", stop_run=True)
    except Exception as exc:
        logger.error("%s 触发图运行失败 graph=%s@%s", mode, graph_id, version, exc_info=True)
        run_store.finish(
            run_id=own_run_id, status="failed",
            error=_safe_run_error(exc),
        )
        return CycleOutcome(run_id=own_run_id, status="failed", stop_run=True)


def run_background_worker(
    services: TenantServices, tenant_id: str,
    graph_id: str, version: int, event: TriggerEvent,
    agent_id: str = "", *, mode: str = "webhook", run_id: str | None = None,
    deps: BackgroundRunDeps,
) -> None:
    """后台运行钉版图：run 记录独立于 HTTP 请求；失败落 failed，不回传触发方。

    webhook 与定时触发共用这条路径（docs/68 §1 D-3：派发按发布号钉版，绝不回落草稿）。
    `run_id` 由调度派发方预先 begin 后传入——它必须在 tick 内就让 run 变成 running，
    否则下一轮 tick 的"同图还在跑"判定会漏看这条刚提交的运行。

    **圈启用面**（docs/130 C-D10）：图级 `loop` 配置存在 ⇒ 本 worker 按圈会话驱动；
    预置的 `run_id` 只用于**第一圈**（保持"tick 内即可见 running"的既有语义），
    后续圈由圈层发新 run_id。缺省配置走原路径逐字不变。
    """
    raw = services.graph_store.get(graph_id, version)
    if raw is None:
        # 图不存在：预置 run 要落 failed（与既有语义同）；无预置 run 则无事可做。
        if run_id is not None:
            services.run_store.finish(
                run_id=run_id, status="failed",
                error=f"已发布版本不存在：{graph_id}@{version}",
            )
        return
    try:
        graph = parse_graph(raw)
    except Exception as exc:
        if run_id is not None:
            services.run_store.finish(
                run_id=run_id, status="failed", error=_safe_run_error(exc)
            )
        logger.error("%s 图解析失败 graph=%s@%s：%s", mode, graph_id, version, exc)
        return

    if cycle_config(graph) is None:
        run_background_cycle(
            services, tenant_id, graph_id, version, event,
            mode=mode, run_id=run_id, agent_id=agent_id, deps=deps,
        )
        return

    def _run_once(step) -> CycleOutcome:
        return run_background_cycle(
            services, tenant_id, graph_id, version, event,
            mode=mode,
            # 只有第一圈沿用派发方预置的 run_id（tick 内可见 running）
            run_id=run_id if step.seq == 1 else None,
            cycle_seq=step.seq, cycle_group_id=step.group_id,
            inputs=step.inputs, agent_id=agent_id, deps=deps,
        )

    run_cycle_session(
        services, graph,
        tenant_id=tenant_id, graph_id=graph_id,
        inputs={"event": event.model_dump()},
        run_once=_run_once,
    )

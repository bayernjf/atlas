# -*- coding: utf-8 -*-
"""Scheduler runtime extracted from ``atlas.api.main`` (third batch).

Owns the lazy global ``ScheduleStore``, the tick loop, dispatch/claim/busy
checks and publish-time schedule reconciliation. Routes stay in
``atlas.api.main``; backend switches and the worker pool are passed in at
call time so ``main`` remains the single source of truth for
``STORAGE_BACKEND`` and tests that monkeypatch it keep working.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from atlas.iam.deps import tenant_registry
from atlas.iam.registry import TenantServices
from atlas.llm.metering import usage_context
from atlas.reflection import run_pass as run_reflection_pass
from atlas.routing import TriggerEvent
from atlas.scheduling.engine import ACTION_SKIPPED_OVERLAP, TickLedger, TickOutcome, tick
from atlas.scheduling.models import REFLECT_SCHEDULE_ID, ScheduleRecord, slot_key
from atlas.scheduling.pg_store import PgScheduleStore
from atlas.scheduling.store import InMemoryScheduleStore, ScheduleStore
from atlas.services.schedule_pure import (
    schedule_enabled,
    schedule_specs_of_published,
    schedule_tick_seconds,
)

logger = logging.getLogger(__name__)

BUSY_SCAN_LIMIT = 200

_schedule_store: ScheduleStore | None = None
_schedule_store_lock = threading.Lock()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None

PgEngine = Callable[[], Any]
ServicesFor = Callable[[str], Any]


def schedule_store(*, storage_backend: str, pg_engine: PgEngine) -> ScheduleStore:
    """全局调度 store（惰性）：不按租户装配的理由见 `scheduling/store.py` 模块头。"""
    global _schedule_store
    with _schedule_store_lock:
        if _schedule_store is None:
            _schedule_store = (
                PgScheduleStore(pg_engine())
                if storage_backend == "pg"
                else InMemoryScheduleStore()
            )
        return _schedule_store


def start_pinned_run(
    services: TenantServices,
    tenant_id: str,
    graph_id: str,
    version: int,
    payload: dict,
    *,
    pool: Any,
    worker: Callable[..., Any],
) -> str:
    """按钉版起一次后台运行，返回 runId。

    `begin` 在这里**同步**做完再交给线程池：调度 tick 必须立刻看得见这条 running，
    否则同一张图会在下一轮（30 秒后）被"同图还在跑"判定放过去。
    """
    run_id = uuid.uuid4().hex
    services.run_store.begin(run_id=run_id, graph_id=graph_id, mode="schedule")
    event = TriggerEvent(channel="api", payload=payload)
    pool.submit(
        worker,
        services, tenant_id, graph_id, version, event,
        mode="schedule", run_id=run_id,
    )
    return run_id


def schedule_claim(
    record: ScheduleRecord, slot: datetime, *, storage_backend: str, pg_engine: PgEngine
) -> bool:
    return schedule_store(storage_backend=storage_backend, pg_engine=pg_engine).claim(
        record.tenant_id, record.graph_id, record.schedule_id, slot, record.action
    )


def schedule_is_busy(
    record: ScheduleRecord, *, services_for: ServicesFor | None = None
) -> bool:
    """同图是否还有 running/suspended 运行（docs/68 §1 D-6，保守按图粒度判）。

    反思项**不受此限**：它是一次只读计算（读既有投影、不写图、不产运行），与在途运行
    不争用任何东西；反过来拿"图在跑"挡住反思，会让繁忙的图永远得不到反思（docs/88 §3 P-4）。
    """
    services_for = services_for or tenant_registry.get
    if record.action == "reflect":
        return False
    services = services_for(record.tenant_id)
    for status in ("running", "suspended"):
        for run in services.run_store.list(status=status, limit=BUSY_SCAN_LIMIT):
            if run.get("graphId") == record.graph_id:
                return True
    return False


def schedule_dispatch(
    record: ScheduleRecord,
    slot: datetime,
    *,
    storage_backend: str,
    pg_engine: PgEngine,
    pool: Any,
    worker: Callable[..., Any],
    services_for: ServicesFor | None = None,
) -> None:
    """派发一次定时动作；跑图的事件载荷带槽位，图里可用 `{{global.event.payload.slot}}` 引用。

    `reflect` 项共用调度载体，只共用**槽位与认领**：它不写 run_store、不发 TriggerEvent
    ——反思不是"跑一次图"，把它塞进运行态会让监控里多出一批没有业务含义的 run（docs/88 §3 P-4）。
    """
    services_for = services_for or tenant_registry.get
    services = services_for(record.tenant_id)
    key = slot_key(slot)
    store = schedule_store(storage_backend=storage_backend, pg_engine=pg_engine)
    if record.action == "reflect":
        # docs/129 打包 AU：反思摘要器的 LLM 调用也计用量（caller=reflection_summarize）。
        with usage_context(record.tenant_id, store=services.usage_store):
            report = run_reflection_pass(
                services, services.reflection_store,
                tenant_id=record.tenant_id, graph_id=record.graph_id,
                base_version=record.version,
            )
        store.note_fired(
            record.tenant_id, record.graph_id, record.schedule_id, slot
        )
        services.audit_store.record(
            tenant_id=record.tenant_id, actor="scheduler", action="schedule.fire",
            status_code=200,
            path=(f"action=reflect graph={record.graph_id}@{record.version} "
                  f"slot={key} status={report.status}"),
            ip="",
        )
        return
    start_pinned_run(
        services, record.tenant_id, record.graph_id, record.version,
        {"source": "schedule", "slot": key, "cron": record.cron},
        pool=pool, worker=worker,
    )
    store.note_fired(
        record.tenant_id, record.graph_id, record.schedule_id, slot
    )
    services.audit_store.record(
        tenant_id=record.tenant_id, actor="scheduler", action="schedule.fire",
        status_code=200, path=f"graph={record.graph_id}@{record.version} slot={key}", ip="",
    )


def run_schedule_tick(
    ledger: TickLedger | None = None,
    *,
    storage_backend: str,
    pg_engine: PgEngine,
    pool: Any,
    worker: Callable[..., Any],
    services_for: ServicesFor | None = None,
) -> list[TickOutcome]:
    """跑一轮调度评估：引擎只判定，写库与跳过计数都在这里（`scheduling/engine.py` 零 IO）。"""
    services_for = services_for or tenant_registry.get
    store = schedule_store(storage_backend=storage_backend, pg_engine=pg_engine)
    outcomes = tick(
        datetime.now(timezone.utc),
        store.list_all(),
        lambda record, slot: schedule_claim(
            record, slot, storage_backend=storage_backend, pg_engine=pg_engine
        ),
        lambda record, slot: schedule_dispatch(
            record,
            slot,
            storage_backend=storage_backend,
            pg_engine=pg_engine,
            pool=pool,
            worker=worker,
            services_for=services_for,
        ),
        busy=lambda record: schedule_is_busy(record, services_for=services_for),
        ledger=ledger,
    )
    for outcome in outcomes:
        if outcome.action == ACTION_SKIPPED_OVERLAP:
            store.note_skipped(
                outcome.tenant_id, outcome.graph_id, outcome.schedule_id, outcome.slot_utc
            )
    return outcomes


def _scheduler_loop(
    *,
    storage_backend: str,
    pg_engine: PgEngine,
    pool: Any,
    worker: Callable[..., Any],
    services_for: ServicesFor | None = None,
) -> None:
    services_for = services_for or tenant_registry.get
    ledger = TickLedger()
    interval = schedule_tick_seconds()
    while not _scheduler_stop.is_set():
        try:
            run_schedule_tick(
                ledger,
                storage_backend=storage_backend,
                pg_engine=pg_engine,
                pool=pool,
                worker=worker,
                services_for=services_for,
            )
        except Exception:  # noqa: BLE001 — 一轮调度的异常不能带走进程
            logger.exception("调度 tick 异常，下一轮继续")
        _scheduler_stop.wait(interval)


def start_scheduler(
    *,
    storage_backend: str,
    pg_engine: PgEngine,
    pool: Any,
    worker: Callable[..., Any],
    services_for: ServicesFor | None = None,
) -> None:
    """在 lifespan 里、`recover_pending()` 之后起线程（docs/68 §2.3 的启动顺序）。"""
    services_for = services_for or tenant_registry.get
    global _scheduler_thread
    if _scheduler_thread is not None:
        return
    if not schedule_enabled():
        logger.info("定时触发调度器已由 ATLAS_SCHEDULE_ENABLED 关闭")
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=lambda: _scheduler_loop(
            storage_backend=storage_backend,
            pg_engine=pg_engine,
            pool=pool,
            worker=worker,
            services_for=services_for,
        ),
        name="atlas-scheduler",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info(
        "定时触发调度器已启动：每 %s 秒评估一轮（UTC 槽位、不补跑；单副本约束不变）",
        schedule_tick_seconds(),
    )


def stop_scheduler(timeout_seconds: float = 2.0) -> None:
    global _scheduler_thread
    if _scheduler_thread is None:
        return
    _scheduler_stop.set()
    _scheduler_thread.join(timeout=timeout_seconds)
    if _scheduler_thread.is_alive():
        # 宁可留着这个引用：清成 None 会让下一次 start_scheduler 再起**第二条** tick 循环。
        # 两条循环不会双发（派发权在认领表），但会双份扫库与双份日志，排查时说不清是谁。
        logger.warning("调度线程未在 %ss 内退出，保留引用避免起第二条", timeout_seconds)
        return
    _scheduler_thread = None


def derive_schedule_on_publish(
    services: TenantServices,
    tenant_id: str,
    graph_id: str,
    version: int,
    *,
    storage_backend: str,
    pg_engine: PgEngine,
) -> None:
    """发布出口：按图内定时节点**全量 reconcile** 调度注册——发布是版本变化的唯一时刻。

    （打包 ZN，D41 ③④）每个定时触发节点派生一条 `run`（schedule_id＝节点 id，各自
    cron/tz/catch_up/overlap_policy）；另派生**一条**图级 `reflect`（`__reflect__`，反思是
    整图 pass，不随节点倍增，节奏跟第一个定时节点，overlap 固定 skip）。本次派生集合之外、
    库里本图仍存在的 schedule_id（节点被删）一律撤销——替代旧「只取第一个、其余 warning」。
    """
    raw = services.graph_store.get(graph_id, version)
    if raw is None:
        return
    specs = schedule_specs_of_published(raw)
    store = schedule_store(storage_backend=storage_backend, pg_engine=pg_engine)
    if not specs:
        store.remove(tenant_id, graph_id)
        return
    desired: set[str] = set()
    for schedule_id, cron, tz_name, catch_up, overlap in specs:
        desired.add(schedule_id)
        store.upsert_published(
            tenant_id=tenant_id, graph_id=graph_id, schedule_id=schedule_id,
            version=version, cron=cron, action="run", timezone=tz_name,
            catch_up_minutes=catch_up, overlap_policy=overlap,
        )
        logger.info(
            "定时跑图调度已登记：graph=%s schedule=%s@%s（cron=%s，tz=%s，catch_up=%s，overlap=%s）",
            graph_id, schedule_id, version, cron, tz_name, catch_up, overlap,
        )
    # 图级反思一条：节奏（cron/tz/catch_up）跟第一个定时节点。
    _, cron0, tz0, catch0, _ = specs[0]
    desired.add(REFLECT_SCHEDULE_ID)
    store.upsert_published(
        tenant_id=tenant_id, graph_id=graph_id, schedule_id=REFLECT_SCHEDULE_ID,
        version=version, cron=cron0, action="reflect", timezone=tz0,
        catch_up_minutes=catch0, overlap_policy="skip",
    )
    logger.info(
        "定时反思调度已登记：graph=%s schedule=%s@%s（cron=%s，tz=%s）",
        graph_id, REFLECT_SCHEDULE_ID, version, cron0, tz0,
    )
    # reconcile：撤销本次不再存在的调度（删掉的定时节点）。
    existing = {
        row.schedule_id
        for row in store.list_tenant(tenant_id) if row.graph_id == graph_id
    }
    for stale in sorted(existing - desired):
        store.remove_schedule(tenant_id, graph_id, stale)
        logger.info("定时调度已撤销：graph=%s schedule=%s", graph_id, stale)


def first_run_schedule(
    tenant_id: str,
    graph_id: str,
    *,
    storage_backend: str,
    pg_engine: PgEngine,
) -> ScheduleRecord | None:
    """该图第一条 run 调度（run-now 缺省目标）；reflect 不计入。"""
    for record in schedule_store(storage_backend=storage_backend, pg_engine=pg_engine).list_tenant(
        tenant_id
    ):
        if record.graph_id == graph_id and record.action == "run":
            return record
    return None

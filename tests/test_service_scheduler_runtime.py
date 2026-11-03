# -*- coding: utf-8 -*-
"""Third-batch split: scheduler runtime block equivalence.

The scheduler store/dispatch/tick assembly moves to
``atlas.services.scheduler_runtime``; ``atlas.api.main`` keeps the names
used by routers and existing tests.
"""

from __future__ import annotations

from types import SimpleNamespace

from atlas.api import main


def test_scheduler_runtime_module_owns_scheduler_helpers() -> None:
    from atlas.services import scheduler_runtime

    for name in (
        "schedule_store",
        "start_pinned_run",
        "schedule_claim",
        "schedule_is_busy",
        "schedule_dispatch",
        "run_schedule_tick",
        "start_scheduler",
        "stop_scheduler",
        "derive_schedule_on_publish",
        "first_run_schedule",
    ):
        assert hasattr(scheduler_runtime, name), name


def test_run_schedule_tick_records_skipped_overlap(monkeypatch) -> None:
    from atlas.scheduling.engine import ACTION_SKIPPED_OVERLAP
    from atlas.services import scheduler_runtime

    notes: list[tuple[object, ...]] = []

    class _Store:
        def list_all(self):
            return []

        def note_skipped(self, *args) -> None:
            notes.append(args)

    outcome = SimpleNamespace(
        action=ACTION_SKIPPED_OVERLAP,
        tenant_id="t1",
        graph_id="g1",
        schedule_id="s1",
        slot_utc="2026-11-03T00:00:00Z",
    )
    monkeypatch.setattr(scheduler_runtime, "schedule_store", lambda **kwargs: _Store())
    monkeypatch.setattr(
        scheduler_runtime,
        "tick",
        lambda now, records, claim, dispatch, busy, ledger: [outcome],
    )
    outcomes = main.run_schedule_tick()
    assert outcomes == [outcome]
    assert notes == [("t1", "g1", "s1", "2026-11-03T00:00:00Z")]


def test_schedule_is_busy_ignores_reflect(monkeypatch) -> None:
    from atlas.services import scheduler_runtime

    touched: list[str] = []
    monkeypatch.setattr(
        scheduler_runtime, "tenant_registry", SimpleNamespace(get=lambda tenant_id: touched.append(tenant_id))
    )
    record = SimpleNamespace(action="reflect", tenant_id="t1", graph_id="g1")
    assert main._schedule_is_busy(record) is False
    assert touched == []


def test_start_scheduler_respects_disabled_switch(monkeypatch) -> None:
    from atlas.services import scheduler_runtime

    monkeypatch.setattr(scheduler_runtime, "_scheduler_thread", None)
    monkeypatch.setattr(scheduler_runtime, "schedule_enabled", lambda: False)
    main.start_scheduler()
    assert scheduler_runtime._scheduler_thread is None


def test_stop_scheduler_without_thread_is_noop(monkeypatch) -> None:
    from atlas.services import scheduler_runtime

    monkeypatch.setattr(scheduler_runtime, "_scheduler_thread", None)
    main.stop_scheduler()
    assert scheduler_runtime._scheduler_thread is None

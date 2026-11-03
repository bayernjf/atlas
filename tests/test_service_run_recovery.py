# -*- coding: utf-8 -*-
"""Third-batch split: run recovery block equivalence.

These checks pin the extraction contract before/after moving the recovery
helpers out of ``atlas.api.main`` into ``atlas.services.run_recovery``:
the module must own the logic, while ``main`` keeps the same names so
existing tests and routers keep their import paths.
"""

from __future__ import annotations

from types import SimpleNamespace

from atlas.api import main


def test_run_recovery_module_owns_recovery_helpers() -> None:
    from atlas.services import run_recovery

    for name in (
        "recover_pending",
        "resume_from_frame",
        "resume_run",
        "frame_sink_for",
        "resume_claim_for",
    ):
        assert hasattr(run_recovery, name), name


def test_recover_pending_is_noop_outside_pg(monkeypatch) -> None:
    from atlas.services import run_recovery

    scanned: list[object] = []
    monkeypatch.setattr(main, "STORAGE_BACKEND", "memory")
    monkeypatch.setattr(
        run_recovery, "load_pending_frames", lambda engine: scanned.append(engine) or []
    )
    main.recover_pending()
    assert scanned == []


def test_resume_from_frame_skips_already_claimed_frame(monkeypatch) -> None:
    submitted: list[tuple[object, ...]] = []
    monkeypatch.setattr(main.tenant_registry, "get", lambda tenant_id: SimpleNamespace())
    monkeypatch.setattr(
        main._BACKGROUND_WORKER_POOL,
        "submit",
        lambda fn, *args: submitted.append((fn, *args)),
    )
    frame = {
        "tenant_id": "t1",
        "resume_token": "tok-claimed",
        "resumed_at": "2026-11-03T00:00:00Z",
        "kind": "approval",
    }
    main._resume_from_frame(object(), frame)
    assert submitted == [], "claimed frames must not restart a resume thread"


def test_frame_sink_marks_suspended_then_writes_pg_frame(monkeypatch) -> None:
    from atlas.services import run_recovery

    calls: list[tuple[object, ...]] = []

    class _RunStore:
        def suspend(self, **kwargs) -> None:
            calls.append(("suspend", kwargs))

    monkeypatch.setattr(main, "STORAGE_BACKEND", "pg")
    monkeypatch.setattr(main, "get_pg_backend", lambda: SimpleNamespace(engine="ENGINE"))
    monkeypatch.setattr(
        run_recovery,
        "make_frame_sink",
        lambda engine, tenant_id, run_id: (
            lambda frame: calls.append(("frame", engine, tenant_id, run_id, frame))
        ),
    )
    frame = {"node_id": "n1", "kind": "approval", "resume_token": "tok", "deadline_at": None}
    sink = main._frame_sink_for("t1", _RunStore(), "r1")
    sink(frame)
    assert calls[0] == (
        "suspend",
        {
            "run_id": "r1",
            "node_id": "n1",
            "kind": "approval",
            "resume_token": "tok",
            "deadline_at": None,
        },
    )
    assert calls[1] == ("frame", "ENGINE", "t1", "r1", frame)


def test_resume_claim_for_matches_storage_backend(monkeypatch) -> None:
    from atlas.services import run_recovery

    monkeypatch.setattr(main, "STORAGE_BACKEND", "memory")
    assert main._resume_claim_for() is None

    monkeypatch.setattr(main, "STORAGE_BACKEND", "pg")
    monkeypatch.setattr(main, "get_pg_backend", lambda: SimpleNamespace(engine="ENGINE"))
    monkeypatch.setattr(
        run_recovery, "make_resume_claim", lambda engine: (lambda token: engine == "ENGINE")
    )
    claim = main._resume_claim_for()
    assert claim is not None and claim("tok") is True

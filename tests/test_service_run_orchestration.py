# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.run_orchestration.

不启 TestClient：用 fake services 与注入的 fake run_graph 直调
`run_sync_cycle` / `run_saved_graph`，钉住一圈内部的记账语义
（begin/finish、监控埋点、灰度门控调用、让位/取消/失败三条早退路径）。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from atlas.collaboration.cancellations import RunCancelled
from atlas.graph.loader import RunSuperseded
from atlas.services import run_orchestration
from atlas.services.run_orchestration import SyncRunDeps


def _graph(*node_ids):
    return SimpleNamespace(nodes=[SimpleNamespace(id=nid, type="wait") for nid in node_ids])


def _principal(tenant="t1"):
    return SimpleNamespace(tenant_id=tenant)


class _RunStore:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def begin(self, **kwargs):
        self.calls.append(("begin", kwargs))

    def finish(self, **kwargs):
        self.calls.append(("finish", kwargs))


class _Monitoring:
    def __init__(self):
        self.records: list[dict] = []

    def record_run(self, **kwargs):
        self.records.append(kwargs)
        return SimpleNamespace(**kwargs)


class _Broker:
    def __init__(self):
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    def register(self, run_id):
        self.registered.append(run_id)
        return SimpleNamespace(is_set=False)

    def unregister(self, run_id):
        self.unregistered.append(run_id)


_VALID_GRAPH_RAW = {
    "version": 1,
    "variables": [],
    "nodes": [
        {"id": "trigger-1", "type": "trigger", "name": "触发",
         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
        {"id": "tool-1", "type": "tool_call", "name": "动作",
         "config": {"tool": "op-do"}},
    ],
    "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
}


def _services():
    return SimpleNamespace(
        graph_store=SimpleNamespace(get=lambda gid, rv: _VALID_GRAPH_RAW),
        routing_store=SimpleNamespace(resolve=lambda *a, **k: (None, None)),
        run_store=_RunStore(),
        monitoring=_Monitoring(),
        cancellation_broker=_Broker(),
        approval_broker=SimpleNamespace(),
        event_wait_broker=SimpleNamespace(),
        usage_store=None,
        message_service=SimpleNamespace(),
        memory_store=None,
    )


@contextmanager
def _fake_usage_context(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _patch_usage_context(monkeypatch):
    monkeypatch.setattr(run_orchestration, "usage_context", _fake_usage_context)


def _deps(services, run_graph, evaluate_calls):
    return SyncRunDeps(
        services_for=lambda principal: services,
        run_graph=run_graph,
        frame_sink_for=lambda tenant_id, store, run_id: None,
        resume_claim_for=lambda: None,
        evaluate_after_run=lambda svc, record: evaluate_calls.append(record),
        runtime_registry_of=lambda services: None,
        tenant_graph_resolver_of=lambda services: None,
        secret_provider=None,
        storage_backend="memory",
        public_url="http://atlas.test",
    )


def test_run_sync_cycle_completed_records_and_yields_none_response() -> None:
    services = _services()
    evaluate_calls: list = []
    result_payload = {"outputs": {"o": 1}, "trace": ["t1"], "traceTree": {"root": True}}
    deps = _deps(services, lambda *a, **k: result_payload, evaluate_calls)

    payloads: dict[str, dict] = {}
    outcome, response = run_orchestration.run_sync_cycle(
        services, _principal(), _graph("n1"),
        graph_id="g1", release_version=2, resolved_version=2,
        event_payload={}, inputs={"x": 1}, cycle_seq=0, cycle_group_id="",
        payloads=payloads, deps=deps,
    )

    assert response is None
    assert outcome.status == "completed" and outcome.outputs == {"o": 1}
    # O-2：带 event 的路由版本落 run 记录；完成后 finish(completed) 且记录进 payloads
    assert services.run_store.calls[0][0] == "begin"
    assert services.run_store.calls[0][1]["resolved_version"] == 2
    assert services.run_store.calls[-1] == (
        "finish",
        {"run_id": outcome.run_id, "status": "completed",
         "outputs": {"o": 1}, "trace": ["t1"]},
    )
    assert payloads[outcome.run_id] is result_payload
    # M9：正常完成同样进灰度门控评估
    assert len(evaluate_calls) == 1
    completed = services.monitoring.records[-1]
    assert completed["status"] == "completed" and completed["mode"] == "sync"
    # 打包 O：取消句柄随请求退出注销
    assert services.cancellation_broker.unregistered == [outcome.run_id]


def test_run_sync_cycle_cancelled_skips_rollout_gate() -> None:
    services = _services()
    evaluate_calls: list = []

    def cancel_run(*args, **kwargs):
        raise RunCancelled("n1")

    deps = _deps(services, cancel_run, evaluate_calls)
    outcome, response = run_orchestration.run_sync_cycle(
        services, _principal(), _graph("n1"),
        graph_id="g1", release_version=None, resolved_version=None,
        event_payload={}, inputs=None, cycle_seq=0, cycle_group_id="",
        payloads={}, deps=deps,
    )

    assert outcome.status == "cancelled" and outcome.stop_run is True
    assert response is not None and response.status == "cancelled"
    assert services.run_store.calls[-1][1]["status"] == "cancelled"
    # 用户主动取消不算失败：不进 M9 灰度门控（与流式路径逐条对齐）
    assert evaluate_calls == []
    assert services.monitoring.records[-1]["status"] == "cancelled"


def test_run_sync_cycle_superseded_keeps_run_unfinished() -> None:
    services = _services()

    def supersede(*args, **kwargs):
        raise RunSuperseded("n1", "tok-1")

    deps = _deps(services, supersede, [])
    outcome, response = run_orchestration.run_sync_cycle(
        services, _principal(), _graph("n1"),
        graph_id="g1", release_version=None, resolved_version=None,
        event_payload={}, inputs=None, cycle_seq=0, cycle_group_id="",
        payloads={}, deps=deps,
    )

    assert outcome.status == "suspended" and outcome.stop_run is True
    assert response is not None and response.status == "suspended"
    assert "superseded by another process" in response.trace[0]
    # docs/62 §2 D-4：输家不写 run 终态、不记监控——begin 后没有 finish
    assert [name for name, _ in services.run_store.calls] == ["begin"]
    assert services.monitoring.records == []


def test_run_sync_cycle_failure_writes_failed_and_reraises() -> None:
    services = _services()
    evaluate_calls: list = []

    def boom(*args, **kwargs):
        raise RuntimeError("node exploded")

    deps = _deps(services, boom, evaluate_calls)
    with pytest.raises(RuntimeError, match="node exploded"):
        run_orchestration.run_sync_cycle(
            services, _principal(), _graph("n1"),
            graph_id="g1", release_version=None, resolved_version=None,
            event_payload={}, inputs=None, cycle_seq=0, cycle_group_id="",
            payloads={}, deps=deps,
        )

    assert services.run_store.calls[-1][1]["status"] == "failed"
    # safe_run_error 把原始异常泛化成固定文案（error_safety 约定）
    assert services.run_store.calls[-1][1]["error"] == "运行时发生未预期错误"
    error_record = services.monitoring.records[-1]
    assert error_record["status"] == "error" and error_record["error"] == "运行时发生未预期错误"
    # M9：异常运行同样计入 candidate 门控
    assert len(evaluate_calls) == 1


def test_run_saved_graph_rejects_debug_and_returns_last_cycle() -> None:
    services = _services()

    # debug 与同步运行互斥：422（与拆分前逐字一致）
    deps = _deps(services, lambda *a, **k: {}, [])
    with pytest.raises(HTTPException) as exc:
        run_orchestration.run_saved_graph(
            "g1", {"debug": {"breakpoints": []}}, _principal(), deps=deps
        )
    assert exc.value.status_code == 422

    # 非 cycle 图：直接跑一圈，返回该圈的 RunGraphResponse
    def ok_run(graph, *args, **kwargs):
        return {"status": "completed", "outputs": {"ok": True}, "trace": ["t"], "traceTree": {}, "rng_seed": 7}

    deps = _deps(services, ok_run, [])
    # event 版本路由由 graph_release.resolve_event_version 承担（另有单测）；
    # 这里走无 event 路径：releaseVersion=1 钉版。
    response = run_orchestration.run_saved_graph(
        "g1", {"releaseVersion": 1}, _principal(),
        deps=SyncRunDeps(
            services_for=deps.services_for,
            run_graph=deps.run_graph,
            frame_sink_for=deps.frame_sink_for,
            resume_claim_for=deps.resume_claim_for,
            evaluate_after_run=deps.evaluate_after_run,
            runtime_registry_of=deps.runtime_registry_of,
            tenant_graph_resolver_of=deps.tenant_graph_resolver_of,
            secret_provider=deps.secret_provider,
            storage_backend=deps.storage_backend,
            public_url=deps.public_url,
        ),
    )
    assert response.status == "completed"
    assert response.outputs == {"ok": True}
    assert response.rng_seed == 7

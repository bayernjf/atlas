# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.background_run.

不启 TestClient、不真跑 run_graph：用 fake services / 注入的 fake run_graph 直调
`run_background_cycle` / `run_background_worker`，钉住后台钉版运行一圈的记账语义
（begin/finish、让位/超时挂起/失败三条早退路径）与 worker 的图缺失/解析失败/
圈会话接线（第一圈沿用派发方预置 run_id）。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend
from atlas.services import background_run
from atlas.services.background_run import BackgroundRunDeps


class _RunStore:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def begin(self, **kwargs):
        self.calls.append(("begin", kwargs))

    def finish(self, **kwargs):
        self.calls.append(("finish", kwargs))


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


def _services(raw: dict | None = _VALID_GRAPH_RAW):
    return SimpleNamespace(
        graph_store=SimpleNamespace(get=lambda gid, rv: raw),
        run_store=_RunStore(),
        approval_broker=SimpleNamespace(),
        event_wait_broker=SimpleNamespace(),
        usage_store=None,
        memory_store=None,
    )


@contextmanager
def _fake_usage_context(*args, **kwargs):
    yield


@pytest.fixture(autouse=True)
def _patch_usage_context(monkeypatch):
    monkeypatch.setattr(background_run, "usage_context", _fake_usage_context)


def _deps(services, run_graph):
    return BackgroundRunDeps(
        run_graph=run_graph,
        frame_sink_for=lambda tenant_id, store, run_id: ("sink", tenant_id, run_id),
        resume_claim_for=lambda: None,
        runtime_registry_of=lambda services: ("registry", services),
        tenant_graph_resolver_of=lambda services: ("resolver", services),
        secret_provider="secret",
        storage_backend="memory",
    )


def _event():
    return SimpleNamespace(model_dump=lambda: {"event": "payload"})


# --------------------------------------------------------------------------- #
# run_background_cycle：一圈记账语义
# --------------------------------------------------------------------------- #
def test_cycle_completed_finishes_and_returns_outcome() -> None:
    services = _services()
    captured: dict = {}

    def run_graph(graph, **kwargs):
        captured.update(kwargs)
        return {"outputs": {"o": 1}, "trace": ["t1"]}

    outcome = background_run.run_background_cycle(
        services, "t1", "g1", 3, _event(),
        mode="webhook", run_id=None, deps=_deps(services, run_graph),
    )

    assert outcome.status == "completed" and outcome.outputs == {"o": 1}
    assert outcome.stop_run is False
    # 无预置 run_id：本圈自造 run_id 并 begin → finish(completed)
    assert [name for name, _ in services.run_store.calls] == ["begin", "finish"]
    assert services.run_store.calls[0][1]["mode"] == "webhook"
    assert services.run_store.calls[0][1]["agent_id"] == ""
    assert services.run_store.calls[-1][1]["status"] == "completed"
    assert services.run_store.calls[-1][1]["outputs"] == {"o": 1}
    assert services.run_store.calls[-1][1]["run_id"] == outcome.run_id
    # 钉版运行：版本/租户与依赖束逐项透传 run_graph
    assert captured["graph_version"] == 3
    assert captured["tenant_id"] == "t1"
    assert captured["graph_id"] == "g1"
    assert captured["registry"] == ("registry", services)
    assert captured["graph_resolver"] == ("resolver", services)
    assert captured["frame_sink"] == ("sink", "t1", outcome.run_id)
    assert captured["secret_provider"] == "secret"
    assert captured["memory_repo"] is None
    assert captured["approval_broker"] is services.approval_broker
    assert captured["event_wait_broker"] is services.event_wait_broker
    # memory 档不阻断子图挂起（pg 才阻断）
    assert captured["_block_subgraph_suspend"] is False
    # inputs 缺省：event 序列化透传
    assert captured["inputs"] == {"event": {"event": "payload"}}


def test_cycle_preset_run_id_skips_begin_and_uses_explicit_inputs() -> None:
    services = _services()

    def run_graph(graph, **kwargs):
        return {"outputs": {}, "trace": []}

    background_run.run_background_cycle(
        services, "t1", "g1", 1, _event(),
        mode="schedule", run_id="preset-1",
        cycle_seq=2, cycle_group_id="cg1", inputs={"i": 1}, agent_id="agent-7",
        deps=_deps(services, run_graph),
    )

    # 调度派发方已预 begin：一圈内不再重复 begin，只 finish
    assert [name for name, _ in services.run_store.calls] == ["finish"]
    assert services.run_store.calls[0][1]["run_id"] == "preset-1"
    assert services.run_store.calls[0][1]["status"] == "completed"


def test_cycle_missing_published_version_fails_fast() -> None:
    services = _services(raw=None)

    outcome = background_run.run_background_cycle(
        services, "t1", "g1", 3, _event(),
        mode="webhook", run_id=None, deps=_deps(services, lambda *a, **k: {}),
    )

    assert outcome.status == "failed" and outcome.stop_run is True
    assert services.run_store.calls[-1][1]["status"] == "failed"
    assert services.run_store.calls[-1][1]["error"] == "已发布版本不存在：g1@3"


def test_cycle_superseded_keeps_run_unfinished() -> None:
    services = _services()

    def supersede(*args, **kwargs):
        raise RunSuperseded("n1", "tok-1")

    outcome = background_run.run_background_cycle(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id=None, deps=_deps(services, supersede),
    )

    # docs/62 §2 D-4：输家不落 failed——run 保持 begin 后的未终态
    assert outcome.status == "suspended" and outcome.stop_run is True
    assert [name for name, _ in services.run_store.calls] == ["begin"]


def test_cycle_wait_timeout_suspension_keeps_run_unfinished() -> None:
    services = _services()

    def timeout_suspend(*args, **kwargs):
        raise WaitTimeoutSuspend("wait-1", "tok-9")

    outcome = background_run.run_background_cycle(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id=None, deps=_deps(services, timeout_suspend),
    )

    # docs/136 打包 BS：超时挂起不 finish、不清帧，等人工恢复
    assert outcome.status == "suspended" and outcome.stop_run is True
    assert [name for name, _ in services.run_store.calls] == ["begin"]


def test_cycle_failure_writes_failed_with_safe_error() -> None:
    services = _services()

    def boom(*args, **kwargs):
        raise RuntimeError("node exploded")

    outcome = background_run.run_background_cycle(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id=None, deps=_deps(services, boom),
    )

    assert outcome.status == "failed" and outcome.stop_run is True
    assert services.run_store.calls[-1][1]["status"] == "failed"
    # safe_run_error 把原始异常泛化成固定文案，内部串不出 run 记录
    assert services.run_store.calls[-1][1]["error"] == "运行时发生未预期错误"


def test_cycle_pg_backend_blocks_subgraph_suspend() -> None:
    services = _services()
    captured: dict = {}

    def run_graph(graph, **kwargs):
        captured.update(kwargs)
        return {"outputs": {}, "trace": []}

    deps = BackgroundRunDeps(
        run_graph=run_graph,
        frame_sink_for=lambda *a: None,
        resume_claim_for=lambda: None,
        runtime_registry_of=lambda s: None,
        tenant_graph_resolver_of=lambda s: None,
        secret_provider=None,
        storage_backend="pg",
    )
    background_run.run_background_cycle(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id=None, deps=deps,
    )
    assert captured["_block_subgraph_suspend"] is True


# --------------------------------------------------------------------------- #
# run_background_worker：图缺失 / 解析失败 / 圈会话接线
# --------------------------------------------------------------------------- #
def test_worker_missing_graph_fails_preset_run_only() -> None:
    services = _services(raw=None)

    background_run.run_background_worker(
        services, "t1", "g1", 3, _event(),
        mode="webhook", run_id="preset-1", deps=_deps(services, lambda *a, **k: {}),
    )

    assert services.run_store.calls == [
        ("finish", {"run_id": "preset-1", "status": "failed",
                    "error": "已发布版本不存在：g1@3"}),
    ]


def test_worker_missing_graph_without_preset_run_is_noop() -> None:
    services = _services(raw=None)

    background_run.run_background_worker(
        services, "t1", "g1", 3, _event(),
        mode="webhook", run_id=None, deps=_deps(services, lambda *a, **k: {}),
    )

    # 无预置 run 则无事可做：不动 run 记录
    assert services.run_store.calls == []


def test_worker_unparsable_graph_fails_preset_run(monkeypatch) -> None:
    services = _services(raw={"version": 1, "variables": [], "nodes": [], "edges": []})
    parse_calls: list = []

    def broken_parse(raw):
        parse_calls.append(raw)
        raise ValueError("no entrypoint")

    monkeypatch.setattr(background_run, "parse_graph", broken_parse)

    background_run.run_background_worker(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id="preset-1", deps=_deps(services, lambda *a, **k: {}),
    )

    assert len(parse_calls) == 1
    assert services.run_store.calls[-1][1]["status"] == "failed"
    assert services.run_store.calls[-1][1]["error"] == "运行时发生未预期错误"


def test_worker_plain_graph_runs_single_cycle_with_preset_run_id(monkeypatch) -> None:
    services = _services()
    cycle_calls: list[dict] = []

    def fake_cycle(services_, tenant_id, graph_id, version, event,
                   *, mode, run_id, deps, **kwargs):
        cycle_calls.append({
            "tenant_id": tenant_id, "graph_id": graph_id, "version": version,
            "mode": mode, "run_id": run_id, **kwargs,
        })
        return SimpleNamespace(status="completed", stop_run=False)

    monkeypatch.setattr(background_run, "run_background_cycle", fake_cycle)

    background_run.run_background_worker(
        services, "t1", "g1", 1, _event(),
        agent_id="agent-7", mode="webhook", run_id="preset-1",
        deps=_deps(services, lambda *a, **k: {}),
    )

    # 非圈图：跳过圈会话，直接跑一圈并沿用预置 run_id
    assert len(cycle_calls) == 1
    assert cycle_calls[0]["run_id"] == "preset-1"
    assert cycle_calls[0]["agent_id"] == "agent-7"


def test_worker_cycle_graph_drives_session_and_reuses_preset_run_once(monkeypatch) -> None:
    services = _services()
    session_calls: list[dict] = []
    cycle_calls: list[dict] = []

    def fake_cycle(services_, tenant_id, graph_id, version, event,
                   *, mode, run_id, deps, **kwargs):
        cycle_calls.append({"run_id": run_id, **kwargs})
        return SimpleNamespace(status="completed", stop_run=False)

    def fake_session(services_, graph, *, tenant_id, graph_id, inputs, run_once):
        session_calls.append({
            "tenant_id": tenant_id, "graph_id": graph_id, "inputs": inputs,
            "run_once": run_once,
        })

    monkeypatch.setattr(background_run, "run_background_cycle", fake_cycle)
    monkeypatch.setattr(background_run, "cycle_config",
                        lambda graph: SimpleNamespace(max_cycles=3))
    monkeypatch.setattr(background_run, "run_cycle_session", fake_session)

    background_run.run_background_worker(
        services, "t1", "g1", 1, _event(),
        mode="webhook", run_id="preset-1", deps=_deps(services, lambda *a, **k: {}),
    )

    # 圈图：进圈会话，inputs 统一为 event 序列化
    assert len(session_calls) == 1
    assert session_calls[0]["inputs"] == {"event": {"event": "payload"}}
    assert session_calls[0]["graph_id"] == "g1"

    # 第一圈沿用派发方预置 run_id（tick 内即可见 running），后续圈发新 run_id
    step1 = SimpleNamespace(seq=1, group_id="cg1", inputs={"i": 1})
    step2 = SimpleNamespace(seq=2, group_id="cg1", inputs={"i": 2})
    session_calls[0]["run_once"](step1)
    session_calls[0]["run_once"](step2)

    assert cycle_calls[0]["run_id"] == "preset-1"
    assert cycle_calls[0]["cycle_seq"] == 1
    assert cycle_calls[0]["cycle_group_id"] == "cg1"
    assert cycle_calls[0]["inputs"] == {"i": 1}
    assert cycle_calls[1]["run_id"] is None
    assert cycle_calls[1]["cycle_seq"] == 2
    assert cycle_calls[1]["inputs"] == {"i": 2}

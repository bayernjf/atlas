# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.run_stream.

不启 TestClient：`prepare_stream_run` 用 fake services 直调（请求线程预装配、
取消句柄登记、tracer 建/不建的分界）；`stream_saved_graph_events` 用
asyncio.run 直驱异步生成器（与 tests/test_sse_bridge.py 同款约定），注入
fake run_graph 诱导各条终帧路径，钉住终态记账与终帧形状。
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from atlas.collaboration.cancellations import RunCancelled
from atlas.debug import DebugStopped
from atlas.graph.loader import RunSuperseded, WaitTimeoutSuspend
from atlas.services import run_stream
from atlas.services.run_stream import StreamRunDeps


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


def _graph(*node_ids):
    return SimpleNamespace(nodes=[SimpleNamespace(id=nid, type="wait") for nid in node_ids])


def _principal(tenant="t1"):
    return SimpleNamespace(tenant_id=tenant)


def _services():
    return SimpleNamespace(
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
    monkeypatch.setattr(run_stream, "usage_context", _fake_usage_context)


def _deps(run_graph, evaluate_calls, heartbeat=0.05):
    return StreamRunDeps(
        run_graph=run_graph,
        frame_sink_for=lambda tenant_id, store, run_id: ("sink", tenant_id, run_id),
        resume_claim_for=lambda: None,
        evaluate_after_run=lambda svc, record: evaluate_calls.append(record),
        runtime_registry_of=lambda services: ("registry", services),
        tenant_graph_resolver_of=lambda services: ("resolver", services),
        tool_call_from_event=lambda event: {"tool": event["node_id"]},
        graph_version=lambda graph_id, version: str(version) if version else None,
        secret_provider="secret",
        storage_backend="memory",
        public_url="http://atlas.test",
        heartbeat_seconds=heartbeat,
    )


def _prepare(services, deps, *, debug_session=None, resolved_version=2, inputs=None):
    return run_stream.prepare_stream_run(
        _principal(), services,
        graph_id="g1", graph=_graph("n1", "n2"),
        event_payload={"k": "v"}, inputs=inputs or {"x": 1},
        resolved_version=resolved_version, debug_session=debug_session, deps=deps,
    )


def _parse_frames(chunks: list[str]) -> list[tuple[str, dict]]:
    raw = "".join(chunks)
    frames: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        if "event: " not in block or "data: " not in block:
            continue  # 心跳注释帧（: keepalive）天然不入事件帧列表
        lines = block.splitlines()
        event = next(ln[len("event: "):] for ln in lines if ln.startswith("event: "))
        data_line = next(ln[len("data: "):] for ln in lines if ln.startswith("data: "))
        frames.append((event, json.loads(data_line)))
    return frames


def _drive(prepare_kwargs, run_graph, evaluate_calls):
    """同一事件循环内预装配 + 排空生成器（SSEBridge 绑定 running loop）。"""

    async def run():
        ctx = _prepare(_services(), _deps(run_graph, evaluate_calls), **prepare_kwargs)
        chunks = [c async for c in run_stream.stream_saved_graph_events(ctx)]
        return ctx, chunks

    return asyncio.run(run())


def _prepare_in_loop(services, deps, **kwargs):
    """prepare_stream_run 捕获 running loop（SSEBridge 需要），直调用 asyncio.run 包裹。"""

    async def once():
        return _prepare(services, deps, **kwargs)

    return asyncio.run(once())


# --------------------------------------------------------------------------- #
# prepare_stream_run：请求线程预装配
# --------------------------------------------------------------------------- #
def test_prepare_registers_cancellation_and_builds_traced_context() -> None:
    services = _services()
    deps = _deps(lambda *a, **k: {}, [])
    ctx = _prepare_in_loop(
        services, deps,
        debug_session=None, resolved_version=2, inputs={"x": 1},
    )

    # B 包（docs/27 §4.1）：取消句柄在请求线程登记
    assert services.cancellation_broker.registered == [ctx.run_id]
    # 真实运行（非 debug）建 tracer：graph_version 经 deps.graph_version 解析
    assert ctx.tracer is not None and ctx.tracer.graph_version == "2"
    assert ctx.tracer.trace_id
    # graph_view 只收 id/type（监控 extract 的输入形状）
    assert ctx.graph_view == {
        "nodes": [{"id": "n1", "type": "wait"}, {"id": "n2", "type": "wait"}],
    }
    # frame_sink 绑定租户与预生成 run_id
    assert ctx.frame_sink == ("sink", "t1", ctx.run_id)
    assert ctx.resolved_version == 2 and ctx.inputs == {"x": 1}
    assert ctx.event_payload == {"k": "v"}
    assert ctx.tenant_id == "t1" and ctx.services is services


def test_prepare_with_debug_session_skips_tracer() -> None:
    services = _services()
    deps = _deps(lambda *a, **k: {}, [])
    ctx = _prepare_in_loop(services, deps, debug_session=SimpleNamespace())

    # debug 单步不是真实运行：不建 tracer（04 §5.13/§5.15，SSE 帧保持旧形状）
    assert ctx.tracer is None
    assert ctx.debug_session is not None
    assert services.cancellation_broker.registered == [ctx.run_id]


# --------------------------------------------------------------------------- #
# stream_saved_graph_events：终帧路径与终态记账
# --------------------------------------------------------------------------- #
def test_stream_completed_frames_and_accounting() -> None:
    evaluate_calls: list = []

    def run_graph(graph, **kwargs):
        kwargs["emit"]({"type": "node_start", "node_id": "n1"})
        kwargs["emit"]({"type": "node_end", "node_id": "n1", "output": {"a": 1}})
        kwargs["emit"]({"type": "tool_metric", "node_id": "tool-1"})
        return {"outputs": {"o": 1}, "trace": ["t1"], "traceTree": {"root": True}}

    ctx, chunks = _drive({}, run_graph, evaluate_calls)
    frames = _parse_frames(chunks)
    names = [name for name, _ in frames]

    assert names == ["node_start", "node_end", "tool_metric", "result"]
    result = frames[-1][1]
    # M10：终帧为 run_end 超集（spanId/graphVersion）；traceTree 不进 SSE
    assert result["id"] == "g1"
    assert result["outputs"] == {"o": 1} and result["trace"] == ["t1"]
    assert "traceTree" not in result
    assert result["spanId"] == ctx.tracer.root.span_id
    assert result["graphVersion"] == "2"

    # 终态记账：begin(resolved_version) → finish(completed) → record_run → 灰度评估
    assert [name for name, _ in ctx.run_store.calls] == ["begin", "finish"]
    assert ctx.run_store.calls[0][1]["resolved_version"] == 2
    assert ctx.run_store.calls[-1][1]["status"] == "completed"
    record = ctx.monitoring.records[-1]
    assert record["status"] == "completed" and record["mode"] == "stream"
    assert record["resolved_version"] == 2
    # completed 的逐节点成败从 outputs 推断（"o" 非图节点 id ⇒ 空）
    assert record["nodes"] == []
    # docs/28 §4.1 ⑧：顶层 tool_metric 另册收集进 tool_calls
    assert record["tool_calls"] == [{"tool": "tool-1"}]
    assert record["spans"] == {"root": True}
    assert len(evaluate_calls) == 1
    # 结束后取消句柄注销
    assert ctx.services.cancellation_broker.unregistered == [ctx.run_id]


def test_stream_cancelled_records_collected_nodes_no_rollout_gate() -> None:
    evaluate_calls: list = []

    def cancel_run(graph, **kwargs):
        # A 包（docs/27 §10.1）：顶层 node_end 进 collected，子图内部节点不进
        kwargs["emit"]({"type": "node_end", "node_id": "n1", "output": {"a": 1}})
        kwargs["emit"]({"type": "node_end", "node_id": "sub", "output": {"b": 2},
                        "subgraphPath": ["n1"]})
        raise RunCancelled("n2")

    ctx, chunks = _drive({}, cancel_run, evaluate_calls)
    frames = _parse_frames(chunks)
    # 抛错前 emit 的中间帧照常转发上屏，随后才是急停终帧
    assert [name for name, _ in frames] == ["node_end", "node_end", "cancelled"]
    assert frames[-1][1] == {"type": "cancelled", "node_id": "n2", "reason": "user_cancel"}

    # 用户主动急停：finish(cancelled)、record_run，但不进 M9 灰度门控、不算失败
    assert [name for name, _ in ctx.run_store.calls] == ["begin", "finish"]
    assert ctx.run_store.calls[-1][1]["status"] == "cancelled"
    record = ctx.monitoring.records[-1]
    assert record["status"] == "cancelled"
    # cancelled 的逐节点成败从 collected 推断：只有顶层节点进册
    assert [node.node_id for node in record["nodes"]] == ["n1"]
    assert evaluate_calls == []


def test_stream_superseded_zero_terminal_writes() -> None:
    evaluate_calls: list = []

    def supersede(graph, **kwargs):
        raise RunSuperseded("n1", "tok-1")

    ctx, chunks = _drive({}, supersede, evaluate_calls)
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["superseded"]
    assert frames[0][1] == {
        "type": "superseded", "node_id": "n1", "reason": "resumed_by_other_process",
    }

    # docs/62 §2 D-4：输家零终态写——不 finish、不 record_run、不评估
    assert [name for name, _ in ctx.run_store.calls] == ["begin"]
    assert ctx.monitoring.records == []
    assert evaluate_calls == []
    # finally 仍注销取消句柄
    assert ctx.services.cancellation_broker.unregistered == [ctx.run_id]


def test_stream_wait_timeout_suspended_keeps_run_open() -> None:
    evaluate_calls: list = []

    def timeout_suspend(graph, **kwargs):
        raise WaitTimeoutSuspend("n2", "tok-9")

    ctx, chunks = _drive({}, timeout_suspend, evaluate_calls)
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["wait_timeout_suspended"]
    assert frames[0][1] == {
        "type": "wait_timeout_suspended", "node_id": "n2",
        "reason": "timeout_kept_suspended",
    }

    # docs/136 打包 BS：run 保持 suspended 待人工恢复——无终态写
    assert [name for name, _ in ctx.run_store.calls] == ["begin"]
    assert ctx.monitoring.records == []
    assert evaluate_calls == []


def test_stream_failed_writes_safe_error_and_evaluates() -> None:
    evaluate_calls: list = []

    def boom(graph, **kwargs):
        kwargs["emit"]({"type": "node_end", "node_id": "n1", "output": {"a": 1}})
        raise RuntimeError("node exploded secret marker")

    ctx, chunks = _drive({}, boom, evaluate_calls)
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["node_end", "error"]
    detail = frames[-1][1]["detail"]
    # 未知异常折叠为固定安全文案与机器码，内部串不出 SSE（docs/101 D58）
    assert detail["code"] == "RUNTIME_UNEXPECTED"
    assert detail["message"] == "运行时发生未预期错误"
    assert "node exploded secret marker" not in "".join(chunks)

    assert ctx.run_store.calls[-1][1]["status"] == "failed"
    assert ctx.run_store.calls[-1][1]["error"] == "运行时发生未预期错误"
    record = ctx.monitoring.records[-1]
    assert record["status"] == "error"
    assert record["error"] == "运行时发生未预期错误"
    assert [node.node_id for node in record["nodes"]] == ["n1"]
    assert len(evaluate_calls) == 1


def test_stream_debug_session_skips_accounting_and_stops_cleanly() -> None:
    evaluate_calls: list = []

    def stopped(graph, **kwargs):
        raise DebugStopped("n1")

    ctx, chunks = _drive({"debug_session": SimpleNamespace()}, stopped, evaluate_calls)
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["stopped"]
    assert frames[0][1] == {"type": "stopped", "node_id": "n1", "reason": "user_stop"}

    # debug 会话不是真实运行：全程不埋点（不 begin/finish、不 record_run）
    assert ctx.run_store.calls == []
    assert ctx.monitoring.records == []
    assert evaluate_calls == []


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_stream_dead_worker_synthesizes_error_frame() -> None:
    evaluate_calls: list = []

    def die(graph, **kwargs):
        # BaseException 不被 except Exception 捕获：worker 裸死、无终帧 offer；
        # SystemExit 被 threading.excepthook 静默忽略。
        raise SystemExit(7)

    ctx, chunks = _drive({}, die, evaluate_calls)
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["error"]
    detail = frames[0][1]["detail"]
    assert detail == {"message": "运行时发生未预期错误", "code": "RUNTIME_UNEXPECTED",
                      "params": {}}
    # 暴毙路径不写终态（收尾不在 worker 控制内），取消句柄已注销
    assert [name for name, _ in ctx.run_store.calls] == ["begin"]
    assert ctx.services.cancellation_broker.unregistered == [ctx.run_id]


def test_stream_emits_keepalive_while_worker_alive() -> None:
    evaluate_calls: list = []

    def slow(graph, **kwargs):
        time.sleep(0.18)
        return {"outputs": {}, "trace": [], "traceTree": {}}

    ctx, chunks = _drive({"resolved_version": None}, slow, evaluate_calls)
    raw = "".join(chunks)
    # heartbeat_seconds=0.05：worker 存活期间至少一条心跳注释帧
    assert raw.count(": keepalive\n\n") >= 1
    frames = _parse_frames(chunks)
    assert [name for name, _ in frames] == ["result"]

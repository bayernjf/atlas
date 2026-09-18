"""M10 批 3：任务信封贯穿真实 traceId + task/tool span + RunRecord.trace_id（U47/U48 延伸）。

契约：04 §5.15 / 06 §6.15 / 03 task_envelope+run_record。
- sandbox 传 tracer 时，Envelope.traceId 用真实 traceId（不再占位 runId）、parentSpanId
  指向 task_dispatch span；dispatch/done/tool span 入树；
- 不传 tracer 时保持 M7 旧行为（traceId=runId、parentSpanId 空）；
- 幂等重放不重复执行（无新 task_done/tool span）；
- MonitoringStore.record_run 透传 trace_id。
"""

from __future__ import annotations

from atlas.coordination import TaskStore, run_return_refund
from atlas.harness.base import ActionRequest, Permission
from atlas.logistics import LogisticsAdapter
from atlas.monitoring.records import MonitoringStore
from atlas.shop.service import DemoShopService
from atlas.tracing import (
    KIND_TASK_DISPATCH,
    KIND_TASK_DONE,
    KIND_TOOL,
    Tracer,
)


def _shop() -> DemoShopService:
    return DemoShopService()


def _logistics(signed_order: str | None = None) -> LogisticsAdapter:
    adapter = LogisticsAdapter(granted_permissions={Permission.READ, Permission.WRITE})
    if signed_order:
        adapter.execute(
            ActionRequest(
                capability_name="sign_receipt",
                parameters={"order_id": signed_order},
            )
        )
    return adapter


def _collect(node: dict, acc: list[dict]) -> None:
    acc.append(node)
    for child in node.get("children", []):
        _collect(child, acc)


def test_sandbox_envelopes_carry_real_trace_and_span_tree():
    store = TaskStore()
    tracer = Tracer(graph_id="return-flow", graph_version="return-flow@1")
    result = run_return_refund(
        store,
        order_id="12346",
        shop=_shop(),
        logistics=_logistics("12346"),  # 已签收 → paid
        run_id="run-xyz",
        tracer=tracer,
    )
    assert result["outcome"] == "paid"

    # 信封 traceId 为真实 traceId（不再占位 runId），parentSpanId 指向 dispatch span
    for env in result["tasks"]:
        assert env.traceId == tracer.trace_id
        assert env.traceId != "run-xyz"
        assert len(env.parentSpanId) == 16
        assert env.graphVersion == "return-flow@1"

    flat: list[dict] = []
    _collect(tracer.to_tree(include_internal=True), flat)
    dispatch = [n for n in flat if n["kind"] == KIND_TASK_DISPATCH]
    done = [n for n in flat if n["kind"] == KIND_TASK_DONE]
    tools = [n for n in flat if n["kind"] == KIND_TOOL]
    assert len(dispatch) == 2
    assert len(done) == 2
    assert len(tools) == 1  # 仅物流 check_receipt 包 tool span
    assert {n["attrs"]["actor"] for n in dispatch} == {"bot.customer", "bot.logistics"}
    assert {n["attrs"]["actor"] for n in done} == {"bot.customer", "bot.logistics"}
    tool = tools[0]
    assert tool["attrs"]["adapter"] == "logistics"
    assert tool["attrs"]["capability"] == "check_receipt"
    assert tool["name"] == "tool:logistics/check_receipt"
    # 全树共享同一 traceId、无孤儿（root 之外都有 parentSpanId）
    assert {n["traceId"] for n in flat} == {tracer.trace_id}
    assert all("parentSpanId" in n for n in flat if n["kind"] != "run")


def test_sandbox_without_tracer_keeps_legacy_placeholder():
    store = TaskStore()
    result = run_return_refund(
        store, order_id="12345", shop=_shop(), logistics=_logistics()
    )
    assert result["outcome"] == "escalate"
    for env in result["tasks"]:
        assert env.traceId == "run-return"  # M7 占位行为保留
        assert env.parentSpanId == ""


def test_idempotent_replay_does_not_re_execute_tasks():
    store = TaskStore()
    shop = _shop()
    logistics = _logistics("12347")
    first_tracer = Tracer(graph_id="return-flow", graph_version="return-flow@1")
    first = run_return_refund(
        store, order_id="12347", shop=shop, logistics=logistics,
        run_id="run-a", tracer=first_tracer,
    )
    assert first["outcome"] == "paid"

    # 重放：新 run/新 tracer、同 store 同幂等键 → 不重复执行
    replay_tracer = Tracer(graph_id="return-flow", graph_version="return-flow@1")
    replay = run_return_refund(
        store, order_id="12347", shop=shop, logistics=logistics,
        run_id="run-b", tracer=replay_tracer,
    )
    assert replay["already_refunded"] is True

    # 重放 tracer 折叠树：无 task_done/tool（未执行），仅 2 个 internal 重放 dispatch（被折叠）
    folded = replay_tracer.to_tree(include_internal=False)
    fold_flat: list[dict] = []
    _collect(folded, fold_flat)
    assert not [n for n in fold_flat if n["kind"] == KIND_TASK_DONE]
    assert not [n for n in fold_flat if n["kind"] == KIND_TOOL]

    full_flat: list[dict] = []
    _collect(replay_tracer.to_tree(include_internal=True), full_flat)
    replay_dispatch = [n for n in full_flat if n["kind"] == KIND_TASK_DISPATCH]
    assert len(replay_dispatch) == 2
    assert all(n.get("internal") is True for n in replay_dispatch)
    assert all(n["attrs"].get("idempotentReplay") is True for n in replay_dispatch)


def test_record_run_persists_trace_id():
    monitoring = MonitoringStore()
    monitoring.record_run(
        graph_id="g",
        mode="sync",
        status="completed",
        started_at="2026-09-18T00:00:00+00:00",
        duration_ms=5.0,
        nodes=[],
        trace_id="trace-abc",
    )
    record = monitoring.list_runs("g")[0]
    assert record.trace_id == "trace-abc"

    # 默认空、向后兼容
    monitoring.record_run(
        graph_id="g2", mode="stream", status="completed",
        started_at="2026-09-18T00:00:01+00:00", duration_ms=1.0, nodes=[],
    )
    assert monitoring.list_runs("g2")[0].trace_id == ""

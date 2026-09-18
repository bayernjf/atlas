"""退货退款协同沙盘（M7 批 3，docs/20 §4.2 验收；M10 起挂 task/tool span）。

确定性 Graph 主干不动（19 §2.1），任务信封是 Graph 之上的执行层：本沙盘用
`TaskStore` + shop/logistics 适配器编排 dispatch→join→未签收禁放款 condition→
escalate，验证多 Bot 协同语义。沙盘不解除 D31（真实 Bot 接入仍需触发）。

M10（04 §5.15）：传入 `tracer` 时，dispatch 建 `task_dispatch` span、Bot 执行建
`task_done` span（actor=assignee、attrs orderId），物流核验内嵌 `tool` span；
信封 traceId 用真实 traceId（不再占位为 runId），parentSpanId 指向 dispatch span。
幂等重放不重复执行（无新 task_done/tool span），重放的 dispatch span 标 internal 折叠。
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from atlas.harness.base import ActionRequest
from atlas.tracing import (
    KIND_TASK_DISPATCH,
    KIND_TASK_DONE,
    KIND_TOOL,
    Span,
    Tracer,
)
from .store import TaskStore


def run_return_refund(
    store: TaskStore,
    *,
    order_id: str,
    shop: Any,
    logistics: Any,
    run_id: str = "run-return",
    graph_version: str = "return-flow@1",
    tracer: Tracer | None = None,
) -> dict[str, Any]:
    """退货退款协同：客服核验 + 物流签收并行 dispatch → join → 未签收禁放款。

    返回 `{outcome: "paid"|"escalate", reason, tasks, cas?}`：
    - 物流未签收 → `escalate`（未签收禁止放款，升级人工审批）；
    - 已签收 → `paid`（CAS 乐观锁放款，冲突时 `cas_conflict`）。
    """
    trace_id = tracer.trace_id if tracer is not None else run_id

    def _dispatch(task_type: str, assignee: str, idem_key: str):
        # dispatch span 包裹信封创建；幂等命中（重放）时该 span 标 internal 折叠。
        span_cm = (
            tracer.span(
                f"task_dispatch:{task_type}",
                kind=KIND_TASK_DISPATCH,
                actor=assignee,
                orderId=order_id,
                taskType=task_type,
            )
            if tracer is not None
            else nullcontext()
        )
        with span_cm as dispatch_span:
            kwargs = dict(
                run_id=run_id,
                idempotency_key=idem_key,
                trace_id=trace_id,
                graph_version=graph_version,
                type=task_type,
                assignee=assignee,
                payload={"orderId": order_id},
                deadline_ms=30000,
            )
            if isinstance(dispatch_span, Span):
                kwargs["parent_span_id"] = dispatch_span.span_id
            envelope, created = store.dispatch(**kwargs)
            if not created and isinstance(dispatch_span, Span):
                dispatch_span.attrs["idempotentReplay"] = True
                dispatch_span.internal = True
        return envelope, created

    # dispatch 两个任务信封（L1 幂等键 `资源|动作|版本`；幂等命中返首结果、created=False）
    customer_env, customer_created = _dispatch(
        "refund.verify_order", "bot.customer", f"{order_id}|verify|v1"
    )
    logistics_env, logistics_created = _dispatch(
        "logistics.check_receipt", "bot.logistics", f"{order_id}|receipt|v1"
    )

    def _done_span(task_type: str, assignee: str):
        return (
            tracer.span(
                f"task_done:{task_type}",
                kind=KIND_TASK_DONE,
                actor=assignee,
                orderId=order_id,
                taskType=task_type,
            )
            if tracer is not None
            else nullcontext()
        )

    # 客服 Bot：核验订单（幂等命中则跳过执行、复用首结果）
    if customer_created:
        with _done_span("refund.verify_order", "bot.customer"):
            store.accept(customer_env.taskId)
            store.start(customer_env.taskId)
            pending = shop.list_pending_refunds()
            store.complete(customer_env.taskId, {"pending": pending})

    # 物流 Bot：核验签收（幂等命中则从首结果取 signed，避免重复执行）
    if logistics_created:
        with _done_span("logistics.check_receipt", "bot.logistics"):
            store.accept(logistics_env.taskId)
            store.start(logistics_env.taskId)
            if tracer is not None:
                tool_cm = tracer.span(
                    "tool:logistics/check_receipt",
                    kind=KIND_TOOL,
                    adapter="logistics",
                    capability="check_receipt",
                )
            else:
                tool_cm = nullcontext()
            with tool_cm:
                receipt = logistics.execute(
                    ActionRequest(
                        capability_name="check_receipt",
                        parameters={"order_id": order_id},
                    )
                )
            signed = bool(receipt.output["signed"])
            store.complete(logistics_env.taskId, {"signed": signed})
    else:
        signed = bool(logistics_env.result.get("signed", False))

    tasks = [store.get(customer_env.taskId), store.get(logistics_env.taskId)]

    # join（两信封均 done）→ condition：未签收禁止放款 → escalate
    if not signed:
        return {"outcome": "escalate", "reason": "未签收，禁止放款", "tasks": tasks}

    # 放款：CAS 乐观锁（L2；金融写仍走 shop 单一通道）。
    # 幂等重放返首结果：订单已放款则直接返回，不重复执行（19 §2.4 L1）。
    order = shop.get_order(order_id)
    if order.status == "refunded":
        return {"outcome": "paid", "already_refunded": True, "tasks": tasks}
    cas = shop.compare_and_set(order_id, order.version, status="refunded", note="退货签收后放款")
    if cas.get("conflict"):
        return {"outcome": "cas_conflict", "cas": cas, "tasks": tasks}
    return {"outcome": "paid", "cas": cas, "tasks": tasks}

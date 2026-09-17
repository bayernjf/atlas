"""退货退款协同沙盘（M7 批 3，docs/20 §4.2 验收）。

确定性 Graph 主干不动（19 §2.1），任务信封是 Graph 之上的执行层：本沙盘用
`TaskStore` + shop/logistics 适配器编排 dispatch→join→未签收禁放款 condition→
escalate，验证多 Bot 协同语义。沙盘不解除 D31（真实 Bot 接入仍需触发）。
"""

from __future__ import annotations

from typing import Any

from atlas.harness.base import ActionRequest
from .store import TaskStore


def run_return_refund(
    store: TaskStore,
    *,
    order_id: str,
    shop: Any,
    logistics: Any,
    run_id: str = "run-return",
    graph_version: str = "return-flow@1",
) -> dict[str, Any]:
    """退货退款协同：客服核验 + 物流签收并行 dispatch → join → 未签收禁放款。

    返回 `{outcome: "paid"|"escalate", reason, tasks, cas?}`：
    - 物流未签收 → `escalate`（未签收禁止放款，升级人工审批）；
    - 已签收 → `paid`（CAS 乐观锁放款，冲突时 `cas_conflict`）。
    """
    # dispatch 两个任务信封（L1 幂等键 `资源|动作|版本`；幂等命中返首结果、created=False）
    customer_env, customer_created = store.dispatch(
        run_id=run_id, idempotency_key=f"{order_id}|verify|v1", trace_id=run_id,
        graph_version=graph_version, type="refund.verify_order", assignee="bot.customer",
        payload={"orderId": order_id}, deadline_ms=30000,
    )
    logistics_env, logistics_created = store.dispatch(
        run_id=run_id, idempotency_key=f"{order_id}|receipt|v1", trace_id=run_id,
        graph_version=graph_version, type="logistics.check_receipt", assignee="bot.logistics",
        payload={"orderId": order_id}, deadline_ms=30000,
    )

    # 客服 Bot：核验订单（幂等命中则跳过执行、复用首结果）
    if customer_created:
        store.accept(customer_env.taskId)
        store.start(customer_env.taskId)
        pending = shop.list_pending_refunds()
        store.complete(customer_env.taskId, {"pending": pending})

    # 物流 Bot：核验签收（幂等命中则从首结果取 signed，避免重复执行）
    if logistics_created:
        store.accept(logistics_env.taskId)
        store.start(logistics_env.taskId)
        receipt = logistics.execute(
            ActionRequest(capability_name="check_receipt", parameters={"order_id": order_id})
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

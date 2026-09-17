"""M7 批 3：退货退款协同沙盘（08 M7 立项条，docs/20 §4.2 验收，U47/U48）。"""

from __future__ import annotations

from atlas.coordination import TaskStore, run_return_refund
from atlas.harness.base import ActionRequest, Permission
from atlas.logistics import LogisticsAdapter
from atlas.shop.service import DemoShopService


def _shop() -> DemoShopService:
    return DemoShopService()


def _logistics() -> LogisticsAdapter:
    return LogisticsAdapter(granted_permissions={Permission.READ, Permission.WRITE})


def test_unsigned_receipt_escalates():
    """退货退款：物流未签收 → join 后 condition 禁放款 → escalate。"""
    store = TaskStore()
    result = run_return_refund(store, order_id="12345", shop=_shop(), logistics=_logistics())
    assert result["outcome"] == "escalate"
    assert result["reason"] == "未签收，禁止放款"
    # 两个任务信封均走完 done，状态机合法
    assert [t.state for t in result["tasks"]] == ["done", "done"]
    assert store.get(result["tasks"][1].taskId).result["signed"] is False
    # 订单未被放款
    assert _shop().get_order("12345").status == "pending"


def test_signed_receipt_pays_via_cas():
    """退货退款：物流已签收 → join 后放款（CAS 乐观锁）。"""
    logistics = _logistics()
    logistics.execute(ActionRequest(capability_name="sign_receipt", parameters={"order_id": "12346"}))
    shop = _shop()
    store = TaskStore()
    result = run_return_refund(store, order_id="12346", shop=shop, logistics=logistics)
    assert result["outcome"] == "paid"
    assert result["cas"]["conflict"] is False
    assert shop.get_order("12346").status == "refunded"


def test_idempotent_replay_returns_first_result_and_cas_blocks_double_payout():
    """幂等重放返首结果 + CAS 防重复放款（19 §2.4 L1/L2）。"""
    logistics = _logistics()
    logistics.execute(ActionRequest(capability_name="sign_receipt", parameters={"order_id": "12347"}))
    shop = _shop()
    store = TaskStore()

    first = run_return_refund(store, order_id="12347", shop=shop, logistics=logistics)
    assert first["outcome"] == "paid"
    assert len(store.list()) == 2

    # 幂等重放：同幂等键 dispatch 返首信封（不新建任务），订单已放款则直接返首结果
    replay = run_return_refund(store, order_id="12347", shop=shop, logistics=logistics)
    assert len(store.list()) == 2  # 不新建任务
    assert replay["outcome"] == "paid"
    assert replay["already_refunded"] is True  # 首结果，不重复执行
    assert shop.get_order("12347").status == "refunded"  # 未重复放款
    assert shop.get_order("12347").version == 1  # 只放款一次


def test_timeout_then_fail_safe():
    """超时三级链（19 §2.4）：任务超时 → timeout 终态 → fail-safe（下游判 status）。"""
    store = TaskStore()
    env, created = store.dispatch(
        run_id="run-1", idempotency_key="refund-1|receipt|v1", trace_id="run-1",
        graph_version="return-flow@1", type="logistics.check_receipt",
        assignee="bot.logistics", payload={"orderId": "12345"}, deadline_ms=1,
    )
    assert created is True
    store.accept(env.taskId)
    store.start(env.taskId)
    timed_out = store.timeout(env.taskId)
    assert timed_out.state == "timeout"
    assert store.get(env.taskId).state in {"timeout"}
    # fail-safe：run 仍可继续（下游 condition 按 timeout 状态判），任务记录保留供查询
    assert store.list("timeout")[0].taskId == env.taskId

"""M7 批 2：Bot 执行体抽象 + 假物流适配器 + CAS 乐观锁（08 M7 立项条，U47/U48 部分）。"""

from __future__ import annotations

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.logistics import LogisticsAdapter
from atlas.shop.service import DemoShopService
from atlas.skills import Bot, CoordinatorConfig, Skill


def _logistics_bot() -> Bot:
    return Bot(
        id="bot.logistics",
        name="物流 Bot",
        skills=[
            Skill(
                id="logistics-check",
                name="签收核验",
                capabilities=["logistics/check_receipt", "logistics/sign_receipt"],
                required_permissions=["read", "write"],
            )
        ],
        adapter_ids=["logistics"],
        coordinator=CoordinatorConfig(confidence_threshold=0.5),
    )


def test_bot_abstraction_and_logistics_adapter():
    bot = _logistics_bot()
    assert bot.id == "bot.logistics"
    assert bot.adapter_ids == ["logistics"]
    assert bot.coordinator.confidence_threshold == 0.5
    assert bot.skills[0].capabilities == ["logistics/check_receipt", "logistics/sign_receipt"]

    adapter = LogisticsAdapter(
        granted_permissions={Permission.READ, Permission.WRITE}
    )
    # 初始未签收
    checked = adapter.execute(
        ActionRequest(capability_name="check_receipt", parameters={"order_id": "12345"})
    )
    assert checked.status == ActionStatus.SUCCESS
    assert checked.output == {"order_id": "12345", "signed": False}

    # 签收后 check 返回 signed=true
    signed = adapter.execute(
        ActionRequest(capability_name="sign_receipt", parameters={"order_id": "12345"})
    )
    assert signed.status == ActionStatus.SUCCESS
    assert signed.output["signed"] is True
    assert adapter.execute(
        ActionRequest(capability_name="check_receipt", parameters={"order_id": "12345"})
    ).output["signed"] is True


def test_cas_optimistic_lock_conflict_and_success():
    """L2 乐观锁 CAS（19 §2.4）：version 匹配才更新，冲突返回 current_version 供重读。"""
    shop = DemoShopService()
    order = shop.get_order("12345")
    v0 = order.version

    # 一方成功 CAS：pending → refunded，version +1
    result = shop.compare_and_set("12345", v0, status="refunded", note="放款")
    assert result["conflict"] is False
    assert result["version"] == v0 + 1
    assert shop.get_order("12345").status == "refunded"

    # 另一方持旧 version 并发写 → 冲突，重读发现已终态则放弃
    conflict = shop.compare_and_set("12345", v0, status="human_review", note="改判")
    assert conflict["conflict"] is True
    assert conflict["current_version"] == v0 + 1
    assert conflict["status"] == "refunded"  # 重读发现已终态
    assert shop.get_order("12345").status == "refunded"  # 未覆盖


def test_execute_refund_does_not_require_version():
    """既有 execute_refund 零回归：不加 version 参数，仍可退款。"""
    shop = DemoShopService()
    result = shop.execute_refund("12346", note="自动退款")
    assert result["status"] == "refunded"
    assert shop.get_order("12346").version >= 0  # version 字段存在但不影响旧路径

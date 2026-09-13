"""Demo 电商售后平台（进程内模拟）。

W9-W10 端到端 Demo 的目标系统：种子退款单数据与真实操作（执行退款/
发起人工审批）都落在本服务并保留状态，供 ShopHarnessAdapter 与
模拟商家控制台页面共用。业务规则对齐 docs/06 §9.2 黄金用例。
"""

from __future__ import annotations

from dataclasses import dataclass, field

PENDING = "pending"
REFUNDED = "refunded"
HUMAN_REVIEW = "human_review"


@dataclass
class RefundOrder:
    order_id: str
    reason: str
    amount: float
    status: str = PENDING
    history: list[str] = field(default_factory=list)


def seed_orders() -> dict[str, RefundOrder]:
    rows = [
        RefundOrder("12345", "商品破损", 299),
        RefundOrder("12346", "不想要了", 5000),
        RefundOrder("12347", "商品有质量瑕疵", 128),
        RefundOrder("12348", "商家错发商品", 460),
        RefundOrder("12349", "尺寸不合适", 899),
    ]
    return {row.order_id: row for row in rows}


class DemoShopService:
    def __init__(self, orders: dict[str, RefundOrder] | None = None) -> None:
        self.orders = orders if orders is not None else seed_orders()
        self.logged_in = False

    def login(self, username: str = "demo", password: str = "demo") -> bool:
        if username == "demo" and password == "demo":
            self.logged_in = True
            return True
        return False

    def list_pending_refunds(self) -> list[dict]:
        return [
            {"order_id": order.order_id, "reason": order.reason, "amount": order.amount}
            for order in self.orders.values()
            if order.status == PENDING
        ]

    def get_order(self, order_id: str) -> RefundOrder:
        if order_id not in self.orders:
            raise KeyError(f"订单不存在：{order_id}")
        return self.orders[order_id]

    def execute_refund(self, order_id: str, note: str = "") -> dict:
        order = self.get_order(order_id)
        order.status = REFUNDED
        order.history.append(f"自动退款：{note}")
        return {"order_id": order_id, "status": REFUNDED}

    def request_human_approval(self, order_id: str, note: str = "") -> dict:
        order = self.get_order(order_id)
        order.status = HUMAN_REVIEW
        order.history.append(f"转人工审批：{note}")
        return {"order_id": order_id, "status": HUMAN_REVIEW}

    def reset(self) -> None:
        """恢复种子数据（种子客户每家从初始状态体验）。"""
        self.orders = seed_orders()
        self.logged_in = False

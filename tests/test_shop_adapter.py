"""Demo 电商服务与 shop 适配器测试（06 §6.4 契约 / §9.2 用例）。"""

from __future__ import annotations

import pytest

from atlas.harness.base import ActionRequest, Permission
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import HUMAN_REVIEW, PENDING, REFUNDED, DemoShopService


def test_seed_orders_are_all_pending():
    service = DemoShopService()
    assert {order["order_id"] for order in service.list_pending_refunds()} == {
        "12345",
        "12346",
        "12347",
        "12348",
        "12349",
    }


def test_execute_refund_and_human_approval_transitions():
    service = DemoShopService()
    assert service.execute_refund("12345", "质量原因")["status"] == REFUNDED
    assert service.orders["12345"].status == REFUNDED
    assert service.request_human_approval("12346", "主观原因")["status"] == HUMAN_REVIEW
    pending = {order["order_id"] for order in service.list_pending_refunds()}
    assert pending == {"12347", "12348", "12349"}


def test_login_accepts_demo_credentials_only():
    service = DemoShopService()
    assert service.login("demo", "demo") is True
    assert service.login("demo", "wrong") is False


def _adapter(service=None, permissions=None):
    return ShopHarnessAdapter(
        service=service or DemoShopService(),
        granted_permissions=permissions or {Permission.READ, Permission.WRITE, Permission.FINANCIAL},
    )


def test_adapter_process_refund_routes_by_action():
    adapter = _adapter()
    approved = adapter.execute(
        ActionRequest("process_refund", {"order_id": "12345", "action": "approve_refund", "note": "破损"})
    )
    assert approved.status.value == "SUCCESS"
    assert approved.output == {"order_id": "12345", "status": REFUNDED}

    human = adapter.execute(
        ActionRequest(
            "process_refund", {"order_id": "12346", "action": "request_human_approval", "note": "主观"}
        )
    )
    assert human.output == {"order_id": "12346", "status": HUMAN_REVIEW}


def test_adapter_invalid_action_and_missing_order_return_structured_errors():
    adapter = _adapter()
    bad_action = adapter.execute(ActionRequest("process_refund", {"order_id": "12345", "action": "free_money"}))
    assert bad_action.status.value == "FAILED"
    assert bad_action.error.code == "INVALID_ACTION"

    missing = adapter.execute(
        ActionRequest("process_refund", {"order_id": "99999", "action": "approve_refund"})
    )
    assert missing.error.code == "ORDER_NOT_FOUND"

    no_param = adapter.execute(ActionRequest("execute_refund", {}))
    assert no_param.error.code == "MISSING_PARAMETER"


def test_adapter_login_failure_is_structured():
    adapter = _adapter()
    result = adapter.execute(ActionRequest("login", {"username": "demo", "password": "bad"}))
    assert result.status.value == "FAILED"
    assert result.error.code == "AUTH_FAILED"


def test_financial_capability_requires_permission():
    adapter = _adapter(permissions={Permission.READ})
    denied = adapter.execute(ActionRequest("process_refund", {"order_id": "1", "action": "approve_refund"}))
    assert denied.status.value == "FAILED"
    assert adapter.service.orders["12345"].status == PENDING


def test_list_pending_refunds_capability():
    adapter = _adapter()
    result = adapter.execute(ActionRequest("list_pending_refunds", {}))
    assert result.status.value == "SUCCESS"
    assert len(result.output["orders"]) == 5
    observation = adapter.observe()
    assert observation.title == "Demo 商家售后控制台"

"""W9-W10 集成：自动登录 Demo 商家平台并抓取待处理退款单（08 §7.3 验收 2）。

运行：
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_shop_platform_integration.py

链路：trigger → shop/login → shop/list_pending_refunds，
经真实适配器注册表与 DemoShopService 执行（非模拟分支）。
"""

from __future__ import annotations

import os

import pytest

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.shop.service import DemoShopService

pytestmark = pytest.mark.integration

pytest.importorskip(
    "fastapi",
    reason="integration requires API dependencies",
)
if os.environ.get("ATLAS_RUN_INTEGRATION") != "1":
    pytest.skip("set ATLAS_RUN_INTEGRATION=1 to run shop platform integration tests", allow_module_level=True)


def _login_fetch_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "定时抓取",
                 "config": {"triggerType": "manual"}},
                {"id": "tool_call-1", "type": "tool_call", "name": "自动登录",
                 "config": {"tool": "shop/login"}},
                {"id": "tool_call-2", "type": "tool_call", "name": "抓取退款单",
                 "config": {"tool": "shop/list_pending_refunds"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "tool_call-1"},
                {"id": "e2", "source": "tool_call-1", "target": "tool_call-2"},
            ],
        }
    )


def test_auto_login_then_fetch_pending_refunds():
    from atlas.harness.base import Permission
    from atlas.harness.registry import AdapterRegistry
    from atlas.shop.adapter import ShopHarnessAdapter

    service = DemoShopService()
    assert service.logged_in is False
    registry = AdapterRegistry()
    registry.register(
        ShopHarnessAdapter(
            service=service,
            granted_permissions={Permission.READ, Permission.WRITE},
        )
    )

    result = run_graph(_login_fetch_graph(), registry=registry)

    assert service.logged_in is True
    login_output = result["outputs"]["tool_call-1"]
    assert login_output["action_status"] == "SUCCESS"
    assert login_output["result"] == {"logged_in": True}
    fetched = result["outputs"]["tool_call-2"]["result"]
    assert len(fetched["orders"]) == 5
    assert {order["order_id"] for order in fetched["orders"]} == {
        "12345",
        "12346",
        "12347",
        "12348",
        "12349",
    }

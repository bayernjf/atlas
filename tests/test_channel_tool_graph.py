# -*- coding: utf-8 -*-
"""渠道工具在图里真能跑（docs/67 打包 M；解 docs/63 §0A N2）。

N2 的实况是：`channel:shopify/<binding>` 不在通用 JSON 通道里，落进 demo shop 的按能力
硬编码装配，只拿到 `{"note": …}`，到适配器再取 `params["order_id"]` KeyError ⇒ 节点必失败，
且全仓没有一条测试把图跑过它。本文件把这条链钉住——**外部只假在 HTTP 传输层**
（Shopify API 本身），适配器/客户端/参数装配/图执行全走真码。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atlas.channels.adapter import ShopifyHarnessAdapter
from atlas.channels.base import TransportResponse
from atlas.channels.shopify import ShopifyChannelClient
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import GENERIC_JSON_ADAPTERS, build_demo_registry, run_graph
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry

BINDING_ID = "ch-9"
TOOL = f"channel:shopify:{BINDING_ID}/create_refund"

ORDER = {
    "id": 12345, "name": "#12345", "email": "a@example.com",
    "financial_status": "paid", "fulfillment_status": None,
    "total_price": "299.00", "currency": "USD",
    "created_at": "2026-09-01T00:00:00Z",
    "line_items": [{"id": 11, "quantity": 1}],
}


class FakeTransport:
    """只假在 Shopify HTTP 边界：请求体由真客户端拼出来，断言才有意义。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout):
        self.calls.append({"method": method, "url": url, "json_body": json_body})
        if method == "GET":
            return TransportResponse(status=200, headers={}, text=json.dumps({"order": ORDER}))
        return TransportResponse(status=200, headers={}, text=json.dumps({"refund": {"id": 99, "status": "refunded"}}))


class _StubChannelRegistry:
    """渠道绑定/令牌解析的最小替身——被测的是 loader 路由与适配器参数装配。"""

    def _require(self, binding_id: str) -> dict[str, str]:
        return {"id": binding_id, "shop": "acme"}

    def client_for(self, binding) -> ShopifyChannelClient:
        return ShopifyChannelClient("acme", "shpat-test", transport=binding["transport"])


class _RegistryWithTransport(_StubChannelRegistry):
    """把 fake 传输塞进真客户端；绑定/令牌解析这层不是本文件要测的东西。"""

    def __init__(self, transport: FakeTransport) -> None:
        self._transport = transport

    def client_for(self, binding) -> ShopifyChannelClient:
        return ShopifyChannelClient("acme", "shpat-test", transport=self._transport)


def _registry(transport: FakeTransport) -> AdapterRegistry:
    registry = build_demo_registry()
    registry.register(
        ShopifyHarnessAdapter(
            BINDING_ID,
            _RegistryWithTransport(transport),
            granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL},
        )
    )
    return registry


def _tool_graph(params: str):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "tool-1", "type": "tool_call", "name": "渠道退款",
                 "config": {"tool": TOOL, "params": params}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
    )


def _chain_graph(params: str):
    """trigger → ai_decision → human_approval → 渠道真实退款（N2 要求的完整链形）。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
                 "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}}"}},
                {"id": "human-1", "type": "human_approval", "name": "审批",
                 "config": {
                     "summary": "订单退款审批", "approver": "客服主管",
                     "timeoutSeconds": 300, "onTimeout": "reject",
                     "approvedTarget": "tool-1", "rejectedTarget": "tool-note",
                 }},
                {"id": "tool-1", "type": "tool_call", "name": "渠道退款",
                 "config": {"tool": TOOL, "params": params}},
                {"id": "tool-note", "type": "tool_call", "name": "记录拒绝",
                 "config": {"tool": "op-reject"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "human-1"},
                {"id": "e3", "source": "human-1", "target": "tool-1"},
                {"id": "e4", "source": "human-1", "target": "tool-note"},
            ],
        }
    )


PARAMS = '{"order_id": "{{trigger-1.context.payload.order_id}}", "amount": 299, "reason": "破损", "full_refund": true}'


# --- 通用 JSON 通道现在认得 channel: ------------------------------------------


def test_channel_tool_receives_json_params_and_refunds():
    transport = FakeTransport()
    result = run_graph(
        _tool_graph(PARAMS),
        inputs={"order_id": "12345"},
        registry=_registry(transport),
    )
    node = result["outputs"]["tool-1"]
    assert node["action_status"] == "SUCCESS", node
    assert node["result"] == {"refundId": 99, "status": "refunded", "amount": 299.0}

    posts = [c for c in transport.calls if c["method"] == "POST"]
    assert len(posts) == 1, transport.calls
    body = posts[0]["json_body"]["refund"]
    assert body["transactions"] == [{"kind": "refund", "amount": "299.00"}], (
        "真客户端拼出的请求体必须带上图里插值出来的 order_id/amount——这才是 N2 修的实质"
    )
    assert "/orders/12345/refunds" in posts[0]["url"]


def test_channel_tool_bad_json_fails_before_any_http():
    transport = FakeTransport()
    result = run_graph(
        _tool_graph("not json at all"),
        inputs={"order_id": "12345"},
        registry=_registry(transport),
    )
    node = result["outputs"]["tool-1"]
    assert node["result"]["status"] == "FAILED"
    assert node["result"]["code"] == "INVALID_PARAMETER"
    assert transport.calls == [], "非法 params 不该打到外部 API"


def test_channel_tool_missing_required_key_reports_channel_param_error():
    """边界仍在：params 合法但少 order_id ⇒ 由真适配器报缺参，而不是 KeyError 冒出来。"""
    transport = FakeTransport()
    result = run_graph(
        _tool_graph('{"amount": 10}'),
        inputs={"order_id": "12345"},
        registry=_registry(transport),
    )
    node = result["outputs"]["tool-1"]
    assert node["result"]["status"] == "FAILED"
    assert node["result"]["code"] == "CHANNEL_INVALID_PARAMETER"


# --- N2 的完整链形：审批通过 → 真渠道退款 --------------------------------------


def test_approved_human_approval_refunds_through_the_real_channel_adapter():
    transport = FakeTransport()
    events: list[dict] = []
    result = run_graph(
        _chain_graph(PARAMS),
        inputs={"order_id": "12345", "reason": "破损", "approvals": {"human-1": "approved"}},
        registry=_registry(transport),
        emit=events.append,
    )
    assert result["status"] == "completed"
    assert result["outputs"]["human-1"]["target"] == "tool-1"
    assert result["outputs"]["tool-1"]["action_status"] == "SUCCESS"
    assert [c["method"] for c in transport.calls] == ["GET", "POST"]
    start_events = [e["node_id"] for e in events if e["type"] == "node_start"]
    assert start_events == ["trigger-1", "ai_decision-1", "human-1", "tool-1"]


# --- 反向门：路由表本身若不含 channel 前缀，上面的链必红 -----------------------


def test_reverse_gate_channel_prefix_is_the_load_bearing_set():
    """`channel:` 走通用通道是靠那一个 startswith 前缀——它被摘掉时本批用例必须失效。"""
    from atlas.graph import loader

    source = loader._execute_tool.__code__.co_consts
    assert any(
        isinstance(c, tuple) and "openapi:" in c and "channel:" in c for c in source
    ), (
        "路由前缀里找不到 channel：/openapi: ——说明判断条件被改写，"
        "docs/63 §0A N2 的那条 KeyError 路径就重新打开了"
    )
    assert "channel:" not in GENERIC_JSON_ADAPTERS, (
        "channel: 不该塞进 frozenset（id 带 binding 后缀，只能按前缀判）"
    )

def test_counterfactual_non_generic_id_still_hits_the_hardcoded_assembly(tmp_path, monkeypatch):
    """并排反证（不改生产码）：同一个 JSON params 交给**非通用通道**的适配器 id，
    仍会落进 demo shop 的硬编码装配 ⇒ 只拿到 note ⇒ 缺参失败。
    这证明本批承重的是那条 `channel:` 前缀，不是别处的巧合。
    """
    from atlas.graph.loader import _execute_tool

    transport = FakeTransport()
    from atlas.graph.dsl import NodeDSL

    node = NodeDSL.model_validate(
        {"id": "tool-1", "type": "tool_call", "name": "x",
         "config": {"tool": "shoplike/create_refund", "params": '{"order_id": "12345", "amount": 299}'}}
    )
    registry = build_demo_registry()
    real = ShopifyHarnessAdapter("ch-cf", _RegistryWithTransport(transport),
                                 granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL})
    # 注册前改掉 id：同一个**真适配器**挂到不带 channel: 前缀的名字上，
    # 于是 loader 走 demo shop 的硬编码装配，params 只剩 note（＝改造前的世界）。
    real.adapter_id = "shoplike"
    registry.register(real)

    out = _execute_tool(
        node,
        {"trigger-1": {"context": {"payload": {"order_id": "12345"}}}},
        registry,
    )
    assert out["result"]["status"] == "FAILED"
    assert out["result"]["code"] == "CHANNEL_INVALID_PARAMETER", out

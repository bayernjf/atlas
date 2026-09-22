# -*- coding: utf-8 -*-
"""Shopify 渠道 Harness 适配器（docs/38 §1B；ADR T28）。

每个绑定产出一个适配器实例（adapter_id channel:shopify:{bindingId}），
由 loader 按租户动态注册进 AdapterRegistry。
"""

from __future__ import annotations

from typing import Callable

from atlas.channels.base import ChannelError
from atlas.channels.registry import ChannelRegistry
from atlas.channels.shopify import ShopifyChannelClient
from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)

_LIST_ORDERS_INPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["open", "any", "closed"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 250},
    },
}

_ORDER_INPUT = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}

_REFUND_INPUT = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "reason": {"type": "string", "maxLength": 200},
        "full_refund": {"type": "boolean"},
    },
    "required": ["order_id", "amount"],
}


class ShopifyHarnessAdapter(HarnessAdapter):
    adapter_type = "shop"

    def __init__(
        self,
        binding_id: str,
        registry: ChannelRegistry,
        *,
        granted_permissions: set[Permission] | None = None,
        audit_sink: Callable | None = None,
    ) -> None:
        super().__init__(granted_permissions=granted_permissions, audit_sink=audit_sink)
        self.adapter_id = f"channel:shopify:{binding_id}"
        self._binding_id = binding_id
        self._registry = registry

    def _client(self) -> ShopifyChannelClient:
        binding = self._registry._require(self._binding_id)
        return self._registry.client_for(binding)

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="list_orders",
                description="列出 Shopify 店铺订单（open/any/closed）",
                action="shop_list_orders",
                input_schema=_LIST_ORDERS_INPUT,
                permission=Permission.READ,
                is_idempotent=True,
            ),
            Capability(
                name="get_order",
                description="按 id 获取 Shopify 订单详情",
                action="shop_get_order",
                input_schema=_ORDER_INPUT,
                permission=Permission.READ,
                is_idempotent=True,
            ),
            Capability(
                name="create_refund",
                description="对 Shopify 订单发起退款（金额≤订单总额）",
                action="shop_create_refund",
                input_schema=_REFUND_INPUT,
                permission=Permission.FINANCIAL,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        params = request.parameters
        try:
            client = self._client()
            if request.capability_name == "list_orders":
                output = client.list_orders(
                    status=params.get("status", "open"),
                    limit=params.get("limit", 50),
                )
            elif request.capability_name == "get_order":
                output = client.get_order(params["order_id"])
            elif request.capability_name == "create_refund":
                output = client.create_refund(
                    order_id=params["order_id"],
                    amount=params["amount"],
                    reason=params.get("reason", ""),
                    full_refund=bool(params.get("full_refund", False)),
                )
            else:
                return ActionResult.failed(
                    StructuredError("UNKNOWN_CAPABILITY", request.capability_name)
                )
        except ChannelError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        except KeyError as exc:
            return ActionResult.failed(
                StructuredError("CHANNEL_INVALID_PARAMETER", f"缺少参数：{exc}")
            )
        return ActionResult.success(output)

    def observe(self) -> Observation:
        binding = self._registry._require(self._binding_id)
        return Observation(
            url=f"https://{binding.config['shop']}.myshopify.com",
            title=f"Shopify 渠道：{binding.config['shop']}",
            data={"shop": binding.config["shop"],
                  "apiVersion": binding.config.get("apiVersion"),
                  "status": binding.status},
        )

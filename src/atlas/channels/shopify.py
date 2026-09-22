# -*- coding: utf-8 -*-
"""Shopify Admin API 渠道客户端（docs/38 §1A；ADR T28）。

v1 操作：list_orders / get_order / create_refund / shop 探活。
token 由构造注入但绝不落日志；传输可注入 fake。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from atlas.channels.base import (
    DEFAULT_TIMEOUT_SECONDS,
    ChannelError,
    ChannelTransport,
    HttpChannelTransport,
)
from atlas.channels.webhooks import SUPPORTED_TOPICS

DEFAULT_API_VERSION = "2025-01"
API_VERSION_RE = re.compile(r"^\d{4}-\d{2}$")
SHOP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ORDER_STATUSES = ("open", "any", "closed")


def normalize_shop(raw: str) -> str:
    """归一化 Shopify 店铺标识：去协议/路径/.myshopify.com 后缀，仅留店铺名。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ChannelError("CHANNEL_INVALID_PARAMETER", "缺少店铺标识 shop")
    value = raw.strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0].split("?", 1)[0]
    if value.endswith(".myshopify.com"):
        value = value[: -len(".myshopify.com")]
    elif value.endswith(".myshopify.com."):
        value = value[: -len(".myshopify.com.")]
    if not SHOP_NAME_RE.match(value):
        raise ChannelError(
            "CHANNEL_INVALID_PARAMETER",
            "shop 必须为合法的 myshopify 店铺名（字母数字与连字符）",
        )
    return value


def _project_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order.get("id"),
        "name": order.get("name"),
        "email": order.get("email"),
        "financialStatus": order.get("financial_status"),
        "fulfillStatus": order.get("fulfillment_status"),
        "totalPrice": order.get("total_price"),
        "currency": order.get("currency"),
        "createdAt": order.get("created_at"),
    }


class ShopifyChannelClient:
    def __init__(
        self,
        shop: str,
        access_token: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        transport: ChannelTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        base_url: str | None = None,
    ) -> None:
        self.shop = normalize_shop(shop)
        if not isinstance(access_token, str) or not access_token:
            raise ChannelError("CHANNEL_UNAUTHORIZED", "缺少渠道访问令牌")
        if not API_VERSION_RE.match(api_version or ""):
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", f"非法 API 版本：{api_version}（应为 YYYY-MM）"
            )
        self._token = access_token
        self.api_version = api_version
        self._transport = transport or HttpChannelTransport()
        self._timeout = timeout
        self.base_url = base_url or (
            f"https://{self.shop}.myshopify.com/admin/api/{api_version}"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "X-Shopify-Access-Token": self._token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        resp = self._transport.request(
            method, url, headers=headers, json_body=json_body, timeout=self._timeout
        )
        if resp.status == 422:
            raise ChannelError(
                "CHANNEL_ALREADY_REGISTERED",
                "店铺侧已存在该回调注册（HTTP 422）",
                status_code=409,
            )
        if resp.status in (401, 403):
            raise ChannelError(
                "CHANNEL_UNAUTHORIZED", f"渠道鉴权失败（HTTP {resp.status}）"
            )
        if resp.status >= 400:
            raise ChannelError(
                "CHANNEL_UPSTREAM_FAILED", f"渠道上游错误 HTTP {resp.status}"
            )
        try:
            return json.loads(resp.text) if resp.text else {}
        except (ValueError, TypeError) as exc:
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "渠道响应不是合法 JSON") from exc

    def get_shop(self) -> dict[str, Any]:
        body = self._request("GET", "/shop.json")
        shop = body.get("shop")
        if not isinstance(shop, dict):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "shop 响应结构缺失")
        return {"id": shop.get("id"), "name": shop.get("name"), "domain": shop.get("domain")}

    def list_orders(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        if status not in ORDER_STATUSES:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER",
                f"status 必须为 {ORDER_STATUSES} 之一",
            )
        bounded = max(1, min(int(limit), 250))
        body = self._request(
            "GET", "/orders.json", params={"status": status, "limit": bounded}
        )
        orders = body.get("orders")
        if not isinstance(orders, list):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "orders 响应结构缺失")
        return [_project_order(o) for o in orders if isinstance(o, dict)]

    def _get_raw_order(self, order_id: str) -> dict[str, Any]:
        if not isinstance(order_id, str) or not order_id.strip():
            raise ChannelError("CHANNEL_INVALID_PARAMETER", "缺少 order_id")
        body = self._request("GET", f"/orders/{order_id.strip()}.json")
        order = body.get("order")
        if not isinstance(order, dict):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "order 响应结构缺失")
        return order

    def get_order(self, order_id: str) -> dict[str, Any]:
        return _project_order(self._get_raw_order(order_id))

    @staticmethod
    def _build_refund_line_items(order: dict[str, Any], full_refund: bool) -> list[dict[str, Any]]:
        items = order.get("line_items") or []
        return [
            {
                "line_item_id": li.get("id"),
                "quantity": li.get("quantity") if full_refund else 0,
                "restock_type": 0,
            }
            for li in items
            if isinstance(li, dict) and li.get("id") is not None
        ]

    def create_refund(
        self,
        order_id: str,
        amount: float,
        reason: str = "",
        full_refund: bool = False,
    ) -> dict[str, Any]:
        try:
            value = float(amount)
        except (TypeError, ValueError) as exc:
            raise ChannelError("CHANNEL_INVALID_PARAMETER", "amount 必须为数字") from exc
        if value <= 0:
            raise ChannelError("CHANNEL_INVALID_PARAMETER", "amount 必须为正数")
        order = self._get_raw_order(order_id)
        try:
            total = float(order.get("total_price"))
        except (TypeError, ValueError) as exc:
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "订单 total_price 非法") from exc
        if value > total:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", "退款金额不能超过订单总额"
            )
        note = (reason or "")[:200]
        payload = {
            "refund": {
                "note": note,
                "notify": False,
                "refund_line_items": self._build_refund_line_items(order, full_refund),
                "transactions": [{"kind": "refund", "amount": f"{value:.2f}"}],
            }
        }
        body = self._request(
            "POST", f"/orders/{order_id.strip()}/refunds.json", json_body=payload
        )
        refund = body.get("refund")
        if not isinstance(refund, dict):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "refund 响应结构缺失")
        return {"refundId": refund.get("id"), "status": refund.get("status"), "amount": value}

    def list_registered_webhooks(self, limit: int = 250) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 250))
        body = self._request("GET", "/webhooks.json", params={"limit": bounded})
        webhooks = body.get("webhooks")
        if not isinstance(webhooks, list):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "webhooks 响应结构缺失")
        return [
            {"remoteId": str(w.get("id")), "topic": w.get("topic"), "address": w.get("address")}
            for w in webhooks
            if isinstance(w, dict)
        ]

    def register_webhook(self, *, topic: str, address: str) -> dict[str, Any]:
        if topic not in SUPPORTED_TOPICS:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", f"不支持的 topic：{topic}"
            )
        if not isinstance(address, str) or not address.startswith("https://"):
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", "回调地址必须为 HTTPS 绝对 URL"
            )
        payload = {"webhook": {"topic": topic, "address": address, "format": "json"}}
        body = self._request("POST", "/webhooks.json", json_body=payload)
        webhook = body.get("webhook")
        if not isinstance(webhook, dict):
            raise ChannelError("CHANNEL_INVALID_RESPONSE", "webhook 响应结构缺失")
        return {
            "remoteId": str(webhook.get("id")),
            "topic": webhook.get("topic"),
            "address": webhook.get("address"),
        }

    def delete_registered_webhook(self, remote_id: str) -> bool:
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise ChannelError("CHANNEL_INVALID_PARAMETER", "缺少 remote_id")
        self._request("DELETE", f"/webhooks/{remote_id.strip()}.json")
        return True

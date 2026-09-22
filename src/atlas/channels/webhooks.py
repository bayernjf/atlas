# -*- coding: utf-8 -*-
"""Shopify 入站 webhook：HMAC 验签、信封、异步投递（docs/39 §1A，ADR T29）。

验签必须在 JSON 解析前于原始 body 上完成；秘密不落日志。投递经 M9 Router
resolve 钉版本，无已发布版本 warning 跳过；幂等环进程内 per-tenant。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from collections import deque
from typing import Callable, Mapping, Protocol

from pydantic import BaseModel

from atlas.channels.base import ChannelError
from atlas.routing.models import TriggerEvent

logger = logging.getLogger(__name__)

SUPPORTED_TOPICS = {"orders/create", "orders/updated", "refunds/create"}

SHOP_DOMAIN_HEADER = "x-shopify-shop-domain"
TOPIC_HEADER = "x-shopify-topic"
WEBHOOK_ID_HEADER = "x-shopify-webhook-id"
TRIGGERED_AT_HEADER = "x-shopify-triggered-at"
HMAC_HEADER = "x-shopify-hmac-sha256"

IDEMPOTENCY_RING_SIZE = 200
IDEMPOTENCY_TTL_SECONDS = 3600.0

Resolver = Callable[[str, str, TriggerEvent], int | None]
TriggerFn = Callable[[str, int, TriggerEvent, str], None]


class Auditor(Protocol):
    def __call__(self, action: str, tenant_id: str, metadata: dict) -> None: ...


def verify_shopify_hmac(raw_body: bytes, provided: str | None, client_secret: str) -> bool:
    """base64(HMAC-SHA256(client_secret, raw_body)) 恒定时间比对；不抛、不记秘密。"""
    if not provided:
        return False
    digest = hmac.new(client_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    try:
        return hmac.compare_digest(expected, provided)
    except Exception:  # pragma: no cover - compare_digest 仅在类型异常时
        return False


class WebhookEnvelope(BaseModel):
    shop_domain: str
    topic: str
    webhook_id: str
    triggered_at: str | None = None
    data: dict


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def build_envelope(headers: Mapping[str, str], data: dict) -> WebhookEnvelope:
    """从请求头＋已解析 JSON 构造信封；缺必需头 → WEBHOOK_MALFORMED(400)。"""
    shop_domain = _header(headers, SHOP_DOMAIN_HEADER)
    topic = _header(headers, TOPIC_HEADER)
    webhook_id = _header(headers, WEBHOOK_ID_HEADER)
    missing = [
        name
        for name, value in (
            ("X-Shopify-Shop-Domain", shop_domain),
            ("X-Shopify-Topic", topic),
            ("X-Shopify-Webhook-Id", webhook_id),
        )
        if not value
    ]
    if missing:
        raise ChannelError(
            "WEBHOOK_MALFORMED",
            f"缺少必需的 Webhook 请求头：{', '.join(missing)}",
            status_code=400,
        )
    return WebhookEnvelope(
        shop_domain=shop_domain,
        topic=topic,
        webhook_id=webhook_id,
        triggered_at=_header(headers, TRIGGERED_AT_HEADER),
        data=data,
    )


def build_trigger_event(envelope: WebhookEnvelope) -> TriggerEvent:
    return TriggerEvent(
        channel="webhook",
        payload={"topic": envelope.topic, "shop": envelope.shop_domain, "data": envelope.data},
    )


class _IdempotencyRing:
    """单租户 webhook id 幂等环：deque 保序、dict 记时间，惰性剔超 TTL 旧条。"""

    def __init__(self, maxsize: int, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._clock = clock
        self._order: deque[str] = deque()
        self._seen_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def duplicate(self, webhook_id: str) -> bool:
        now = self._clock()
        with self._lock:
            self._expire(now)
            if webhook_id in self._seen_at:
                return True
            self._seen_at[webhook_id] = now
            self._order.append(webhook_id)
            if len(self._order) > self._maxsize:
                oldest = self._order.popleft()
                self._seen_at.pop(oldest, None)
            return False

    def _expire(self, now: float) -> None:
        while self._order and now - self._seen_at[self._order[0]] >= self._ttl:
            oldest = self._order.popleft()
            self._seen_at.pop(oldest, None)


def _active_subscriptions(binding: dict, topic: str) -> list[dict]:
    raw = binding.get("webhook_subscriptions") or []
    return [
        sub
        for sub in raw
        if sub.get("topic") == topic and sub.get("enabled", True)
    ]


class WebhookDeliverer:
    """验签后的投递：幂等 → 订阅 → resolve 钉版本 → 后台触发；异常仅 warning。"""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        trigger: TriggerFn | None = None,
        auditor: Auditor | None = None,
        clock: Callable[[], float] = time.time,
        ring_size: int = IDEMPOTENCY_RING_SIZE,
        ring_ttl: float = IDEMPOTENCY_TTL_SECONDS,
    ) -> None:
        self._resolve = resolver
        self._trigger = trigger
        self._audit = auditor
        self._clock = clock
        self._ring_size = ring_size
        self._ring_ttl = ring_ttl
        self._rings: dict[str, _IdempotencyRing] = {}
        self._rings_lock = threading.Lock()

    def deliver(self, tenant_id: str, binding: dict, envelope: WebhookEnvelope) -> dict:
        ring = self._ring_for(tenant_id)
        if ring.duplicate(envelope.webhook_id):
            return {"duplicate": True}

        if self._audit is not None:
            try:
                self._audit(
                    f"channel.webhook_received:{envelope.topic}",
                    tenant_id,
                    {
                        "bindingId": binding.get("id"),
                        "webhookId": envelope.webhook_id,
                        "shop": envelope.shop_domain,
                    },
                )
            except Exception:
                logger.warning("webhook 审计写入失败", exc_info=True)

        subscriptions = _active_subscriptions(binding, envelope.topic)
        if envelope.topic not in SUPPORTED_TOPICS or not subscriptions:
            return {"ignored": True}

        event = build_trigger_event(envelope)
        for sub in subscriptions:
            graph_id = sub.get("graph_id")
            if not graph_id:
                continue
            try:
                version = (
                    self._resolve(graph_id, tenant_id, event)
                    if self._resolve is not None
                    else None
                )
            except Exception:
                logger.warning("webhook 路由解析失败 graph=%s", graph_id, exc_info=True)
                continue
            if version is None:
                logger.warning(
                    "webhook 订阅图无已发布版本，跳过 graph=%s topic=%s",
                    graph_id,
                    envelope.topic,
                )
                continue
            try:
                if self._trigger is not None:
                    self._trigger(graph_id, version, event, tenant_id)
            except Exception:
                logger.warning("webhook 触发图运行失败 graph=%s", graph_id, exc_info=True)
        return {"received": True}

    def _ring_for(self, tenant_id: str) -> _IdempotencyRing:
        with self._rings_lock:
            ring = self._rings.get(tenant_id)
            if ring is None:
                ring = _IdempotencyRing(self._ring_size, self._ring_ttl, self._clock)
                self._rings[tenant_id] = ring
            return ring

# -*- coding: utf-8 -*-
"""渠道绑定注册服务（docs/38 §1A/§1C；ADR T28）。"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from atlas.channels.base import (
    ChannelBinding,
    ChannelError,
    ChannelTransport,
    HttpChannelTransport,
)
from atlas.channels.memory import ChannelStore
from atlas.channels.shopify import (
    DEFAULT_API_VERSION,
    API_VERSION_RE,
    ShopifyChannelClient,
    normalize_shop,
)
from atlas.channels.webhooks import SUPPORTED_TOPICS
from atlas.connections.service import (
    ConnectionService,
    ConnectionServiceError,
)

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("shopify",)


class ChannelRegistry:
    def __init__(
        self,
        store: Any,
        *,
        tenant_id: str,
        connection_service: ConnectionService,
        transport: ChannelTransport | None = None,
        base_url: str | None = None,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._connections = connection_service
        self._transport = transport
        self._base_url = base_url

    def list(self) -> list[dict[str, Any]]:
        return [b.view() for b in self._store.list()]

    def _require(self, binding_id: str) -> ChannelBinding:
        binding = self._store.get(binding_id)
        if binding is None or binding.tenant_id != self._tenant_id:
            raise ChannelError("CHANNEL_NOT_BOUND", "渠道绑定不存在", status_code=404)
        return binding

    def get(self, binding_id: str) -> dict[str, Any]:
        return self._require(binding_id).view()

    def bind(
        self,
        provider: str,
        connection_id: str,
        config: dict[str, Any] | None,
        *,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        if provider not in SUPPORTED_PROVIDERS:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER",
                f"不支持的渠道类型：{provider}（当前支持 {SUPPORTED_PROVIDERS}）",
            )
        if not isinstance(connection_id, str) or not connection_id.strip():
            raise ChannelError("CHANNEL_INVALID_PARAMETER", "缺少 connectionId")
        connection_id = connection_id.strip()
        try:
            self._connections.get(connection_id)
        except ConnectionServiceError as exc:
            raise ChannelError(
                "CHANNEL_NOT_BOUND", "绑定的连接不存在", status_code=404
            ) from exc
        for existing in self._store.list():
            if existing.connection_id == connection_id:
                raise ChannelError(
                    "CHANNEL_ALREADY_BOUND",
                    "该连接已绑定渠道，请勿重复绑定",
                    status_code=409,
                )
        cfg = config if isinstance(config, dict) else {}
        shop = normalize_shop(cfg.get("shop", ""))
        api_version = cfg.get("apiVersion") or DEFAULT_API_VERSION
        if not API_VERSION_RE.match(str(api_version)):
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", f"非法 API 版本：{api_version}"
            )
        binding = ChannelBinding(
            id="",
            tenant_id=self._tenant_id,
            provider=provider,
            connection_id=connection_id,
            config={"shop": shop, "apiVersion": api_version},
            created_by=created_by,
        )
        self._store.create(binding)
        return binding.view()

    def client_for(self, binding: ChannelBinding) -> ShopifyChannelClient:
        token = self._connections.access_token_for(binding.connection_id)
        if token is None:
            binding.status = "error"
            binding.last_error = "绑定连接未完成授权或令牌不可用"
            self._store.save(binding)
            raise ChannelError(
                "CHANNEL_UNAUTHORIZED", "绑定连接未完成授权或令牌不可用"
            )
        return ShopifyChannelClient(
            binding.config["shop"],
            token,
            api_version=binding.config.get("apiVersion", DEFAULT_API_VERSION),
            transport=self._transport,
            base_url=self._base_url,
        )

    def test(self, binding_id: str) -> dict[str, Any]:
        binding = self._require(binding_id)
        try:
            client = self.client_for(binding)
            client.get_shop()
        except ChannelError as exc:
            binding.status = "error"
            binding.last_error = str(exc)
            self._store.save(binding)
            return {"ok": False, "status": "error", "reason": str(exc)}
        binding.status = "connected"
        binding.last_error = None
        self._store.save(binding)
        return {"ok": True, "status": "connected"}

    def delete(self, binding_id: str) -> bool:
        binding = self._require(binding_id)
        return self._store.delete(binding.id)

    def subscriptions(self, binding_id: str) -> list[dict[str, Any]]:
        return self._require(binding_id).view()["webhookSubscriptions"]

    def set_subscriptions(self, binding_id: str, subs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        binding = self._require(binding_id)
        binding.webhook_subscriptions = subs
        self._store.save(binding)
        return binding.view()["webhookSubscriptions"]

    @staticmethod
    def _hook_address(binding_id: str) -> str:
        public_url = os.getenv("ATLAS_PUBLIC_URL", "http://localhost:5174").rstrip("/")
        return f"{public_url}/api/channels/hooks/shopify/{binding_id}"

    def _fail_binding(self, binding: ChannelBinding, exc: ChannelError) -> None:
        binding.status = "error"
        binding.last_error = str(exc)
        self._store.save(binding)

    def remote_webhooks(self, binding_id: str) -> list[dict[str, Any]]:
        binding = self._require(binding_id)
        client = self.client_for(binding)
        try:
            return client.list_registered_webhooks()
        except ChannelError as exc:
            if exc.code == "CHANNEL_UNAUTHORIZED":
                self._fail_binding(binding, exc)
            raise

    def register_remote(self, binding_id: str, topic: str) -> dict[str, Any]:
        binding = self._require(binding_id)
        if topic not in SUPPORTED_TOPICS:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", f"不支持的 Webhook topic：{topic}",
                status_code=422,
            )
        address = self._hook_address(binding_id)
        if not address.startswith("https://"):
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER",
                "回调地址必须为 HTTPS，请配置 ATLAS_PUBLIC_URL",
                status_code=422,
            )
        client = self.client_for(binding)
        try:
            return client.register_webhook(topic=topic, address=address)
        except ChannelError as exc:
            if exc.code == "CHANNEL_UNAUTHORIZED":
                self._fail_binding(binding, exc)
            raise

    def unregister_remote(self, binding_id: str, topic: str) -> bool:
        binding = self._require(binding_id)
        if topic not in SUPPORTED_TOPICS:
            raise ChannelError(
                "CHANNEL_INVALID_PARAMETER", f"不支持的 Webhook topic：{topic}",
                status_code=422,
            )
        address = self._hook_address(binding_id)
        client = self.client_for(binding)
        try:
            registered = client.list_registered_webhooks()
        except ChannelError as exc:
            if exc.code == "CHANNEL_UNAUTHORIZED":
                self._fail_binding(binding, exc)
            raise
        for item in registered:
            if item.get("topic") == topic and item.get("address") == address:
                client.delete_registered_webhook(item["remoteId"])
                return True
        return False


def build_channel_registry(
    connection_service: ConnectionService,
    *,
    tenant_id: str,
    store: Any | None = None,
) -> ChannelRegistry:
    """每租户一个：绑定 store（默认内存，PG 档注入 PgChannelStore）+ 共享出向守卫的 HTTP 传输。

    ATLAS_SHOPIFY_ADMIN_BASE_URL 设置时（仅开发/测试缝，docs/41 §D）：Admin base 指向
    同进程 mock，传输显式不带 EgressGuard（localhost 恒被守卫 denylist 拦，无法 allowlist）；
    未设置时生产路径原样。
    """
    admin_base = os.getenv("ATLAS_SHOPIFY_ADMIN_BASE_URL")
    if admin_base:
        transport: ChannelTransport = HttpChannelTransport()
        base_url = admin_base.rstrip("/")
    else:
        from atlas.connections.service import get_egress_guard

        transport = HttpChannelTransport(egress=get_egress_guard())
        base_url = None
    return ChannelRegistry(
        store if store is not None else ChannelStore(),
        tenant_id=tenant_id,
        connection_service=connection_service,
        transport=transport,
        base_url=base_url,
    )

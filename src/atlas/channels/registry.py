# -*- coding: utf-8 -*-
"""渠道绑定注册服务（docs/38 §1A/§1C；ADR T28）。"""

from __future__ import annotations

import logging
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
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._connections = connection_service
        self._transport = transport

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


def build_channel_registry(
    connection_service: ConnectionService,
    *,
    tenant_id: str,
    store: Any | None = None,
) -> ChannelRegistry:
    """每租户一个：绑定 store（默认内存，PG 档注入 PgChannelStore）+ 共享出向守卫的 HTTP 传输。"""
    from atlas.connections.service import get_egress_guard

    transport = HttpChannelTransport(egress=get_egress_guard())
    return ChannelRegistry(
        store if store is not None else ChannelStore(),
        tenant_id=tenant_id,
        connection_service=connection_service,
        transport=transport,
    )

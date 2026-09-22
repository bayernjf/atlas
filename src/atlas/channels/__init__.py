"""真实渠道适配层（docs/38，ADR T28）。

在 T4 generic OAuth2 连接之上提供平台特定渠道能力（v1：Shopify Admin API）。
形状权威＝docs/38；本包不持久化明文 token（调用时经 connection_service 现解密）。
"""

from __future__ import annotations

from atlas.channels.base import (
    ChannelBinding,
    ChannelError,
    ChannelTransport,
    HttpChannelTransport,
    TransportResponse,
)
from atlas.channels.memory import ChannelStore
from atlas.channels.registry import ChannelRegistry
from atlas.channels.shopify import ShopifyChannelClient, normalize_shop

__all__ = [
    "ChannelBinding",
    "ChannelError",
    "ChannelStore",
    "ChannelRegistry",
    "ChannelTransport",
    "HttpChannelTransport",
    "ShopifyChannelClient",
    "TransportResponse",
    "normalize_shop",
]

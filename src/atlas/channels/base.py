# -*- coding: utf-8 -*-
"""渠道层基础类型：错误、传输协议、绑定模型（docs/38 §1A/§3）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from atlas.security.egress import EgressDenied, EgressGuard

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0


class ChannelError(Exception):
    """渠道层结构化错误。

    codes: CHANNEL_NOT_BOUND / CHANNEL_UNAUTHORIZED / CHANNEL_UPSTREAM_FAILED /
    CHANNEL_INVALID_RESPONSE / CHANNEL_INVALID_PARAMETER / CHANNEL_ALREADY_BOUND
    """

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class TransportResponse:
    status: int
    headers: dict[str, str]
    text: str


class ChannelTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None,
        timeout: float,
    ) -> TransportResponse: ...


class HttpChannelTransport:
    """真实 httpx 出站传输；每次请求先过 EgressGuard，禁止重定向。"""

    def __init__(self, egress: EgressGuard | None = None) -> None:
        self._egress = egress if egress is not None else EgressGuard.from_env()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None,
        timeout: float,
    ) -> TransportResponse:
        try:
            self._egress.check(url)
        except EgressDenied as exc:
            raise ChannelError(exc.code, f"出向校验未通过：{exc}") from exc
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ChannelError("CHANNEL_UPSTREAM_FAILED", f"渠道请求失败：{exc}") from exc
        return TransportResponse(
            status=resp.status_code,
            headers={k: v for k, v in resp.headers.items()},
            text=resp.text,
        )


@dataclass
class ChannelBinding:
    id: str
    tenant_id: str
    provider: str
    connection_id: str
    config: dict[str, Any] = field(default_factory=dict)
    status: str = "connected"  # connected | error
    last_error: str | None = None
    created_by: str | None = None
    created_at: str | None = None  # UTC iso
    updated_at: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "connectionId": self.connection_id,
            "config": {"shop": self.config.get("shop"),
                       "apiVersion": self.config.get("apiVersion")},
            "status": self.status,
            "lastError": self.last_error,
            "createdBy": self.created_by,
            "createdAt": self.created_at,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

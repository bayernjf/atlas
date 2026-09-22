# -*- coding: utf-8 -*-
"""generic OAuth2 授权码流程纯逻辑（docs/35 §4.3，T4；平台无关、不触网）。

- state：base64url(json).base64url(HMAC-SHA256)，防 CSRF，校验签名/过期/租户/连接；
- authorize URL 构造；授权码换 token、refresh token 刷新（HTTP POST 全部可注入 post_form）；
- TokenBundle 与过期判定（60s 提前量）。

本模块不 import httpx、不读 store、不做加密落库：调用方（ConnectionService）负责解密
client_secret、注入 post_form、把 token 经 SecretProvider 加密落库。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600
TOKEN_TIMEOUT_SECONDS = 10.0
EXPIRY_SKEW_SECONDS = 60

# 仅在未配置 ATLAS_MASTER_KEY 时用于 state 签名的固定 dev 密钥。
# state 只防 CSRF、不是凭据；凭据加密另走 SecretProvider。生产必须配置主密钥。
_DEV_STATE_SECRET = "atlas-dev-oauth-state-insecure-do-not-use-in-prod"


class OAuthStateError(Exception):
    """state 缺失/签名错误/过期/租户或连接不匹配。"""


class OAuthTokenError(Exception):
    """token 端点非 2xx、响应缺 access_token 或无法解析。"""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None


def resolve_state_secret() -> str:
    """state HMAC 密钥：优先 ATLAS_MASTER_KEY；缺失用固定 dev 密钥并 warning（仅一次）。"""
    master = os.getenv("ATLAS_MASTER_KEY", "").strip()
    if master:
        return master
    if not getattr(resolve_state_secret, "_warned", False):
        logger.warning(
            "ATLAS_MASTER_KEY 未配置：OAuth state 使用固定 dev 密钥签名，仅限本地开发；"
            "生产必须配置 32 字节主密钥。"
        )
        resolve_state_secret._warned = True  # type: ignore[attr-defined]
    return _DEV_STATE_SECRET


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def build_state(tenant_id: str, conn_id: str, *, secret: str, ttl_seconds: int = STATE_TTL_SECONDS) -> str:
    """生成 state：base64url(json{tenant,conn,nonce,iat,exp}).base64url(hmac)。"""
    now = int(datetime.now(timezone.utc).timestamp())
    body = {
        "tenant": tenant_id,
        "conn": conn_id,
        "nonce": _secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    payload_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_state(
    token: str,
    *,
    secret: str,
    expected_tenant: str,
    expected_conn: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """校验 state 签名/过期/租户/连接，成功返回 body dict，失败抛 OAuthStateError。"""
    if not isinstance(token, str) or token.count(".") != 1:
        raise OAuthStateError("state 格式非法")
    payload_b64, sig = token.split(".", 1)
    expected_sig = _sign(payload_b64, secret)
    if not hmac.compare_digest(sig, expected_sig):
        raise OAuthStateError("state 签名不匹配")
    try:
        body = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise OAuthStateError("state 载荷无法解析") from exc
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    if int(body.get("exp", 0)) < now_ts:
        raise OAuthStateError("state 已过期")
    if body.get("tenant") != expected_tenant or body.get("conn") != expected_conn:
        raise OAuthStateError("state 与租户或连接不匹配")
    return body


def build_authorize_url(
    *, auth_url: str, client_id: str, redirect_uri: str, scopes: list[str], state: str
) -> str:
    """构造授权端点 URL（response_type=code；scope 以空格分隔，空则省略）。"""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    separator = "&" if "?" in auth_url else "?"
    return f"{auth_url}{separator}{urlencode(params)}"


def _parse_token_response(response: object) -> TokenBundle:
    status = getattr(response, "status_code", None)
    if not isinstance(status, int) or not 200 <= status < 300:
        text = getattr(response, "text", "") or ""
        raise OAuthTokenError(f"token 端点返回非 2xx：{status} {text[:200]}")
    try:
        data = response.json()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        raise OAuthTokenError("token 端点响应不是合法 JSON") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise OAuthTokenError("token 端点响应缺少 access_token")
    expires_in = data.get("expires_in")
    try:
        expires_in_int = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in_int = None
    return TokenBundle(
        access_token=str(data["access_token"]),
        refresh_token=str(data["refresh_token"]) if data.get("refresh_token") else None,
        token_type=str(data["token_type"]) if data.get("token_type") else None,
        expires_in=expires_in_int,
    )


def exchange_code(
    *,
    token_url: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    post_form,
    timeout: float = TOKEN_TIMEOUT_SECONDS,
) -> TokenBundle:
    """授权码换 token（grant_type=authorization_code，凭据走 form body）。"""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        response = post_form(token_url, data=data, timeout=timeout)
    except OAuthTokenError:
        raise
    except Exception as exc:  # noqa: BLE001 网络/超时统一折算
        raise OAuthTokenError(f"请求 token 端点失败：{exc}") from exc
    return _parse_token_response(response)


def refresh_tokens(
    *,
    token_url: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    post_form,
    timeout: float = TOKEN_TIMEOUT_SECONDS,
) -> TokenBundle:
    """刷新 token（grant_type=refresh_token）。"""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        response = post_form(token_url, data=data, timeout=timeout)
    except OAuthTokenError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OAuthTokenError(f"刷新 token 请求失败：{exc}") from exc
    return _parse_token_response(response)


def is_expired(expires_at: str | None, *, now: datetime | None = None,
               skew_seconds: int = EXPIRY_SKEW_SECONDS) -> bool:
    """过期判定（留 60s 提前量）；expires_at 为空视为不过期。"""
    if not expires_at:
        return False
    try:
        raw = expires_at.replace("Z", "+00:00") if expires_at.endswith("Z") else expires_at
        expires = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference >= expires - timedelta(seconds=skew_seconds)


def compute_expires_at(expires_in: int | None, *, now: datetime | None = None) -> str | None:
    """now + expires_in（秒）的 UTC iso；expires_in 为空返回 None（视为不过期）。"""
    if expires_in is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference + timedelta(seconds=int(expires_in))).isoformat()

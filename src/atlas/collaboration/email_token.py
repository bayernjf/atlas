# -*- coding: utf-8 -*-
"""邮件一键决策 capability token（docs/36 §3，审批闭环批 A 包）。

形状与 connections/oauth.py 的 state 同构：base64url(json).base64url(HMAC-SHA256)。
token 是不记名凭证：持有人在有效期内可查看审批并提交决策，故须短时效、绑定租户与
审批 token；验签失败统一按失效处理（端点层统一 404，不区分原因）。

纯 stdlib、不触网、时钟可注入。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

from atlas.security.bootstrap import read_env_profile

logger = logging.getLogger(__name__)

# 仅在未配置签名密钥时使用的固定 dev 密钥（与 oauth dev state key 同策略）。
_DEV_TOKEN_SECRET = "atlas-dev-approval-email-insecure-do-not-use-in-prod"

MAX_TTL_SECONDS = 3600
EXPIRY_GRACE_SECONDS = 300


class EmailTokenError(Exception):
    """token 缺失/签名错误/过期；子类细分，端点层统一映射 404。"""


class TokenMalformed(EmailTokenError):
    """分段数不对或载荷无法解析。"""


class TokenBadSignature(EmailTokenError):
    """签名不匹配。"""


class TokenExpired(EmailTokenError):
    """签名合法但已过有效期。"""


def resolve_token_secret() -> str:
    """签名密钥：优先 ATLAS_APPROVAL_HMAC_SECRET，其次 ATLAS_MASTER_KEY；都缺用固定 dev 密钥并 warning。"""
    configured = os.getenv("ATLAS_APPROVAL_HMAC_SECRET", "").strip()
    if configured:
        return configured
    master = os.getenv("ATLAS_MASTER_KEY", "").strip()
    if master:
        return master
    if read_env_profile() == "prod":
        raise RuntimeError(
            "ATLAS_ENV=prod 下必须配置 ATLAS_APPROVAL_HMAC_SECRET 或 "
            "ATLAS_MASTER_KEY（≥32 字节），邮件决策签名拒绝使用 dev 密钥"
        )
    if not getattr(resolve_token_secret, "_warned", False):
        logger.warning(
            "ATLAS_APPROVAL_HMAC_SECRET 未配置：邮件决策链接使用固定 dev 密钥签名，"
            "仅限本地开发；生产必须配置 32 字节随机密钥。"
        )
        resolve_token_secret._warned = True  # type: ignore[attr-defined]
    return _DEV_TOKEN_SECRET


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


@dataclass
class TokenIssuer:
    """签发/验签：secret 与时钟在构造期注入（默认取环境密钥与真实时钟）。"""

    secret: str | None = None
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if self.secret is None:
            self.secret = resolve_token_secret()

    def issue(
        self,
        tenant_id: str,
        approval_token: str,
        timeout_seconds: float,
    ) -> str:
        now = int(self.clock())
        ttl = min(int(timeout_seconds), MAX_TTL_SECONDS) + EXPIRY_GRACE_SECONDS
        body = {
            "v": 1,
            "tenant": tenant_id,
            "at": approval_token,
            "iat": now,
            "exp": now + ttl,
        }
        payload_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        return f"{payload_b64}.{_sign(payload_b64, self.secret or '')}"

    def verify(self, token: str) -> dict[str, object]:
        """成功返回 body dict；格式/载荷错抛 TokenMalformed，签名错抛 TokenBadSignature，过期抛 TokenExpired。"""
        if not isinstance(token, str) or token.count(".") != 1:
            raise TokenMalformed("token 格式非法")
        payload_b64, sig = token.split(".", 1)
        expected_sig = _sign(payload_b64, self.secret or "")
        if not hmac.compare_digest(sig, expected_sig):
            raise TokenBadSignature("token 签名不匹配")
        try:
            body = json.loads(_b64url_decode(payload_b64))
        except Exception as exc:  # noqa: BLE001
            raise TokenMalformed("token 载荷无法解析") from exc
        if int(body.get("exp", 0)) < self.clock():
            raise TokenExpired("token 已过期")
        return body

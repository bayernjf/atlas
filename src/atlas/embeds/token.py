# -*- coding: utf-8 -*-
"""网页嵌入只读面短链 token（docs/133 打包 BQ＝D76；docs/127 里程碑 F）。

**不新建鉴权形态**（BQ-D1）：与审批邮件链接（`collaboration/email_token.py`）同形——
`base64url(json).base64url(HMAC-SHA256)`，密钥链同一处（`ATLAS_APPROVAL_HMAC_SECRET`
→ `ATLAS_MASTER_KEY` → dev 固定密钥），短时效，验签失败统一按失效处理（端点层 404）。

载荷换为 `{v, tenant, graph, iat, exp}`（嵌入面向的是一次只读视图而非审批决策）。
token 是不记名凭证：持有人在有效期内可读嵌入面，故须绑定租户与 graph、短时效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Callable

from atlas.collaboration.email_token import resolve_token_secret

MAX_TTL_SECONDS = 86400  # 嵌入配置表单门控上限（与 store.MAX_TOKEN_TTL_SECONDS 一致）
EXPIRY_GRACE_SECONDS = 300


class EmbedTokenError(Exception):
    """token 缺失/签名错误/过期；子类细分，端点层统一映射 404。"""


class EmbedTokenMalformed(EmbedTokenError):
    """分段数不对或载荷无法解析。"""


class EmbedTokenBadSignature(EmbedTokenError):
    """签名不匹配。"""


class EmbedTokenExpired(EmbedTokenError):
    """签名合法但已过有效期。"""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


@dataclass
class EmbedTokenIssuer:
    """签发/验签嵌入短链：secret 与时钟构造期注入（默认环境密钥与真实时钟）。"""

    secret: str | None = None
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if self.secret is None:
            self.secret = resolve_token_secret()

    def issue(self, tenant_id: str, graph_id: str, ttl_seconds: float) -> str:
        now = int(self.clock())
        ttl = min(int(ttl_seconds), MAX_TTL_SECONDS) + EXPIRY_GRACE_SECONDS
        body: dict[str, object] = {
            "v": 1,
            "tenant": tenant_id,
            "graph": graph_id,
            "iat": now,
            "exp": now + ttl,
        }
        payload_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        return f"{payload_b64}.{_sign(payload_b64, self.secret or '')}"

    def verify(self, token: str) -> dict[str, object]:
        """成功返回 body dict；格式/载荷错抛 EmbedTokenMalformed，签名错抛
        EmbedTokenBadSignature，过期抛 EmbedTokenExpired（端点层统一 404）。"""
        if not isinstance(token, str) or token.count(".") != 1:
            raise EmbedTokenMalformed("token 格式非法")
        payload_b64, sig = token.split(".", 1)
        expected_sig = _sign(payload_b64, self.secret or "")
        if not hmac.compare_digest(sig, expected_sig):
            raise EmbedTokenBadSignature("token 签名不匹配")
        try:
            body = json.loads(_b64url_decode(payload_b64))
        except Exception as exc:  # noqa: BLE001
            raise EmbedTokenMalformed("token 载荷无法解析") from exc
        if int(body.get("exp", 0)) < self.clock():
            raise EmbedTokenExpired("token 已过期")
        return body

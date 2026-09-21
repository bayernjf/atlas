"""会话令牌存储（04 §5.14，docs/31 §4）：sess-<uuid hex>，单锁，绝对 TTL。

TTL 由 ATLAS_SESSION_TTL_HOURS 配置（缺省 12，合法 1-168）；过期 token 惰性删除。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass

from .principals import Principal

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 12.0
MIN_TTL_HOURS = 1.0
MAX_TTL_HOURS = 168.0


def session_ttl_seconds() -> float:
    raw = os.environ.get("ATLAS_SESSION_TTL_HOURS")
    if raw is None or not raw.strip():
        return DEFAULT_TTL_HOURS * 3600
    try:
        hours = float(raw)
    except ValueError:
        logger.warning("invalid ATLAS_SESSION_TTL_HOURS=%r, falling back to 12h", raw)
        return DEFAULT_TTL_HOURS * 3600
    if not MIN_TTL_HOURS <= hours <= MAX_TTL_HOURS:
        logger.warning("ATLAS_SESSION_TTL_HOURS=%s out of range 1-168, falling back to 12h", raw)
        return DEFAULT_TTL_HOURS * 3600
    return hours * 3600


@dataclass
class _SessionEntry:
    principal: Principal
    expires_at: float


class SessionStore:
    def __init__(self) -> None:
        self._tokens: dict[str, _SessionEntry] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        principal: Principal,
        *,
        now: float | None = None,
        ttl_seconds: float | None = None,
    ) -> str:
        token = f"sess-{uuid.uuid4().hex}"
        current = time.time() if now is None else now
        ttl = session_ttl_seconds() if ttl_seconds is None else ttl_seconds
        with self._lock:
            self._tokens[token] = _SessionEntry(
                principal=principal, expires_at=current + ttl
            )
        return token

    def principal_for_token(self, token: str | None, *, now: float | None = None) -> Principal | None:
        if not token:
            return None
        current = time.time() if now is None else now
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return None
            if entry.expires_at <= current:
                del self._tokens[token]
                return None
            principal = entry.principal
        return principal.model_copy(deep=True)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_for_user(self, tenant_id: str, username: str, *, keep_token: str | None = None) -> None:
        with self._lock:
            for token, entry in list(self._tokens.items()):
                if token == keep_token:
                    continue
                if entry.principal.tenant_id == tenant_id and entry.principal.username == username:
                    del self._tokens[token]

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()

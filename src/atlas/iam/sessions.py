"""进程内会话令牌存储（04 §5.14）：sess-<uuid hex>，单锁，重启即失。"""

from __future__ import annotations

import threading
import uuid

from .principals import Principal


class SessionStore:
    def __init__(self) -> None:
        self._tokens: dict[str, Principal] = {}
        self._lock = threading.Lock()

    def issue(self, principal: Principal) -> str:
        token = f"sess-{uuid.uuid4().hex}"
        with self._lock:
            self._tokens[token] = principal
        return token

    def principal_for_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._lock:
            principal = self._tokens.get(token)
        return principal.model_copy(deep=True) if principal else None

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()

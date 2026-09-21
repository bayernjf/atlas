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

    def revoke_for_user(self, tenant_id: str, username: str, *, keep_token: str | None = None) -> None:
        with self._lock:
            for token, principal in list(self._tokens.items()):
                if token == keep_token:
                    continue
                if principal.tenant_id == tenant_id and principal.username == username:
                    del self._tokens[token]

    def reset(self) -> None:
        with self._lock:
            self._tokens.clear()

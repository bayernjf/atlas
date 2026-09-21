"""账号存储与幂等 seeder（ADR T25，docs/31 §2）。

UserStore 为全局单例（同 SessionStore，不分租户）：登录按 username 全局查。
禁用/重置/改密成功经绑定的会话存储吊销对应用户的会话。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from .passwords import hash_password
from .principals import Role, SEED_USERS, TenantUser


class UserAccount(BaseModel):
    tenant_id: str
    username: str
    password_hash: str
    display_name: str
    role: Role
    status: Literal["active", "disabled"] = "active"
    created_at: str
    updated_at: str


class UserExists(Exception):
    """同租户用户名已存在。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserStore:
    def __init__(self, session_store: Any | None = None) -> None:
        self._users: dict[tuple[str, str], UserAccount] = {}
        self._lock = threading.Lock()
        self._session_store = session_store

    def bind_session_store(self, session_store: Any) -> None:
        self._session_store = session_store

    def seed(self, users: list[TenantUser] | None = None) -> int:
        """幂等播种：仅插入缺失行，已存在账号（含已改密）零改动；返回新插入数。"""
        inserted = 0
        for user in users or SEED_USERS:
            with self._lock:
                if (user.tenant_id, user.username) in self._users:
                    continue
                now = _now_iso()
                account = UserAccount(
                    tenant_id=user.tenant_id,
                    username=user.username,
                    password_hash=hash_password(user.password),
                    display_name=user.display_name,
                    role=user.role,
                    created_at=now,
                    updated_at=now,
                )
                self._users[(user.tenant_id, user.username)] = account
            inserted += 1
        return inserted

    def get(self, tenant_id: str, username: str) -> UserAccount | None:
        with self._lock:
            account = self._users.get((tenant_id, username))
        return account.model_copy(deep=True) if account else None

    def get_by_username(self, username: str) -> UserAccount | None:
        with self._lock:
            account = next(
                (account for account in self._users.values() if account.username == username),
                None,
            )
        return account.model_copy(deep=True) if account else None

    def list(self, tenant_id: str) -> list[UserAccount]:
        with self._lock:
            accounts = [
                account.model_copy(deep=True)
                for account in self._users.values()
                if account.tenant_id == tenant_id
            ]
        return sorted(accounts, key=lambda account: account.username)

    def create(
        self,
        *,
        tenant_id: str,
        username: str,
        password: str,
        display_name: str,
        role: Role,
    ) -> UserAccount:
        with self._lock:
            if (tenant_id, username) in self._users:
                raise UserExists(username)
            now = _now_iso()
            account = UserAccount(
                tenant_id=tenant_id,
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                role=role,
                created_at=now,
                updated_at=now,
            )
            self._users[(tenant_id, username)] = account
        return account.model_copy(deep=True)

    def update(
        self,
        tenant_id: str,
        username: str,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        status: Literal["active", "disabled"] | None = None,
    ) -> UserAccount | None:
        with self._lock:
            account = self._users.get((tenant_id, username))
            if account is None:
                return None
            if display_name is not None:
                account.display_name = display_name
            if role is not None:
                account.role = role
            if status is not None:
                account.status = status
            account.updated_at = _now_iso()
            result = account.model_copy(deep=True)
        if status == "disabled" and self._session_store is not None:
            self._session_store.revoke_for_user(tenant_id, username)
        return result

    def set_password(self, tenant_id: str, username: str, password: str) -> UserAccount | None:
        with self._lock:
            account = self._users.get((tenant_id, username))
            if account is None:
                return None
            account.password_hash = hash_password(password)
            account.updated_at = _now_iso()
            result = account.model_copy(deep=True)
        if self._session_store is not None:
            self._session_store.revoke_for_user(tenant_id, username)
        return result

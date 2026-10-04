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
    # docs/95 打包 AV：NULL ＝ 口令从未经本人之手（引导播种/admin 建号/admin 重置）。
    password_rotated_at: str | None = None


class UserExists(Exception):
    """同租户用户名已存在。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserStore:
    def __init__(self, session_store: Any | None = None) -> None:
        self._users: dict[tuple[str, str], UserAccount] = {}
        self._lock = threading.RLock()  # 可重入：create/seed 内复用 get_by_username
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
                existing = self.get_by_username(user.username)
                if existing is not None and existing.tenant_id != user.tenant_id:
                    raise ValueError(
                        f"用户名 {user.username!r} 已被租户 {existing.tenant_id} 占用，"
                        "用户名全局唯一（docs/64 J-1d）"
                    )
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
            # docs/64 J-1d：确定性选择（字典序最小租户），不再依赖插入序。
            account = min(
                (account for account in self._users.values() if account.username == username),
                key=lambda a: (a.username, a.tenant_id),
                default=None,
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
            existing = self.get_by_username(username)
            if existing is not None and existing.tenant_id != tenant_id:
                # docs/64 J-1d：用户名全局唯一，跨租户重名拒绝（防登录歧义）。
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

    def set_password(
        self,
        tenant_id: str,
        username: str,
        password: str,
        *,
        keep_token: str | None = None,
        by_owner: bool = False,
    ) -> UserAccount | None:
        """改口令并盖轮换位。

        `by_owner` 为真（本人经 `/api/auth/change-password`）才盖章；admin 重置留 NULL——
        口令由第三方设定就不算「本人选定的口令」，强制位因此重新生效（docs/95 §3）。
        """
        with self._lock:
            account = self._users.get((tenant_id, username))
            if account is None:
                return None
            account.password_hash = hash_password(password)
            account.password_rotated_at = _now_iso() if by_owner else None
            account.updated_at = _now_iso()
            result = account.model_copy(deep=True)
        if self._session_store is not None:
            self._session_store.revoke_for_user(tenant_id, username, keep_token=keep_token)
        return result

"""种子租户/账号、Principal 与三角色能力矩阵（04 §5.14）。

种子账号登录经 ADR T25（docs/31）的 scrypt 哈希校验；持久化账号随 iam_users。
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from .passwords import hash_password, verify_password

Capability = Literal["read", "operate", "administer"]


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Tenant(BaseModel):
    id: str
    name: str


class TenantUser(BaseModel):
    tenant_id: str
    username: str
    password: str
    display_name: str
    role: Role


class Principal(BaseModel):
    tenant_id: str
    tenant_name: str
    username: str
    display_name: str
    role: Role


SEED_TENANTS: dict[str, Tenant] = {
    "t1": Tenant(id="t1", name="演示企业 A"),
    "t2": Tenant(id="t2", name="演示企业 B"),
}

SEED_USERS: list[TenantUser] = [
    TenantUser(tenant_id="t1", username="admin-a", password="admin123",
               display_name="A 企业管理员", role=Role.ADMIN),
    TenantUser(tenant_id="t1", username="operator-a", password="operator123",
               display_name="A 企业运营", role=Role.OPERATOR),
    TenantUser(tenant_id="t1", username="viewer-a", password="viewer123",
               display_name="A 企业访客", role=Role.VIEWER),
    TenantUser(tenant_id="t2", username="admin-b", password="admin123",
               display_name="B 企业管理员", role=Role.ADMIN),
]

_USERNAME_INDEX: dict[str, TenantUser] = {user.username: user for user in SEED_USERS}

_SEED_HASHES: dict[str, str] = {}
_SEED_HASH_LOCK = threading.Lock()


def _seed_hash(username: str) -> str:
    with _SEED_HASH_LOCK:
        stored = _SEED_HASHES.get(username)
        if stored is None:
            stored = hash_password(_USERNAME_INDEX[username].password)
            _SEED_HASHES[username] = stored
        return stored


ROLE_RANK: dict[Role, int] = {Role.VIEWER: 1, Role.OPERATOR: 2, Role.ADMIN: 3}

_CAPABILITY_RANK: dict[str, int] = {"read": 1, "operate": 2, "administer": 3}


def authenticate(username: str, password: str) -> Principal | None:
    user = _USERNAME_INDEX.get(username)
    if user is None or not verify_password(password, _seed_hash(username)):
        return None
    tenant = SEED_TENANTS[user.tenant_id]
    return Principal(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def can(role: Role, capability: Capability) -> bool:
    return ROLE_RANK[role] >= _CAPABILITY_RANK[capability]

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


def seed_plan_for_profile() -> list[TenantUser]:
    """按环境档位给出**该播哪些账号**（docs/66 打包 L，补 docs/64 J-1c 的进门缺口）。

    - dev/test：原样返回 `SEED_USERS`，演示与测试形态逐键不变。
    - prod：只播各租户的 ADMIN，口令取 `ATLAS_ADMIN_BOOTSTRAP_PASSWORD`（由
      `security.bootstrap.prod_bootstrap_password()` 校验，缺失即 raise＝拒绝启动）。
      operator/viewer 不播——prod 里它们由首位 admin 经 `POST /api/users` 建立。

    生产环境绝不播仓库内明文口令：那四套口令谁都登不进（`iam/deps.py` 的
    `AUTH_SEED_CREDENTIAL` 拒绝），播了等于没有。
    """
    from atlas.security.bootstrap import prod_bootstrap_password

    bootstrap = prod_bootstrap_password()
    if bootstrap is None:
        return SEED_USERS
    return [
        TenantUser(
            tenant_id=user.tenant_id,
            username=user.username,
            password=bootstrap,
            display_name=user.display_name,
            role=user.role,
        )
        for user in SEED_USERS
        if user.role is Role.ADMIN
    ]

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

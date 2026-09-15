"""多租户与权限 v1（04 §5.14）：种子账号、进程内 sess-token、三角色 RBAC、按租户服务注册表。"""

from .deps import get_principal, require, services_for, session_store, tenant_registry
from .principals import (
    SEED_TENANTS,
    SEED_USERS,
    Principal,
    Role,
    Tenant,
    TenantUser,
    authenticate,
    can,
)
from .registry import TenantRegistry, TenantServices
from .sessions import SessionStore

__all__ = [
    "Role",
    "Tenant",
    "TenantUser",
    "Principal",
    "SEED_TENANTS",
    "SEED_USERS",
    "authenticate",
    "can",
    "SessionStore",
    "TenantServices",
    "TenantRegistry",
    "get_principal",
    "require",
    "services_for",
    "session_store",
    "tenant_registry",
]

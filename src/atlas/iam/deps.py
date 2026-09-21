"""FastAPI 鉴权依赖（04 §5.14）：Bearer 会话 → Principal → 角色白名单 → 租户服务。"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request

from .passwords import verify_password
from .principals import Capability, Principal, SEED_TENANTS, can
from .registry import TenantRegistry, TenantServices
from .sessions import SessionStore
from .throttle import LoginThrottle


def select_session_store():
    # docs/30 §4（ADR T24）：PG 档会话落 iam_sessions 表，进程重启/多实例共享令牌；
    # 密码哈希/JWT 仍属生产鉴权批次，不在此列。
    if os.environ.get("ATLAS_STORAGE_BACKEND", "memory") == "pg":
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().session_store()
    return SessionStore()


def select_user_store():
    # ADR T25（docs/31 §2）：PG 档账号落 iam_users；内存档惰性播种。
    if os.environ.get("ATLAS_STORAGE_BACKEND", "memory") == "pg":
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().user_store()
    from .accounts import UserStore

    return UserStore()


session_store = select_session_store()
user_store = select_user_store()
user_store.bind_session_store(session_store)
if os.environ.get("ATLAS_STORAGE_BACKEND", "memory") != "pg":
    user_store.seed()
tenant_registry = TenantRegistry()
login_throttle = LoginThrottle()

_UNAUTHENTICATED = "缺少或无效的登录凭证"
_FORBIDDEN = "当前角色无权执行此操作"


def authenticate_login(username: str, password: str) -> Principal:
    """登录认证（docs/31 §2.2）：user_store 全局按 username 查、验哈希。

    用户不存在/口令错 → 401（不区分，防枚举）；停用账号口令正确 → 403。
    """
    account = user_store.get_by_username(username)
    if account is None or not verify_password(password, account.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if account.status == "disabled":
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    tenant = SEED_TENANTS.get(account.tenant_id)
    return Principal(
        tenant_id=account.tenant_id,
        tenant_name=tenant.name if tenant is not None else account.tenant_id,
        username=account.username,
        display_name=account.display_name,
        role=account.role,
    )


def get_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header[:7].lower() == "bearer " else None
    principal = session_store.principal_for_token(token)
    if principal is None:
        raise HTTPException(status_code=401, detail=_UNAUTHENTICATED)
    return principal


def require(*capabilities: Capability):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not all(can(principal.role, capability) for capability in capabilities):
            raise HTTPException(status_code=403, detail=_FORBIDDEN)
        return principal

    return dependency


def services_for(principal: Principal) -> TenantServices:
    return tenant_registry.get(principal.tenant_id)

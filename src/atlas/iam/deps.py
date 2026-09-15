"""FastAPI 鉴权依赖（04 §5.14）：Bearer 会话 → Principal → 角色白名单 → 租户服务。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .principals import Capability, Principal, can
from .registry import TenantRegistry, TenantServices
from .sessions import SessionStore

session_store = SessionStore()
tenant_registry = TenantRegistry()

_UNAUTHENTICATED = "缺少或无效的登录凭证"
_FORBIDDEN = "当前角色无权执行此操作"


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

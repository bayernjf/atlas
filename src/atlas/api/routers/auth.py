"""P2-1 split: Login, session, password and user administration routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import time
from atlas.iam.accounts import UserExists
from atlas.iam.deps import SESSION_COOKIE, authenticate_login, get_principal, login_throttle, must_change_password, presented_token, require, session_store, tenant_registry, user_store
from atlas.iam.passwords import validate_password, validate_username, verify_password
from atlas.iam.principals import Principal
from atlas.observability.audit import LOGIN_PATH
from fastapi import Depends, HTTPException, Request, Response
from atlas.api.main import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    ResetPasswordRequest,
    UpdateUserRequest,
    UserResponse,
    _cookie_secure,
    _user_response,
    _validate_password_or_422,
    logger,
)


router: APIRouter = APIRouter()


@router.post("/api/auth/login", response_model=LoginResponse)
def login(request: LoginRequest, http_request: Request, response: Response) -> LoginResponse:
    """账号登录换 sess-token（04 §5.14，docs/31 §2.2/§5）。

    坏凭证 401、停用 403；600s 内同 username+IP 5 次失败 → 429（锁定时不校验口令）；成功清零。
    """
    client_ip = http_request.client.host if http_request.client else ""
    throttle_key = f"{request.username}|{client_ip}"
    now = time.time()
    if login_throttle.is_locked(throttle_key, now):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    try:
        principal = authenticate_login(request.username, request.password)
    except HTTPException:
        login_throttle.record_failure(throttle_key, now)
        raise
    login_throttle.reset(throttle_key)
    token = session_store.issue(principal)
    # 触发租户装配，登录后该租户即有独立服务实例
    login_services = tenant_registry.get(principal.tenant_id)
    # T6 审计：登录成功显式记一条（失败在上方抛 401/403 不记；中间件跳过 login 路径）。
    # 审计失败不影响登录本身（fail-safe）。
    try:
        login_services.audit_store.record(
            tenant_id=principal.tenant_id,
            actor=principal.username,
            action="POST /api/auth/login",
            status_code=200,
            path=LOGIN_PATH,
            ip=client_ip,
        )
    except Exception as exc:  # 审计绝不阻断登录
        logger.warning("login audit record failed: %s", exc)
    # 打包 ZQ Q4：会话凭证同时以 httpOnly Cookie 下发（HttpOnly＋SameSite=Strict＋prod Secure）；
    # 前端不落盘 token，浏览器自动携带。响应体仍带 token 以兼容既有客户端与 A2A 调用方。
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(),
        path="/",
    )
    return LoginResponse(
        token=token,
        principal=principal,
        mustChangePassword=must_change_password(principal),
    )


@router.get("/api/auth/me", response_model=LoginResponse)
def me(request: Request, principal: Principal = Depends(get_principal)) -> LoginResponse:
    return LoginResponse(
        token=presented_token(request) or "",
        principal=principal,
        mustChangePassword=must_change_password(principal),
    )


@router.post("/api/auth/logout")
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
) -> dict[str, bool]:
    token = presented_token(request)
    if token:
        session_store.revoke(token)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", path="/")
    return {"logged_out": True}


@router.post("/api/auth/change-password")
def change_password(
    raw: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, bool]:
    """登录用户改本人密码（docs/31 §3）：旧口令错 400；成功吊销本人其他会话、保留当前。

    `by_owner=True` 盖轮换位（docs/95 打包 AV）——这是唯一能清掉强制改密门的写入路径。
    keep_token 用 `presented_token`（与 `get_principal` 同一份取数规则）：以前这里是
    `_bearer_token`，打包 ZQ Q4 之后 SPA 不带 Authorization 头，于是"保留当前"保留的是
    另一个会话，改密的人反而被登出。
    """
    account = user_store.get(principal.tenant_id, principal.username)
    if account is None or not verify_password(raw.oldPassword, account.password_hash):
        # 结构化 detail（docs/125 §3.5，项目级审查发现 #7）：前端按 code 分支，不匹配中文文案。
        raise HTTPException(
            status_code=400,
            detail={"code": "AUTH_OLD_PASSWORD_INVALID", "message": "原密码错误"},
        )
    _validate_password_or_422(raw.newPassword)
    if raw.newPassword == raw.oldPassword:
        raise HTTPException(status_code=422, detail="新密码不能与原密码相同")
    user_store.set_password(
        principal.tenant_id,
        principal.username,
        raw.newPassword,
        keep_token=presented_token(request),
        by_owner=True,
    )
    return {"changed": True}


@router.get("/api/users")
def list_users(principal: Principal = Depends(require("administer"))) -> list[UserResponse]:
    """admin 列本租户用户（docs/31 §3），不含 password_hash。"""
    return [_user_response(account) for account in user_store.list(principal.tenant_id)]


@router.post("/api/users", status_code=201)
def create_user(
    raw: CreateUserRequest,
    principal: Principal = Depends(require("administer")),
) -> UserResponse:
    """admin 在本租户建用户（docs/31 §3）：重名 409、策略不达标 422。"""
    try:
        validate_username(raw.username)
        validate_password(raw.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not raw.displayName.strip():
        raise HTTPException(status_code=422, detail="显示名不能为空")
    try:
        account = user_store.create(
            tenant_id=principal.tenant_id,
            username=raw.username,
            password=raw.password,
            display_name=raw.displayName.strip(),
            role=raw.role,
        )
    except UserExists as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    return _user_response(account)


@router.patch("/api/users/{username}")
def update_user(
    username: str,
    raw: UpdateUserRequest,
    principal: Principal = Depends(require("administer")),
) -> UserResponse:
    """admin 改本租户用户 displayName/role/status（docs/31 §3）；跨租户/不存在 → 404。"""
    if user_store.get(principal.tenant_id, username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    data = raw.model_dump(exclude_unset=True)
    updated = user_store.update(
        principal.tenant_id,
        username,
        display_name=data.get("displayName"),
        role=raw.role,
        status=data.get("status"),
    )
    assert updated is not None
    return _user_response(updated)


@router.post("/api/users/{username}/reset-password")
def reset_password(
    username: str,
    raw: ResetPasswordRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """admin 重置本租户用户密码（docs/31 §3）：404/422；成功吊销该用户全部会话。

    不盖轮换位＝**重新装填**强制改密门（docs/95 §2 订正）：重置后的口令只有操作它的 admin
    与该系统账号知道，本人从未亲手套上过口令，强制位在 prod 因此回到未满足态。
    """
    if user_store.get(principal.tenant_id, username) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _validate_password_or_422(raw.newPassword)
    user_store.set_password(principal.tenant_id, username, raw.newPassword)
    return {"reset": True}

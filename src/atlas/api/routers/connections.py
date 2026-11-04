"""P2-1 split: Connection credentials and OAuth callback routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.connections.service import ConnectionServiceError
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from fastapi import Depends
from fastapi.responses import HTMLResponse
from typing import Any
from atlas.api.main import (
    ConnectionCreateRequest,
    ConnectionExchangeRequest,
    ConnectionUpsertRequest,
    _OAUTH_CALLBACK_HTML,
    _conn_http_error,
)


router: APIRouter = APIRouter()


@router.post("/api/connections", status_code=201)
def create_connection(
    body: ConnectionCreateRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """创建 OAuth2 连接配置（admin only）；授权/token URL 过出向校验，client_secret 立即加密。"""
    try:
        return _main.services_for(principal).connection_service.create(
            body.model_dump(), created_by=principal.username
        )
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.get("/api/connections")
def list_connections(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    return {"items": _main.services_for(principal).connection_service.list()}


@router.get("/api/connections/{conn_id}")
def get_connection(conn_id: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    try:
        return _main.services_for(principal).connection_service.get(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.put("/api/connections/{conn_id}")
def update_connection(
    conn_id: str,
    body: ConnectionUpsertRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """更新连接白名单字段（admin only）；client_secret 未传/空串保留原信封。"""
    try:
        return _main.services_for(principal).connection_service.update(
            conn_id, body.model_dump(exclude_unset=True)
        )
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.delete("/api/connections/{conn_id}")
def delete_connection(conn_id: str, principal: Principal = Depends(require("administer"))) -> dict[str, Any]:
    try:
        deleted = _main.services_for(principal).connection_service.delete(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)
    return {"deleted": deleted}


@router.post("/api/connections/{conn_id}/authorize")
def authorize_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    """返回授权 URL 与 state（前端 window.open 打开，state 10 分钟有效，仅防 CSRF）。"""
    try:
        return _main.services_for(principal).connection_service.authorize(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.post("/api/connections/{conn_id}/exchange")
def exchange_connection(
    conn_id: str,
    body: ConnectionExchangeRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """校验 state→授权码换 token→加密落库；token 失败置 status=error。"""
    try:
        return _main.services_for(principal).connection_service.exchange(conn_id, body.code, body.state)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.post("/api/connections/{conn_id}/refresh")
def refresh_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    try:
        return _main.services_for(principal).connection_service.refresh(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.post("/api/connections/{conn_id}/test")
def test_connection(conn_id: str, principal: Principal = Depends(require("operate"))) -> dict[str, Any]:
    """连接测试：过期先刷新；draft/error 返回 ok=false 与中文原因。generic 框架不调用真实业务 API。"""
    try:
        return _main.services_for(principal).connection_service.test(conn_id)
    except ConnectionServiceError as exc:
        raise _conn_http_error(exc)


@router.get("/connections/callback", response_class=HTMLResponse, include_in_schema=False)
def oauth_callback_page() -> HTMLResponse:
    """OAuth provider 回调落地页（无鉴权、无服务端副作用、不发 token）：展示 code/state 并引导
    回到「连接管理 → 完成授权」粘贴。code 仅停留在本页与用户剪贴板。"""
    return HTMLResponse(content=_OAUTH_CALLBACK_HTML)

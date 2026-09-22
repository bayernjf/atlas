# -*- coding: utf-8 -*-
"""OAuth2 连接服务（docs/35 §4.3/§4.4，T4；平台无关 generic OAuth2）。

ConnectionService 组合连接存储、SecretProvider（凭据信封加密）、出向校验（SSRF）、
state 签名密钥与可注入的 form POST，完成 CRUD、授权 URL、换 token、刷新、测试。
HTTP 层（api/main.py）把 ConnectionServiceError 折算为中文 HTTPException。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

import httpx

from atlas.connections import oauth
from atlas.connections.models import (
    STATUS_CONNECTED,
    STATUS_DRAFT,
    STATUS_ERROR,
    Connection,
)
from atlas.connections.oauth import (
    OAuthStateError,
    OAuthTokenError,
    TokenBundle,
    build_authorize_url,
    build_state,
    compute_expires_at,
    exchange_code,
    is_expired,
    refresh_tokens,
    resolve_state_secret,
    verify_state,
)
from atlas.security.egress import EgressDenied, EgressGuard
from atlas.security.secrets import SecretProvider, build_secret_provider_from_env

logger = logging.getLogger(__name__)

DEFAULT_REDIRECT_URI = "http://localhost:8000/connections/callback"
STATE_TTL = oauth.STATE_TTL_SECONDS


class ConnectionServiceError(Exception):
    """业务错误；status_code 供 HTTP 层折算，code 为稳定错误码。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def default_redirect_uri() -> str:
    return os.getenv("ATLAS_OAUTH_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI


def _default_post_form(url: str, *, data: dict[str, str], timeout: float):
    # data= 让 httpx 以 application/x-www-form-urlencoded 发送；禁止重定向（SSRF 纵深）。
    return httpx.post(
        url,
        data=data,
        timeout=timeout,
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )


class ConnectionService:
    def __init__(
        self,
        store: Any,
        *,
        tenant_id: str,
        secret_provider: SecretProvider,
        state_secret: str | None = None,
        egress: EgressGuard | None = None,
        post_form: Callable[..., Any] | None = None,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._provider = secret_provider
        self._state_secret = state_secret or resolve_state_secret()
        self._egress = egress if egress is not None else EgressGuard.from_env()
        self._post_form = post_form or _default_post_form

    # ---------------- 内部工具 ----------------

    def _require(self, conn_id: str) -> Connection:
        conn = self._store.get(conn_id)
        if conn is None or conn.tenant_id != self._tenant_id:
            raise ConnectionServiceError("CONNECTION_NOT_FOUND", "连接不存在或不属于当前租户", 404)
        return conn

    def _check_url(self, url: str, field: str) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ConnectionServiceError("MISSING_PARAMETER", f"缺少 {field}", 422)
        try:
            self._egress.check(url.strip())
        except EgressDenied as exc:
            # 透传 EGRESS_DENIED / EGRESS_INVALID_URL
            raise ConnectionServiceError(exc.code, f"{field} 出向校验未通过：{exc}", 400) from exc

    @staticmethod
    def _require_str(body: dict, field: str) -> str:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConnectionServiceError("MISSING_PARAMETER", f"缺少 {field}", 422)
        return value.strip()

    def _decrypt(self, envelope: str | None) -> str:
        if not envelope:
            return ""
        return self._provider.decrypt(envelope)

    def _apply_token_bundle(self, conn: Connection, bundle: TokenBundle, *, keep_refresh: bool) -> None:
        conn.access_token_envelope = self._provider.encrypt(bundle.access_token)
        if bundle.refresh_token:
            conn.refresh_token_envelope = self._provider.encrypt(bundle.refresh_token)
        elif not keep_refresh:
            conn.refresh_token_envelope = None
        # keep_refresh=True（刷新流程）且响应未带新 refresh 时保留原 refresh
        conn.token_type = bundle.token_type or conn.token_type or "Bearer"
        conn.expires_at = compute_expires_at(bundle.expires_in)
        conn.status = STATUS_CONNECTED
        conn.last_error = None

    # ---------------- CRUD ----------------

    def create(self, body: dict, *, created_by: str | None = None) -> dict:
        provider = self._require_str(body, "provider")
        display_name = self._require_str(body, "displayName")
        auth_url = self._require_str(body, "authUrl").strip()
        token_url = self._require_str(body, "tokenUrl").strip()
        client_id = self._require_str(body, "clientId")
        self._check_url(auth_url, "authUrl")
        self._check_url(token_url, "tokenUrl")

        scopes = body.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
            raise ConnectionServiceError("INVALID_PARAMETER", "scopes 必须是字符串数组", 422)

        redirect_uri = (body.get("redirectUri") or "").strip() or default_redirect_uri()
        client_secret = body.get("clientSecret")
        secret_envelope = self._provider.encrypt(client_secret) if isinstance(client_secret, str) and client_secret else None

        conn = Connection(
            id="",
            tenant_id=self._tenant_id,
            provider=provider,
            display_name=display_name,
            auth_url=auth_url,
            token_url=token_url,
            client_id=client_id,
            client_secret_envelope=secret_envelope,
            scopes=list(scopes),
            redirect_uri=redirect_uri,
            status=STATUS_DRAFT,
            created_by=created_by,
        )
        self._store.create(conn)
        return conn.public_view()

    def list(self) -> list[dict]:
        return [c.public_view() for c in self._store.list() if c.tenant_id == self._tenant_id]

    def get(self, conn_id: str) -> dict:
        return self._require(conn_id).public_view()

    def update(self, conn_id: str, body: dict) -> dict:
        conn = self._require(conn_id)
        if "provider" in body and isinstance(body["provider"], str) and body["provider"].strip():
            conn.provider = body["provider"].strip()
        if "displayName" in body and isinstance(body["displayName"], str) and body["displayName"].strip():
            conn.display_name = body["displayName"].strip()
        if "clientId" in body and isinstance(body["clientId"], str) and body["clientId"].strip():
            conn.client_id = body["clientId"].strip()
        if "authUrl" in body and isinstance(body["authUrl"], str) and body["authUrl"].strip():
            url = body["authUrl"].strip()
            self._check_url(url, "authUrl")
            conn.auth_url = url
        if "tokenUrl" in body and isinstance(body["tokenUrl"], str) and body["tokenUrl"].strip():
            url = body["tokenUrl"].strip()
            self._check_url(url, "tokenUrl")
            conn.token_url = url
        if "scopes" in body:
            scopes = body["scopes"] or []
            if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
                raise ConnectionServiceError("INVALID_PARAMETER", "scopes 必须是字符串数组", 422)
            conn.scopes = list(scopes)
        if "redirectUri" in body and isinstance(body["redirectUri"], str) and body["redirectUri"].strip():
            conn.redirect_uri = body["redirectUri"].strip()
        # client_secret：未传/空串保留原信封；非空才重新加密
        secret = body.get("clientSecret")
        if isinstance(secret, str) and secret:
            conn.client_secret_envelope = self._provider.encrypt(secret)
        self._store.save(conn)
        return conn.public_view()

    def delete(self, conn_id: str) -> bool:
        conn = self._require(conn_id)
        return self._store.delete(conn.id)

    def access_token_for(self, conn_id: str) -> str | None:
        """现解密并返回 access token（渠道层调用）；未完成授权/已过期 → None。"""
        conn = self._require(conn_id)
        if conn.status != STATUS_CONNECTED or not conn.access_token_envelope:
            return None
        if is_expired(conn.expires_at):
            return None
        return self._decrypt(conn.access_token_envelope)

    # ---------------- OAuth 流程 ----------------

    def authorize(self, conn_id: str) -> dict:
        conn = self._require(conn_id)
        state = build_state(self._tenant_id, conn.id, secret=self._state_secret)
        url = build_authorize_url(
            auth_url=conn.auth_url,
            client_id=conn.client_id,
            redirect_uri=conn.redirect_uri,
            scopes=conn.scopes,
            state=state,
        )
        return {"authorizeUrl": url, "state": state, "expiresIn": STATE_TTL}

    def exchange(self, conn_id: str, code: str, state: str) -> dict:
        conn = self._require(conn_id)
        try:
            verify_state(
                state,
                secret=self._state_secret,
                expected_tenant=self._tenant_id,
                expected_conn=conn.id,
            )
        except OAuthStateError as exc:
            # state 问题不改变连接状态（CSRF/过期，属客户端请求错误）
            raise ConnectionServiceError("OAUTH_STATE_INVALID", f"授权 state 校验失败：{exc}", 400) from exc
        if not isinstance(code, str) or not code.strip():
            raise ConnectionServiceError("MISSING_PARAMETER", "缺少授权码 code", 422)
        client_secret = self._decrypt(conn.client_secret_envelope)
        try:
            bundle = exchange_code(
                token_url=conn.token_url,
                code=code.strip(),
                redirect_uri=conn.redirect_uri,
                client_id=conn.client_id,
                client_secret=client_secret,
                post_form=self._post_form,
            )
        except OAuthTokenError as exc:
            conn.status = STATUS_ERROR
            conn.last_error = str(exc)
            self._store.save(conn)
            raise ConnectionServiceError("OAUTH_TOKEN_FAILED", f"换取令牌失败：{exc}", 502) from exc
        self._apply_token_bundle(conn, bundle, keep_refresh=False)
        self._store.save(conn)
        return conn.public_view()

    def refresh(self, conn_id: str) -> dict:
        conn = self._require(conn_id)
        refresh_envelope = conn.refresh_token_envelope
        if not refresh_envelope:
            raise ConnectionServiceError(
                "OAUTH_NO_REFRESH_TOKEN", "该连接没有 refresh_token，请重新走授权流程", 400
            )
        refresh_token = self._decrypt(refresh_envelope)
        client_secret = self._decrypt(conn.client_secret_envelope)
        try:
            bundle = refresh_tokens(
                token_url=conn.token_url,
                refresh_token=refresh_token,
                client_id=conn.client_id,
                client_secret=client_secret,
                post_form=self._post_form,
            )
        except OAuthTokenError as exc:
            conn.status = STATUS_ERROR
            conn.last_error = str(exc)
            self._store.save(conn)
            raise ConnectionServiceError("OAUTH_REFRESH_FAILED", f"刷新令牌失败：{exc}", 502) from exc
        self._apply_token_bundle(conn, bundle, keep_refresh=True)
        self._store.save(conn)
        return conn.public_view()

    def test(self, conn_id: str) -> dict:
        conn = self._require(conn_id)
        if conn.status == STATUS_DRAFT or not conn.access_token_envelope:
            return {"ok": False, "status": conn.status, "expiresAt": conn.expires_at,
                    "reason": "连接尚未完成授权"}
        # 已过期（含 60s 提前量）先刷新；刷新失败则 error
        if is_expired(conn.expires_at):
            if not conn.refresh_token_envelope:
                conn.status = STATUS_ERROR
                conn.last_error = "访问令牌已过期且无 refresh_token，需重新授权"
                self._store.save(conn)
                return {"ok": False, "status": STATUS_ERROR, "expiresAt": conn.expires_at,
                        "reason": conn.last_error}
            try:
                self.refresh(conn_id)
                conn = self._require(conn_id)
            except ConnectionServiceError as exc:
                return {"ok": False, "status": STATUS_ERROR, "expiresAt": conn.expires_at,
                        "reason": f"令牌已过期且自动刷新失败：{exc}"}
        if conn.status == STATUS_ERROR:
            return {"ok": False, "status": STATUS_ERROR, "expiresAt": conn.expires_at,
                    "reason": conn.last_error or "连接处于错误状态"}
        # generic 框架不调用任何真实业务 API（非目标）；test 仅验证令牌状态/可刷新性。
        return {"ok": True, "status": conn.status, "expiresAt": conn.expires_at}


# ---------------- 进程级装配（共享单例） ----------------

_secret_provider: SecretProvider | None = None
_egress_guard: EgressGuard | None = None


def get_secret_provider() -> SecretProvider:
    global _secret_provider
    if _secret_provider is None:
        _secret_provider = build_secret_provider_from_env()
    return _secret_provider


def get_egress_guard() -> EgressGuard:
    global _egress_guard
    if _egress_guard is None:
        _egress_guard = EgressGuard.from_env()
    return _egress_guard


def build_connection_service(store: Any, *, tenant_id: str) -> ConnectionService:
    """每租户一个 service（绑定 per-tenant store），provider/egress/state 密钥进程共享。"""
    return ConnectionService(
        store,
        tenant_id=tenant_id,
        secret_provider=get_secret_provider(),
        state_secret=resolve_state_secret(),
        egress=get_egress_guard(),
        post_form=_default_post_form,
    )

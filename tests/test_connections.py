# -*- coding: utf-8 -*-
"""T4 generic OAuth2 连接管理测试（docs/35 §4；D22 子集，平台无关）。

覆盖：
- oauth 纯逻辑：state HMAC 验签/过期/租户/连接四要素、authorize URL 形状、
  exchange/refresh form 与 token 响应解析（缺 access_token/非 2xx/网络错）、过期判定；
- ConnectionService（内存 store + fake egress + fake post + PlaintextSecretProvider）：
  CRUD、public_view 绝不回传任何信封/明文、update 缺省保留 secret、egress 拒绝、
  exchange/refresh/test 状态机、他租户 404；
- REST（内存档 TestClient）：权限分层（viewer 只读、operator 授权不可建、admin 建删）、
  列表不泄密、回调页无鉴权；
- PgConnectionStore 直连集成（ATLAS_RUN_INTEGRATION=1 + DATABASE_URL，不设 STORAGE_BACKEND）。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.connections import oauth
from atlas.connections.oauth import (
    OAuthStateError,
    OAuthTokenError,
    build_authorize_url,
    build_state,
    compute_expires_at,
    exchange_code,
    is_expired,
    refresh_tokens,
    verify_state,
)
from atlas.connections.service import (
    ConnectionService,
    ConnectionServiceError,
    default_redirect_uri,
)
from atlas.connections.store import ConnectionStore
from atlas.security.egress import EgressDenied
from atlas.security.secrets import PlaintextSecretProvider

client = TestClient(app)

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pg_integration = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run connections PG integration",
)

AUTH_URL = "https://idp.example.com/oauth/authorize"
TOKEN_URL = "https://idp.example.com/oauth/token"
REDIRECT = "http://localhost:8000/connections/callback"

CONN_BODY = {
    "provider": "generic",
    "displayName": "测试平台",
    "authUrl": AUTH_URL,
    "tokenUrl": TOKEN_URL,
    "clientId": "cid-1",
    "clientSecret": "shh-secret",
    "scopes": ["read", "write"],
    "redirectUri": REDIRECT,
}


class _AllowEgress:
    def check(self, url: str) -> None:
        return None


class _DenyEgress:
    def __init__(self, code: str = "EGRESS_DENIED") -> None:
        self.code = code

    def check(self, url: str) -> None:
        raise EgressDenied(self.code, "blocked")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _make_post(response: _FakeResponse, capture: dict | None = None, raise_exc: Exception | None = None):
    def _post(url, *, data=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["data"] = data
            capture["timeout"] = timeout
        if raise_exc is not None:
            raise raise_exc
        return response

    return _post


def _service(post=None, egress=None, tenant: str = "t1", state_secret: str = "state-secret"):
    return ConnectionService(
        ConnectionStore(),
        tenant_id=tenant,
        secret_provider=PlaintextSecretProvider(),
        state_secret=state_secret,
        egress=egress or _AllowEgress(),
        post_form=post or _make_post(_FakeResponse(200, {})),
    )


# ============================ oauth 纯逻辑 ============================


def test_state_roundtrip_and_tamper_expiry_tenant_conn():
    state = build_state("t1", "conn-7", secret="k")
    body = verify_state(state, secret="k", expected_tenant="t1", expected_conn="conn-7")
    assert body["tenant"] == "t1" and body["conn"] == "conn-7"

    with pytest.raises(OAuthStateError):
        verify_state(state + "x", secret="k", expected_tenant="t1", expected_conn="conn-7")
    with pytest.raises(OAuthStateError):
        verify_state(state, secret="wrong", expected_tenant="t1", expected_conn="conn-7")
    with pytest.raises(OAuthStateError):
        verify_state(state, secret="k", expected_tenant="t2", expected_conn="conn-7")
    with pytest.raises(OAuthStateError):
        verify_state(state, secret="k", expected_tenant="t1", expected_conn="conn-8")

    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = build_state("t1", "conn-7", secret="k", ttl_seconds=600)
    with pytest.raises(OAuthStateError):
        verify_state(expired, secret="k", expected_tenant="t1", expected_conn="conn-7", now=past + timedelta(hours=1))

    with pytest.raises(OAuthStateError):
        verify_state("not-a-state", secret="k", expected_tenant="t1", expected_conn="conn-7")


def test_authorize_url_shape_and_scope():
    url = build_authorize_url(
        auth_url=AUTH_URL, client_id="cid-1", redirect_uri=REDIRECT,
        scopes=["read", "write"], state="st-1",
    )
    assert url.startswith(AUTH_URL + "?")
    assert "response_type=code" in url
    assert "client_id=cid-1" in url
    assert "state=st-1" in url
    assert "scope=read+write" in url  # 空格分隔
    url_no_scope = build_authorize_url(
        auth_url=AUTH_URL, client_id="cid-1", redirect_uri=REDIRECT, scopes=[], state="s"
    )
    assert "scope=" not in url_no_scope


def test_exchange_code_form_and_success():
    capture: dict = {}
    post = _make_post(
        _FakeResponse(200, {"access_token": "at-1", "refresh_token": "rt-1",
                            "token_type": "Bearer", "expires_in": 3600}),
        capture,
    )
    bundle = exchange_code(
        token_url=TOKEN_URL, code="code-1", redirect_uri=REDIRECT,
        client_id="cid-1", client_secret="shh", post_form=post,
    )
    assert bundle.access_token == "at-1" and bundle.refresh_token == "rt-1"
    assert bundle.token_type == "Bearer" and bundle.expires_in == 3600
    assert capture["data"]["grant_type"] == "authorization_code"
    assert capture["data"]["code"] == "code-1"
    assert capture["data"]["client_secret"] == "shh"


def test_exchange_failures():
    with pytest.raises(OAuthTokenError):
        exchange_code(token_url=TOKEN_URL, code="c", redirect_uri=REDIRECT, client_id="x",
                      client_secret="s", post_form=_make_post(_FakeResponse(400, {"error": "bad"})))
    with pytest.raises(OAuthTokenError):
        exchange_code(token_url=TOKEN_URL, code="c", redirect_uri=REDIRECT, client_id="x",
                      client_secret="s", post_form=_make_post(_FakeResponse(200, {"refresh_token": "r"})))
    with pytest.raises(OAuthTokenError):
        exchange_code(token_url=TOKEN_URL, code="c", redirect_uri=REDIRECT, client_id="x",
                      client_secret="s", post_form=_make_post(_FakeResponse(200, {}),
                                                              raise_exc=TimeoutError()))


def test_refresh_form_and_expiry():
    capture: dict = {}
    post = _make_post(
        _FakeResponse(200, {"access_token": "at-2", "expires_in": 60}), capture
    )
    bundle = refresh_tokens(token_url=TOKEN_URL, refresh_token="rt-1",
                            client_id="cid-1", client_secret="shh", post_form=post)
    assert bundle.access_token == "at-2" and bundle.expires_in == 60
    assert capture["data"]["grant_type"] == "refresh_token"
    assert capture["data"]["refresh_token"] == "rt-1"

    assert compute_expires_at(None) is None
    future = compute_expires_at(3600)
    assert is_expired(future) is False
    assert is_expired(None) is False
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert is_expired(past) is True
    # 60s 提前量：50s 后过期视为已过期
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    assert is_expired(soon) is True


# ============================ ConnectionService ============================


def test_create_public_view_never_leaks_secret():
    svc = _service()
    view = svc.create(dict(CONN_BODY), created_by="admin-a")
    assert view["id"].startswith("conn-")
    assert view["hasClientSecret"] is True
    assert view["status"] == "draft"
    assert view["scopes"] == ["read", "write"]
    # 投影不含任何秘密/信封/明文凭据键（tokenUrl/tokenType/hasClientSecret 非秘密）
    sensitive = {
        "clientSecret", "client_secret_envelope", "access_token_envelope",
        "refresh_token_envelope", "accessToken", "refreshToken", "access_token",
        "refresh_token",
    }
    assert sensitive.isdisjoint(view.keys())
    assert view["tokenType"] is None
    # 列表同样不泄密
    listed = svc.list()
    assert "client_secret_envelope" not in listed[0] and "access_token_envelope" not in listed[0]
    # 存储内 secret 是信封（非明文）
    conn = svc._store.get(view["id"])
    assert conn.client_secret_envelope and "shh-secret" not in conn.client_secret_envelope


def test_create_without_secret_and_defaults_redirect():
    svc = _service()
    body = {k: v for k, v in CONN_BODY.items() if k not in ("clientSecret", "redirectUri")}
    view = svc.create(body)
    assert view["hasClientSecret"] is False
    assert view["redirectUri"] == default_redirect_uri()


def test_create_validation_and_egress():
    svc = _service()
    bad = dict(CONN_BODY)
    del bad["displayName"]
    with pytest.raises(ConnectionServiceError) as ei:
        svc.create(bad)
    assert ei.value.status_code == 422

    with pytest.raises(ConnectionServiceError) as ei2:
        _service(egress=_DenyEgress()).create(dict(CONN_BODY))
    assert ei2.value.status_code == 400 and ei2.value.code == "EGRESS_DENIED"

    bad_scopes = dict(CONN_BODY)
    bad_scopes["scopes"] = ["read", 123]
    with pytest.raises(ConnectionServiceError):
        svc.create(bad_scopes)


def test_get_other_tenant_and_missing_404():
    svc = _service(tenant="t1")
    view = svc.create(dict(CONN_BODY))
    other = _service(tenant="t2")
    with pytest.raises(ConnectionServiceError) as ei:
        other.get(view["id"])
    assert ei.value.status_code == 404
    with pytest.raises(ConnectionServiceError) as ei2:
        svc.get("conn-999")
    assert ei2.value.status_code == 404


def test_update_keeps_secret_when_omitted_and_replaces_when_given():
    svc = _service()
    view = svc.create(dict(CONN_BODY))
    cid = view["id"]
    original_env = svc._store.get(cid).client_secret_envelope
    # 不传 secret：保留原信封
    svc.update(cid, {"displayName": "改名"})
    assert svc._store.get(cid).client_secret_envelope == original_env
    assert svc.get(cid)["displayName"] == "改名"
    # 传空串：保留
    svc.update(cid, {"clientSecret": ""})
    assert svc._store.get(cid).client_secret_envelope == original_env
    # 传新值：更换且可正确解密
    svc.update(cid, {"clientSecret": "new-secret"})
    new_env = svc._store.get(cid).client_secret_envelope
    assert new_env != original_env
    assert svc._provider.decrypt(new_env) == "new-secret"


def _connected_service(token_payload=None):
    payload = token_payload or {
        "access_token": "at-1", "refresh_token": "rt-1",
        "token_type": "Bearer", "expires_in": 3600,
    }
    svc = _service(post=_make_post(_FakeResponse(200, payload)))
    view = svc.create(dict(CONN_BODY))
    cid = view["id"]
    state = svc.authorize(cid)["state"]
    return svc, cid, state


def test_authorize_url_and_exchange_success_encrypts_tokens():
    svc, cid, state = _connected_service()
    auth = svc.authorize(cid)
    assert auth["authorizeUrl"].startswith(AUTH_URL) and auth["expiresIn"] == 600
    view = svc.exchange(cid, "code-1", state)
    assert view["status"] == "connected" and view["tokenType"] == "Bearer"
    assert view["expiresAt"] is not None
    conn = svc._store.get(cid)
    # token 以信封落库、非明文
    assert conn.access_token_envelope and "at-1" not in conn.access_token_envelope
    assert conn.refresh_token_envelope and "rt-1" not in conn.refresh_token_envelope
    assert svc._provider.decrypt(conn.access_token_envelope) == "at-1"


def test_exchange_bad_state_keeps_draft():
    svc, cid, _ = _connected_service()
    with pytest.raises(ConnectionServiceError) as ei:
        svc.exchange(cid, "code-1", "tampered-state")
    assert ei.value.code == "OAUTH_STATE_INVALID" and ei.value.status_code == 400
    assert svc._store.get(cid).status == "draft"


def test_exchange_token_error_sets_error_status():
    svc = _service(post=_make_post(_FakeResponse(200, {"oops": 1})))
    cid = svc.create(dict(CONN_BODY))["id"]
    state = svc.authorize(cid)["state"]
    with pytest.raises(ConnectionServiceError) as ei:
        svc.exchange(cid, "code", state)
    assert ei.value.status_code == 502 and ei.value.code == "OAUTH_TOKEN_FAILED"
    conn = svc._store.get(cid)
    assert conn.status == "error" and conn.last_error


def test_refresh_requires_refresh_token():
    svc = _service()
    cid = svc.create(dict(CONN_BODY))["id"]
    with pytest.raises(ConnectionServiceError) as ei:
        svc.refresh(cid)
    assert ei.value.code == "OAUTH_NO_REFRESH_TOKEN"


def test_refresh_success_keeps_old_refresh_when_omitted():
    svc, cid, state = _connected_service()
    svc.exchange(cid, "code", state)
    old_rt_env = svc._store.get(cid).refresh_token_envelope
    # 刷新响应不带新 refresh_token：保留原 refresh
    svc._post_form = _make_post(_FakeResponse(200, {"access_token": "at-2", "expires_in": 3600}))
    view = svc.refresh(cid)
    assert view["status"] == "connected"
    assert svc._store.get(cid).refresh_token_envelope == old_rt_env
    assert svc._provider.decrypt(svc._store.get(cid).access_token_envelope) == "at-2"


def test_test_connection_states():
    # draft：未授权
    svc = _service()
    cid = svc.create(dict(CONN_BODY))["id"]
    res = svc.test(cid)
    assert res["ok"] is False and "未" in res["reason"]

    # connected 未过期：ok
    svc2, cid2, st2 = _connected_service()
    svc2.exchange(cid2, "code", st2)
    assert svc2.test(cid2)["ok"] is True

    # 已过期且有 refresh：先刷新成功
    svc3, cid3, st3 = _connected_service()
    svc3.exchange(cid3, "code", st3)
    svc3._store.get(cid3).expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    svc3._post_form = _make_post(_FakeResponse(200, {"access_token": "at-new", "expires_in": 3600}))
    res3 = svc3.test(cid3)
    assert res3["ok"] is True

    # 过期且无 refresh：error
    svc4, cid4, st4 = _connected_service(token_payload={"access_token": "a", "expires_in": 3600})
    svc4.exchange(cid4, "code", st4)
    conn4 = svc4._store.get(cid4)
    conn4.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    conn4.refresh_token_envelope = None
    res4 = svc4.test(cid4)
    assert res4["ok"] is False and res4["status"] == "error"


def test_delete_idempotent():
    svc = _service()
    cid = svc.create(dict(CONN_BODY))["id"]
    assert svc.delete(cid) is True
    with pytest.raises(ConnectionServiceError) as ei:
        svc.delete(cid)
    assert ei.value.status_code == 404


# ============================ REST（内存档 TestClient） ============================


@pytest.fixture(autouse=True)
def _allow_egress_and_clean():
    # 让 HTTP 路径的连接 service 出向校验放行（不依赖 DNS）；每用例清空连接与演示数据。
    import atlas.connections.service as cs

    cs._egress_guard = _AllowEgress()
    h = _login("admin")
    client.post("/api/demo/reset", headers=h)
    from atlas.iam.deps import tenant_registry

    svc = tenant_registry.get("t1").connection_service
    # registry 为进程单例，t1 service 可能已被先前测试用 from_env guard 创建：直接替换实例 egress
    svc._egress = _AllowEgress()
    for item in svc.list():
        svc.delete(item["id"])
    yield
    client.headers.pop("authorization", None)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_api_requires_auth_and_permissions():
    assert client.post("/api/connections", json=CONN_BODY).status_code == 401
    viewer = _login("viewer")
    assert client.get("/api/connections", headers=viewer).status_code == 200  # viewer 可读
    assert client.post("/api/connections", json=CONN_BODY, headers=viewer).status_code == 403

    operator = _login("operator")
    assert client.post("/api/connections", json=CONN_BODY, headers=operator).status_code == 403


def test_api_admin_crud_list_does_not_leak_and_operator_actions():
    admin = _login("admin")
    r = client.post("/api/connections", json=CONN_BODY, headers=admin)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    listed = client.get("/api/connections", headers=admin).json()["items"]
    assert len(listed) == 1
    row = listed[0]
    assert "clientSecret" not in row and "client_secret_envelope" not in row
    assert "access_token_envelope" not in row and row["hasClientSecret"] is True

    detail = client.get(f"/api/connections/{cid}", headers=admin)
    assert detail.status_code == 200 and detail.json()["displayName"] == "测试平台"
    assert client.get("/api/connections/conn-9999", headers=admin).status_code == 404

    # operator 可授权（不触网，仅构造 URL）
    operator = _login("operator")
    auth = client.post(f"/api/connections/{cid}/authorize", headers=operator)
    assert auth.status_code == 200 and auth.json()["authorizeUrl"].startswith(AUTH_URL)
    # operator 不可删
    assert client.delete(f"/api/connections/{cid}", headers=operator).status_code == 403

    # PUT 更新（admin，不传 secret 保留）
    put = client.put(f"/api/connections/{cid}", json={"displayName": "改名2"}, headers=admin)
    assert put.status_code == 200 and put.json()["displayName"] == "改名2"

    # admin 删除
    assert client.delete(f"/api/connections/{cid}", headers=admin).json() == {"deleted": True}


def test_api_callback_page_is_public_and_side_effect_free():
    r = client.get("/connections/callback?code=abc&state=xyz")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "授权回调" in body and "abc" not in body  # code 由 JS 运行时读取，不进服务端 HTML
    # 无鉴权也能访问
    assert client.get("/connections/callback").status_code == 200


# ============================ PG 直连集成 ============================


def _run_all_migrations(engine) -> None:
    from sqlalchemy import text

    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


@pytest.mark.integration
@pg_integration
def test_pg_connection_store_roundtrip_and_isolation():
    from atlas.connections.models import STATUS_CONNECTED
    from atlas.connections.service import ConnectionService
    from atlas.memory.database import create_database_engine
    from atlas.security.secrets import PlaintextSecretProvider
    from atlas.storage.pg import PgBackend

    tenant = "pgconntest"
    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_all_migrations(engine)
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM oauth_connections WHERE tenant_id = :t"), {"t": tenant})
    try:
        store = PgBackend(engine).connection_store(tenant)
        svc = ConnectionService(
            store, tenant_id=tenant, secret_provider=PlaintextSecretProvider(),
            state_secret="k", egress=_AllowEgress(),
            post_form=_make_post(_FakeResponse(
                200, {"access_token": "at-pg", "refresh_token": "rt-pg",
                      "token_type": "Bearer", "expires_in": 3600})),
        )
        view = svc.create(dict(CONN_BODY), created_by="admin-a")
        cid = view["id"]
        assert cid.startswith("conn-")
        state = svc.authorize(cid)["state"]
        svc.exchange(cid, "code-pg", state)

        fetched = store.get(cid)
        assert fetched is not None and fetched.status == STATUS_CONNECTED
        assert fetched.access_token_envelope and "at-pg" not in fetched.access_token_envelope
        assert fetched.scopes == ["read", "write"]
        # public_view 不泄密
        assert "access_token_envelope" not in fetched.public_view()

        # 租户隔离：另一租户 store 看不到
        other = PgBackend(engine).connection_store("pgconntest-other")
        assert other.get(cid) is None and other.list() == []

        # 更新与删除
        svc.update(cid, {"displayName": "PG 改名"})
        assert store.get(cid).display_name == "PG 改名"
        assert svc.delete(cid) is True
        assert store.get(cid) is None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM oauth_connections WHERE tenant_id LIKE 'pgconntest%'"))
        engine.dispose()

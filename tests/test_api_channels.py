# -*- coding: utf-8 -*-
"""/api/channels REST 测试（docs/38 §5，I 类）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app

client = TestClient(app)

CONN_BODY = {
    "provider": "generic",
    "displayName": "测试平台",
    "authUrl": "https://sso.example.com/auth",
    "tokenUrl": "https://sso.example.com/token",
    "clientId": "cid-1",
    "clientSecret": "shh",
    "scopes": ["read"],
}


class _AllowEgress:
    def check(self, url: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _setup():
    import atlas.connections.service as cs
    from atlas.iam.deps import tenant_registry

    cs._egress_guard = _AllowEgress()
    admin = _login("admin")
    client.post("/api/demo/reset", headers=admin)
    services = tenant_registry.get("t1")
    services.connection_service._egress = _AllowEgress()
    for item in services.connection_service.list():
        services.connection_service.delete(item["id"])
    for item in services.channel_registry.list():
        services.channel_registry.delete(item["id"])
    # 内存 ChannelStore 的 ch-N 序列不随删除归零；本模块用例假定从 ch-1 开始
    services.channel_registry._store._seq = 0
    yield
    client.headers.pop("authorization", None)


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_connection(headers) -> str:
    r = client.post("/api/connections", json=CONN_BODY, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _bind(headers, conn_id, shop="acme"):
    return client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": conn_id,
              "config": {"shop": shop}},
        headers=headers,
    )


def test_requires_auth_and_role_gates():
    client.cookies.clear()  # 打包 ZQ Q4：httpOnly Cookie 由前序测试残留——未认证用例需干净 jar

    assert client.get("/api/channels").status_code == 401
    viewer = _login("viewer")
    assert client.get("/api/channels", headers=viewer).status_code == 200
    assert _bind(viewer, "conn-1").status_code == 403

    operator = _login("operator")
    assert client.delete("/api/channels/ch-1", headers=operator).status_code == 403


def test_bind_list_get_and_projection_has_no_secret():
    admin = _login("admin")
    cid = _create_connection(admin)
    r = _bind(admin, cid, shop="Acme")
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["id"] == "ch-1"
    assert view["config"] == {"shop": "acme", "apiVersion": "2025-01"}

    items = client.get("/api/channels", headers=admin).json()["items"]
    assert items == [view]
    row = items[0]
    assert "token" not in row and "secret" not in row

    detail = client.get("/api/channels/ch-1", headers=admin)
    assert detail.status_code == 200 and detail.json() == view
    assert client.get("/api/channels/ch-99", headers=admin).status_code == 404


def test_bind_errors():
    admin = _login("admin")
    # 引用不存在的 connection → 404
    r = _bind(admin, "conn-999")
    assert r.status_code == 404

    cid = _create_connection(admin)
    assert _bind(admin, cid).status_code == 201
    # 重复绑定同一 connection → 409
    dup = _bind(admin, cid)
    assert dup.status_code == 409
    # 非法店铺 → 400（用新连接，避开重复绑定冲突）
    cid2 = _create_connection(admin)
    bad = client.post(
        "/api/channels",
        json={"provider": "shopify", "connectionId": cid2,
              "config": {"shop": "a b"}},
        headers=admin,
    )
    assert bad.status_code == 400
    # 未知 provider → 400（再开一个连接）
    cid3 = _create_connection(admin)
    unknown = client.post(
        "/api/channels",
        json={"provider": "amazon", "connectionId": cid3, "config": {}},
        headers=admin,
    )
    assert unknown.status_code == 400


def test_test_binding_without_token_returns_ok_false():
    admin = _login("admin")
    cid = _create_connection(admin)  # draft：无 access token
    bid = _bind(admin, cid).json()["id"]
    out = client.post(f"/api/channels/{bid}/test", headers=admin)
    assert out.status_code == 200  # 不 5xx
    body = out.json()
    assert body["ok"] is False and body["status"] == "error"
    assert client.get(f"/api/channels/{bid}", headers=admin).json()["status"] == "error"


def test_u1132_channel_test_endpoint_requires_operate():
    """A-3（docs/89 §15）：`/test` 真出向＋把 binding.status 写成 connected/error，
    所以与兄弟端点 `/api/connections/{id}/test` 同档＝operate；只读角色 403。

    判别对照＝operator 拿到与 admin 一样的 200 体形状，防止"谁都拒"被当成"收口了"。
    """
    admin = _login("admin")
    cid = _create_connection(admin)
    bid = _bind(admin, cid).json()["id"]

    viewer = _login("viewer")
    assert client.post(f"/api/channels/{bid}/test", headers=viewer).status_code == 403

    operator = _login("operator")
    out = client.post(f"/api/channels/{bid}/test", headers=operator)
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "error"  # draft 连接测不通，但确实打了、也写了状态


def test_delete_and_adapters_discovery():
    admin = _login("admin")
    cid = _create_connection(admin)
    bid = _bind(admin, cid).json()["id"]
    adapters = client.get("/api/adapters", headers=admin).json()
    assert any(a["id"] == f"channel:shopify:{bid}" for a in adapters)

    assert client.delete(f"/api/channels/{bid}", headers=admin).json() == {"deleted": True}
    assert client.delete(f"/api/channels/{bid}", headers=admin).status_code == 404
    adapters2 = client.get("/api/adapters", headers=admin).json()
    assert not any(a["id"] == f"channel:shopify:{bid}" for a in adapters2)


def test_bind_writes_audit_and_survives_reset():
    admin = _login("admin")
    cid = _create_connection(admin)
    assert _bind(admin, cid).status_code == 201
    audit = client.get("/api/audit/events", headers=admin).json()["items"]
    assert any(row["action"] == "channel.bind" for row in audit)

    assert client.post("/api/demo/reset", headers=admin).status_code == 200
    items = client.get("/api/channels", headers=admin).json()["items"]
    assert len(items) == 1  # 绑定 reset 不清除


# ----------------------------------------------------------- 打包 BJ：U1168 / U1170

_REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def _runtime_keys() -> set[str]:
    import json
    return set(json.loads((_REPO / "frontend/src/locales/zh-CN/runtime.json").read_text()))


def test_u1168_bind_twice_409_detail_carries_code_and_is_cataloged():
    admin = _login("admin")
    cid = _create_connection(admin)
    assert _bind(admin, cid).status_code == 201
    r = _bind(admin, cid)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert isinstance(detail, dict) and detail["code"] == "CHANNEL_ALREADY_BOUND"
    # 折叠点结构化后，码进得了响应体、且在目录里 → 前端可本地化（不再是中文裸串）。
    assert "CHANNEL_ALREADY_BOUND" in _runtime_keys()


def test_u1170_unknown_binding_404_code_in_body_but_no_catalog_key():
    admin = _login("admin")
    r = client.get("/api/channels/ch-nope", headers=admin)
    assert r.status_code == 404
    detail = r.json()["detail"]
    # CHANNEL_NOT_BOUND 是 B 堆粗码（一码两答案）：本批结构化让 code 出体，但目录
    # 无键、前端按既有守卫回退中文 message。钉住这个中间态，防"假绿说已译"。
    assert detail["code"] == "CHANNEL_NOT_BOUND"
    assert "CHANNEL_NOT_BOUND" not in _runtime_keys()

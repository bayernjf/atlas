# -*- coding: utf-8 -*-
"""打包 BQ（docs/133 D76+D71）：网页嵌入只读面＋NL 造工具（U1477–U1488）。

覆盖（docs/133 §5 验收逐条）：
- U1477  嵌入配置 GET/PUT：缺省 enabled=false（fail-closed）；ttl 1–86400 门控；upsert；
- U1478  短链生成：启用后 POST /api/embeds 返 token/tokenUrl；未启用 422；
- U1479  公开只读面：验签通过 → 图元信息＋最近运行摘要；不含密钥/决策/草稿键；
- U1480  验签失败统一 404（malformed/bad signature/expired 三态同一 404）；
- U1481  TTL 生效：过期 token 404；token_ttl_seconds 配置化上限 86400；
- U1482  AdapterDefinition 校验：非法 schema 形状/未知 keyword/缺 capabilities 拒绝；
- U1483  NL 草稿：LLM 成功 → 201＋ad-N；LLM 失败显式 422（不静默降级）；
- U1484  dry-run：2xx 可达；非 2xx/传输失败 → reachable=false＋原因；不改 decision；
- U1485  采纳：admin adopt/dismiss、可改判；非 admin 403；不存在 404；
- U1486  运行时装配：adopted 草稿在 runtime registry 出现 generated:{draft_id}；dismissed 不出现；
- U1487  F-P2 最小权限：auth_type≠none 未 authConfirmed → adopt 422；确认后成功；
- U1488  reset 清空嵌入配置与草稿。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app, _runtime_registry
from atlas.harness.definition import (
    AdapterDefinitionError,
    validate_adapter_definition,
)

client = TestClient(app)

_VALID_DEFINITION = {
    "name": "shopify-orders-api",
    "description": "查订单",
    "base_url": "https://api.example.test",
    "auth_type": "none",
    "permission": "read",
    "capabilities": [
        {
            "name": "get_orders",
            "description": "按状态查订单",
            "method": "GET",
            "path": "/orders",
            "input_schema": {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
            },
            "output_schema": {
                "type": "object",
                "properties": {"orders": {"type": "array", "items": {"type": "object"}}},
            },
            "permission": "read",
            "is_idempotent": True,
        }
    ],
}


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


ADMIN = _login("admin")
OPERATOR = _login("operator")
VIEWER = _login("viewer")


@pytest.fixture(autouse=True)
def _setup():
    from atlas.iam.deps import tenant_registry

    client.post("/api/demo/reset", headers=ADMIN)
    services = tenant_registry.get("t1")
    services.embed_store.reset()
    services.adapter_drafts.reset()
    yield
    client.headers.pop("authorization", None)


def _make_graph() -> str:
    from atlas.template.graphs import refund_template_graph

    raw = refund_template_graph()
    resp = client.post("/api/graphs", json=raw, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _enable_embed(graph_id: str, *, ttl: int = 3600) -> dict:
    resp = client.put(
        "/api/embeds/config",
        json={"graph_id": graph_id, "enabled": True, "token_ttl_seconds": ttl},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _token_url(graph_id: str) -> str:
    resp = client.post("/api/embeds", json={"graph_id": graph_id}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()["tokenUrl"]


def _valid_definition_payload(auth_type: str = "none") -> dict:
    payload = json.loads(json.dumps(_VALID_DEFINITION))
    payload["auth_type"] = auth_type
    return payload


def _mock_llm(monkeypatch, payload: dict) -> None:
    # generate_adapter_definition 先走 _resolve_model（无配置即抛），再 _call_llm；
    # 两处一起 mock，成功路径才走得通。
    monkeypatch.setattr(
        "atlas.llm.structured._resolve_model",
        lambda *a, **k: ("test-model", None, None),
    )
    monkeypatch.setattr(
        "atlas.llm.structured._call_llm",
        lambda *a, **k: json.dumps(payload),
    )


def _create_draft(monkeypatch, *, auth_type: str = "none", payload: dict | None = None) -> dict:
    _mock_llm(monkeypatch, payload or _valid_definition_payload(auth_type))
    resp = client.post(
        "/api/adapter-drafts", json={"prompt": "做查订单能力"}, headers=OPERATOR
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── D76 嵌入配置 ───────────────────────────────────────────────


def test_u1477_embed_config_default_fail_closed_and_ttl_gate():
    graph_id = _make_graph()
    # 缺省 enabled=false：GET 空列表；PUT 开
    resp = client.get("/api/embeds/config", headers=ADMIN)
    assert resp.status_code == 200 and resp.json()["items"] == []
    config = _enable_embed(graph_id, ttl=999999)
    assert config["enabled"] is True
    # ttl 1–86400 门控：超上限截断（fail-closed 方向＝不放宽）
    config = _enable_embed(graph_id, ttl=999999)
    assert config["token_ttl_seconds"] == 86400
    config = _enable_embed(graph_id, ttl=0)
    assert config["token_ttl_seconds"] == 1
    listed = client.get("/api/embeds/config", headers=ADMIN).json()["items"]
    assert any(c["graph_id"] == graph_id and c["enabled"] for c in listed)


def test_u1478_embed_token_requires_enabled():
    graph_id = _make_graph()
    # 未启用 → 422
    resp = client.post("/api/embeds", json={"graph_id": graph_id}, headers=ADMIN)
    assert resp.status_code == 422
    _enable_embed(graph_id)
    resp = client.post("/api/embeds", json={"graph_id": graph_id}, headers=ADMIN)
    assert resp.status_code == 201
    body = resp.json()
    assert body["token"] and body["tokenUrl"].startswith("http")
    # 非 admin 403
    assert (
        client.post("/api/embeds", json={"graph_id": graph_id}, headers=OPERATOR).status_code
        == 403
    )


def test_u1479_embed_readonly_projection():
    graph_id = _make_graph()
    _enable_embed(graph_id)
    url = _token_url(graph_id)
    token = url.rsplit("/", 1)[-1]
    resp = client.get(f"/api/embeds/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["graphId"] == graph_id
    assert isinstance(body["nodeCount"], int)
    assert isinstance(body["variables"], list)
    # 不含密钥/决策/草稿键（只读投影白名单）
    for secret_key in ("credentialEnvelopes", "decision", "definition", "drafts", "token"):
        assert secret_key not in body


def test_u1480_embed_token_verify_failures_uniform_404():
    graph_id = _make_graph()
    _enable_embed(graph_id)
    url = _token_url(graph_id)
    token = url.rsplit("/", 1)[-1]
    # 篡改签名：载荷合法（能解析）但签名错——专门覆盖"签名校验"这道关
    payload, sig = token.split(".", 1)
    tampered = f"{payload}.{'A' * len(sig)}"
    for bad in ("garbage", "a.b", "aaa.bbb", "x.y.z", tampered):
        resp = client.get(f"/api/embeds/{bad}")
        assert resp.status_code == 404, bad


def test_u1481_embed_token_ttl_expiry():
    graph_id = _make_graph()
    _enable_embed(graph_id, ttl=1)
    from atlas.embeds.token import EmbedTokenIssuer

    class _Clock:
        now = 1000.0

    issuer = EmbedTokenIssuer(secret="test-secret", clock=lambda: _Clock.now)
    token = issuer.issue("t1", graph_id, ttl_seconds=1)
    _Clock.now = 2000.0  # 远超 TTL+grace → expired
    resp = client.get(f"/api/embeds/{token}")
    assert resp.status_code == 404


# ── D71 造工具 ─────────────────────────────────────────────────


def test_u1482_definition_shape_validation():
    # 缺 capabilities
    bad = json.loads(json.dumps(_VALID_DEFINITION))
    bad["capabilities"] = []
    with pytest.raises(AdapterDefinitionError):
        validate_adapter_definition(bad)
    # 未知 schema keyword
    bad = json.loads(json.dumps(_VALID_DEFINITION))
    bad["capabilities"][0]["input_schema"] = {"type": "object", "magic": True}
    with pytest.raises(AdapterDefinitionError):
        validate_adapter_definition(bad)
    # 根 schema 不是 object
    bad = json.loads(json.dumps(_VALID_DEFINITION))
    bad["capabilities"][0]["input_schema"] = {"type": "string"}
    with pytest.raises(AdapterDefinitionError):
        validate_adapter_definition(bad)
    # auth_type 枚举外
    bad = json.loads(json.dumps(_VALID_DEFINITION))
    bad["auth_type"] = "cookie"
    with pytest.raises(AdapterDefinitionError):
        validate_adapter_definition(bad)
    # path 不以 / 开头
    bad = json.loads(json.dumps(_VALID_DEFINITION))
    bad["capabilities"][0]["path"] = "orders"
    with pytest.raises(AdapterDefinitionError):
        validate_adapter_definition(bad)
    # 合法定义通过
    ok = validate_adapter_definition(json.loads(json.dumps(_VALID_DEFINITION)))
    assert ok.name == "shopify-orders-api"
    assert ok.capabilities[0].path == "/orders"


def test_u1483_nl_draft_success_and_llm_failure_is_explicit(monkeypatch):
    draft = _create_draft(monkeypatch)
    assert draft["draft_id"].startswith("ad-")
    assert draft["definition"]["name"] == "shopify-orders-api"
    assert draft["decision_status"] is None
    # LLM 不可用 → 显式 422，不静默降级（_resolve_model 抛 LLMStructuredUnavailable）
    from atlas.llm.structured import LLMStructuredUnavailable

    def _raise_unavailable(*a, **k):
        raise LLMStructuredUnavailable("adapter_draft", "未配置 LLM 模型（测试）")

    monkeypatch.setattr("atlas.llm.structured._resolve_model", _raise_unavailable)
    resp = client.post("/api/adapter-drafts", json={"prompt": "x"}, headers=OPERATOR)
    assert resp.status_code == 422


def test_u1484_dry_run_reachability(monkeypatch):
    draft = _create_draft(monkeypatch)
    draft_id = draft["draft_id"]

    class _OkClient:
        def __init__(self, *a, **k):
            pass

        def request(self, **k):
            return {"status": 200, "headers": {}, "body": {}}

    monkeypatch.setattr("atlas.api.main.HttpApiClient", _OkClient)
    resp = client.post(f"/api/adapter-drafts/{draft_id}/dry-run", headers=OPERATOR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["reachability"]["ok"] is True
    assert resp.json()["decision_status"] is None  # dry-run 不改 decision

    # 非 2xx → 不可达＋原因
    class _ErrClient:
        def __init__(self, *a, **k):
            pass

        def request(self, **k):
            return {"status": 500, "headers": {}, "body": {}}

    monkeypatch.setattr("atlas.api.main.HttpApiClient", _ErrClient)
    resp = client.post(f"/api/adapter-drafts/{draft_id}/dry-run", headers=OPERATOR)
    assert resp.json()["reachability"]["ok"] is False
    assert "500" in resp.json()["reachability"]["reason"]


def test_u1485_adopt_dismiss_reroute_and_roles(monkeypatch):
    draft = _create_draft(monkeypatch)
    draft_id = draft["draft_id"]
    # 非 admin 403
    assert (
        client.put(
            f"/api/adapter-drafts/{draft_id}/decision",
            json={"decision": "adopted"},
            headers=OPERATOR,
        ).status_code
        == 403
    )
    # admin adopt
    resp = client.put(
        f"/api/adapter-drafts/{draft_id}/decision",
        json={"decision": "adopted"},
        headers=ADMIN,
    )
    assert resp.status_code == 200 and resp.json()["decision_status"] == "adopted"
    # 可改判 dismissed
    resp = client.put(
        f"/api/adapter-drafts/{draft_id}/decision",
        json={"decision": "dismissed"},
        headers=ADMIN,
    )
    assert resp.json()["decision_status"] == "dismissed"
    # 不存在 404
    assert (
        client.put(
            "/api/adapter-drafts/ad-999/decision",
            json={"decision": "adopted"},
            headers=ADMIN,
        ).status_code
        == 404
    )


def test_u1486_runtime_assembly_adopted_only(monkeypatch):
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get("t1")
    # dismissed 草稿不出现在 runtime registry
    dismissed = _create_draft(monkeypatch)
    client.put(
        f"/api/adapter-drafts/{dismissed['draft_id']}/decision",
        json={"decision": "dismissed"},
        headers=ADMIN,
    )
    registry = _runtime_registry(services)
    ids = {item["id"] for item in registry.list_adapters()}
    assert f"generated:{dismissed['draft_id']}" not in ids
    # adopted 草稿出现在 runtime registry
    adopted = _create_draft(monkeypatch)
    client.put(
        f"/api/adapter-drafts/{adopted['draft_id']}/decision",
        json={"decision": "adopted"},
        headers=ADMIN,
    )
    registry = _runtime_registry(services)
    adapter = registry.get(f"generated:{adopted['draft_id']}")
    assert adapter is not None
    names = {c.name for c in adapter.list_capabilities()}
    assert "get_orders" in names


def test_u1487_f2_minimal_permission_auth_confirmation(monkeypatch):
    # auth_type=header 未 authConfirmed → adopt 422
    draft = _create_draft(monkeypatch, auth_type="header")
    resp = client.put(
        f"/api/adapter-drafts/{draft['draft_id']}/decision",
        json={"decision": "adopted"},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    # authConfirmed=true → adopt 成功
    resp = client.put(
        f"/api/adapter-drafts/{draft['draft_id']}/decision",
        json={"decision": "adopted", "authConfirmed": True},
        headers=ADMIN,
    )
    assert resp.status_code == 200
    assert resp.json()["decision_status"] == "adopted"
    # 草稿缺省 permission 是 read（最小权限）
    assert draft["definition"]["permission"] == "read"


def test_u1488_reset_clears_embed_and_drafts(monkeypatch):
    graph_id = _make_graph()
    _enable_embed(graph_id)
    _create_draft(monkeypatch)
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get("t1")
    assert services.embed_store.list() != []
    assert services.adapter_drafts.list() != []
    client.post("/api/demo/reset", headers=ADMIN)
    assert services.embed_store.list() == []
    assert services.adapter_drafts.list() == []

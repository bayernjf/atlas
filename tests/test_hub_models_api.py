"""W5-4 验收：内置模型由 hub 管理——PUT 停用(410)，GET 内置区读 hub 投影。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate
from atlas.llm import hub

client = TestClient(app)


def _auth(tenant: str = "t1", username: str = "admin-a", pw: str = "admin123") -> None:
    principal = authenticate(username, pw)
    assert principal is not None
    client.headers["Authorization"] = f"Bearer {session_store.issue(principal)}"


@pytest.fixture(autouse=True)
def _clean():
    from atlas.llm.config import get_model_config_store

    _auth()
    hub.reset_hub_projection()
    hub.stop_hub_refresh()
    yield
    hub.reset_hub_projection()
    hub.stop_hub_refresh()
    # 清理本文件通过 API 写入的 t1 BYOK，避免污染后续测试
    get_model_config_store().delete_byok("t1")
    client.headers.pop("Authorization", None)


def test_put_builtin_returns_410(monkeypatch):
    monkeypatch.delenv("MODEL_HUB_URL", raising=False)
    monkeypatch.delenv("MODEL_HUB_TOKEN", raising=False)
    resp = client.put(
        "/api/models/builtin",
        json={"model": "openai/m", "enabled": True},
    )
    assert resp.status_code == 410


def test_get_models_hub_unconfigured(monkeypatch):
    monkeypatch.delenv("MODEL_HUB_URL", raising=False)
    monkeypatch.delenv("MODEL_HUB_TOKEN", raising=False)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hub"]["configured"] is False
    assert data["hub"]["revision"] is None
    assert data["builtinModels"] == []


def test_get_models_hub_configured(monkeypatch):
    from tests.test_hub_projection import FakeHubClient, _text_model

    # 用一个简单的信封 provider 桩
    import atlas.connections.service as conn_service

    class _Provider:
        def encrypt(self, p):
            return f"enc${p}"

        def decrypt(self, e):
            return e[4:]

    monkeypatch.setattr(conn_service, "get_secret_provider", lambda: _Provider())

    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    models = [
        _text_model("asn_1", "noul", "ark-code-latest", key="sk-plan"),
        _text_model(
            "asn_2", "score", "doubao-raw",
            litellm_id="openai/doubao", key="sk-payg"
        ),
    ]
    hub.sync_hub_projection(FakeHubClient(models))

    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hub"]["configured"] is True
    assert data["hub"]["revision"] == 42
    assert data["builtin"] is None
    ids = sorted(m["assignmentId"] for m in data["builtinModels"])
    assert ids == ["asn_1", "asn_2"]
    # 只读视图不含任何 key 字段
    import json

    assert "sk-plan" not in json.dumps(data)
    assert "sk-payg" not in json.dumps(data)


def test_byok_endpoint_still_works(monkeypatch):
    """租户 BYOK 端点零改动：GET/PUT 仍可用。"""
    get_resp = client.get("/api/models/byok")
    assert get_resp.status_code == 200
    put_resp = client.put(
        "/api/models/byok",
        json={"model": "openai/byok-m", "apiKey": "sk-byok", "enabled": True},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["model"] == "openai/byok-m"

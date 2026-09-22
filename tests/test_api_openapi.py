# -*- coding: utf-8 -*-
"""OpenAPI 导入 REST 端点测试（docs/42 §5；U390 API 段）。

URL 抓取经 monkeypatch 替换 _fetch_openapi_spec，零真实网络。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from atlas.api import main as api_main
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate
from atlas.openapi.errors import OpenApiError

client = TestClient(api_main.app)


def _token(username: str, password: str) -> str:
    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


def _auth(username: str, password: str) -> dict[str, str]:
    token = _token(username, password)
    return {"Authorization": f"Bearer {token}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")


@pytest.fixture(autouse=True)
def _clear_openapi_imports():
    for tenant in ("t1", "t2"):
        store = tenant_registry.get(tenant).openapi_imports
        store._specs.clear()
        store._counter = 0
    yield


DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://petstore.example.com/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List pets",
                "parameters": [
                    {"name": "limit", "in": "query",
                     "schema": {"type": "integer"}}
                ],
            }
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "getPetById",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    {"name": "q", "in": "query", "schema": {
                        "oneOf": [{"type": "string"}, {"type": "integer"}]}},
                ],
            }
        },
    },
}


def _content() -> dict[str, str]:
    return {"content": json.dumps(DOC)}


def test_preview_pasted_document():
    response = client.post("/api/openapi/preview", json=_content(), headers=OPERATOR_A)

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Petstore"
    assert data["base_url"] == "https://petstore.example.com/v1"
    assert data["imported_count"] == 1
    assert data["skipped_count"] == 1
    by_name = {op["name"]: op for op in data["operations"]}
    assert by_name["list_pets"]["skipped"] is False
    assert by_name["get_pet_by_id"]["skipped"] is True
    assert "oneOf" in by_name["get_pet_by_id"]["skip_reason"]


def test_preview_invalid_document_returns_code():
    response = client.post(
        "/api/openapi/preview",
        json={"content": "openapi: 3.0.0"},
        headers=OPERATOR_A,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "OPENAPI_INVALID_DOCUMENT"


def test_preview_requires_exactly_one_source():
    response = client.post(
        "/api/openapi/preview",
        json={"content": "{}", "url": "https://x.example.com/s"},
        headers=OPERATOR_A,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "OPENAPI_INVALID_DOCUMENT"


def test_preview_from_url(monkeypatch):
    monkeypatch.setattr(
        api_main, "_fetch_openapi_spec", lambda url: json.dumps(DOC)
    )

    response = client.post(
        "/api/openapi/preview",
        json={"url": "https://petstore.example.com/openapi.json"},
        headers=OPERATOR_A,
    )

    assert response.status_code == 200
    assert response.json()["imported_count"] == 1


def test_preview_fetch_failure_mapped(monkeypatch):
    def fail(url):
        raise OpenApiError("OPENAPI_FETCH_FAILED", "规格抓取失败：x")

    monkeypatch.setattr(api_main, "_fetch_openapi_spec", fail)

    response = client.post(
        "/api/openapi/preview",
        json={"url": "https://petstore.example.com/openapi.json"},
        headers=OPERATOR_A,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "OPENAPI_FETCH_FAILED"


def test_import_creates_spec_with_successful_operations_only():
    response = client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    assert response.status_code == 201
    data = response.json()
    assert data["spec_id"] == "openapi-1"
    assert [op["name"] for op in data["operations"]] == ["list_pets"]
    assert data["created_at"]


def test_import_all_skipped_rejected():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "x"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [
            {"name": "q", "in": "query", "schema": {"oneOf": [
                {"type": "string"}, {"type": "integer"}]}}]}}},
    }

    response = client.post(
        "/api/openapi/imports",
        json={"content": json.dumps(doc)},
        headers=OPERATOR_A,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "OPENAPI_NO_IMPORTABLE_OPERATION"


def test_list_get_and_404():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    listing = client.get("/api/openapi/imports", headers=VIEWER_A)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["spec_id"] == "openapi-1"

    one = client.get("/api/openapi/imports/openapi-1", headers=VIEWER_A)
    assert one.status_code == 200
    assert one.json()["title"] == "Petstore"

    missing = client.get("/api/openapi/imports/openapi-9", headers=VIEWER_A)
    assert missing.status_code == 404


def test_cross_tenant_get_is_404():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    response = client.get("/api/openapi/imports/openapi-1", headers=ADMIN_B)
    assert response.status_code == 404
    assert client.get("/api/openapi/imports", headers=ADMIN_B).json()["items"] == []


def test_imported_adapter_merged_into_discovery():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    adapters = client.get("/api/adapters", headers=VIEWER_A).json()
    assert any(item["id"] == "openapi:openapi-1" for item in adapters)


def test_delete_removes_from_store_and_discovery():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    response = client.delete("/api/openapi/imports/openapi-1", headers=ADMIN_A)
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    assert client.get(
        "/api/openapi/imports/openapi-1", headers=VIEWER_A
    ).status_code == 404
    adapters = client.get("/api/adapters", headers=VIEWER_A).json()
    assert all(item["id"] != "openapi:openapi-1" for item in adapters)


def test_delete_missing_returns_404():
    response = client.delete("/api/openapi/imports/openapi-9", headers=ADMIN_A)
    assert response.status_code == 404


def test_role_gates():
    assert client.post(
        "/api/openapi/preview", json=_content(), headers=VIEWER_A
    ).status_code == 403
    assert client.post(
        "/api/openapi/imports", json=_content(), headers=VIEWER_A
    ).status_code == 403
    assert client.delete(
        "/api/openapi/imports/openapi-1", headers=OPERATOR_A
    ).status_code in (403, 404)


def test_import_post_writes_audit_record():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)

    records = tenant_registry.get("t1").audit_store.list(
        action_prefix="POST /api/openapi/imports"
    )
    assert any(record["statusCode"] == 201 for record in records)

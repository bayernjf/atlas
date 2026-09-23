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


# --- static security schemes / credential envelopes（docs/44） -------------

SECRET_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Secured", "version": "1.0.0"},
    "servers": [{"url": "https://secured.example.com"}],
    "paths": {"/things": {"get": {"operationId": "getThings"}}},
    "components": {
        "securitySchemes": {
            "KeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
    },
    "security": [{"KeyHeader": []}, {"BearerAuth": []}],
}


def _secret_content(credentials=None) -> dict:
    body = {"content": json.dumps(SECRET_DOC)}
    if credentials is not None:
        body["credentials"] = credentials
    return body


def test_preview_projects_schemes_without_secret_values():
    response = client.post(
        "/api/openapi/preview",
        json={"content": json.dumps(SECRET_DOC)},
        headers=OPERATOR_A,
    )
    assert response.status_code == 200
    schemes = {s["name"]: s for s in response.json()["security_schemes"]}
    assert set(schemes) == {"KeyHeader", "BearerAuth"}
    assert schemes["KeyHeader"]["kind"] == "api_key"
    assert schemes["KeyHeader"]["param"] == "X-API-Key"
    assert schemes["BearerAuth"]["kind"] == "bearer"
    raw = response.text
    assert "secret" not in raw.lower()


def test_import_with_credentials_encrypts_values():
    response = client.post(
        "/api/openapi/imports",
        json=_secret_content({"KeyHeader": "top-secret-key"}),
        headers=OPERATOR_A,
    )
    assert response.status_code == 201
    data = response.json()
    assert set(data["security_schemes"]) == {"KeyHeader", "BearerAuth"}
    envelopes = data["credential_envelopes"]
    assert set(envelopes) == {"KeyHeader"}
    assert "top-secret-key" not in response.text


def test_import_without_credentials_succeeds():
    response = client.post(
        "/api/openapi/imports",
        json=_secret_content(),
        headers=OPERATOR_A,
    )
    assert response.status_code == 201
    assert response.json()["credential_envelopes"] == {}


def test_import_unknown_scheme_rejected():
    response = client.post(
        "/api/openapi/imports",
        json=_secret_content({"Nope": "v"}),
        headers=OPERATOR_A,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "OPENAPI_INVALID_CREDENTIAL"
    assert client.get("/api/openapi/imports", headers=VIEWER_A).json()["items"] == []


def test_put_credentials_upsert_delete_and_404():
    created = client.post(
        "/api/openapi/imports",
        json=_secret_content(),
        headers=OPERATOR_A,
    ).json()
    spec_id = created["spec_id"]

    upsert = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"KeyHeader": "k1", "BearerAuth": "b1"}},
        headers=OPERATOR_A,
    )
    assert upsert.status_code == 200
    assert upsert.json()["configured"] == ["KeyHeader", "BearerAuth"]
    assert "k1" not in upsert.text and "b1" not in upsert.text

    one = client.get(f"/api/openapi/imports/{spec_id}", headers=VIEWER_A).json()
    assert set(one["credential_envelopes"]) == {"KeyHeader", "BearerAuth"}

    delete = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"KeyHeader": ""}},
        headers=OPERATOR_A,
    )
    assert delete.status_code == 200
    assert delete.json()["configured"] == ["BearerAuth"]

    missing = client.put(
        "/api/openapi/imports/openapi-9/credentials",
        json={"credentials": {}},
        headers=OPERATOR_A,
    )
    assert missing.status_code == 404

    bad_scheme = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"Nope": "v"}},
        headers=OPERATOR_A,
    )
    assert bad_scheme.status_code == 422
    assert bad_scheme.json()["detail"]["code"] == "OPENAPI_INVALID_CREDENTIAL"


BASIC_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Basic Secured", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/profile": {"get": {"operationId": "getProfile"}},
    },
    "components": {
        "securitySchemes": {
            "BasicAuth": {"type": "http", "scheme": "basic"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
    },
    "security": [{"BasicAuth": []}, {"BearerAuth": []}],
}


def _basic_import():
    return client.post(
        "/api/openapi/imports",
        json={"content": json.dumps(BASIC_DOC)},
        headers=OPERATOR_A,
    ).json()


def test_put_basic_credentials_encrypts_object():
    spec_id = _basic_import()["spec_id"]
    response = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"BasicAuth": {"username": "alice", "password": "wonderland"}}},
        headers=OPERATOR_A,
    )
    assert response.status_code == 200
    assert response.json()["configured"] == ["BasicAuth"]
    stored = client.get(f"/api/openapi/imports/{spec_id}", headers=VIEWER_A).json()
    assert set(stored["credential_envelopes"]) == {"BasicAuth"}
    assert "alice" not in response.text and "wonderland" not in response.text


def test_put_basic_credentials_requires_both_fields():
    spec_id = _basic_import()["spec_id"]
    for bad in ({"username": "alice"}, {"password": "x"}, {"username": "", "password": "x"}):
        response = client.put(
            f"/api/openapi/imports/{spec_id}/credentials",
            json={"credentials": {"BasicAuth": bad}},
            headers=OPERATOR_A,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "OPENAPI_INVALID_CREDENTIAL"


def test_put_basic_null_or_string_deletes_envelope():
    spec_id = _basic_import()["spec_id"]
    client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"BasicAuth": {"username": "a", "password": "b"}}},
        headers=OPERATOR_A,
    )
    response = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={"credentials": {"BasicAuth": None}},
        headers=OPERATOR_A,
    )
    assert response.status_code == 200
    assert response.json()["configured"] == []
    stored = client.get(f"/api/openapi/imports/{spec_id}", headers=VIEWER_A).json()
    assert stored["credential_envelopes"] == {}


def test_put_basic_and_bearer_distinguished_by_name():
    spec_id = _basic_import()["spec_id"]
    response = client.put(
        f"/api/openapi/imports/{spec_id}/credentials",
        json={
            "credentials": {
                "BasicAuth": {"username": "a", "password": "b"},
                "BearerAuth": "token-xyz",
            }
        },
        headers=OPERATOR_A,
    )
    assert response.status_code == 200
    assert response.json()["configured"] == ["BasicAuth", "BearerAuth"]


def test_no_response_ever_contains_plaintext_secret():
    secret = "PLAINTEXT-PROBE-8675309"
    client.post(
        "/api/openapi/imports",
        json=_secret_content({"KeyHeader": secret, "BearerAuth": secret}),
        headers=OPERATOR_A,
    )
    for response_text in (
        client.get("/api/openapi/imports", headers=VIEWER_A).text,
        client.get("/api/openapi/imports/openapi-1", headers=VIEWER_A).text,
        client.get("/api/adapters", headers=VIEWER_A).text,
    ):
        assert secret not in response_text


# --- docs/56 §3：去重 409、软删除与恢复端点 ---
def test_duplicate_import_returns_409_with_existing_id():
    first = client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)
    assert first.status_code == 201
    again = client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert detail["code"] == "OPENAPI_DUPLICATE"
    assert detail["existingSpecId"] == "openapi-1"


def test_soft_delete_reimport_restore_conflict_then_restore():
    assert client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A).status_code == 201
    # 软删 openapi-1
    assert client.delete("/api/openapi/imports/openapi-1", headers=ADMIN_A).status_code == 200
    assert client.get("/api/openapi/imports/openapi-1", headers=VIEWER_A).status_code == 404
    assert client.get("/api/openapi/imports", headers=VIEWER_A).json()["items"] == []
    # 软删后同内容可重新导入为 openapi-2
    second = client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)
    assert second.status_code == 201 and second.json()["spec_id"] == "openapi-2"
    # openapi-2 未删时恢复 openapi-1 → 409 冲突，回传 openapi-2
    conflict = client.post("/api/openapi/imports/openapi-1/restore", headers=ADMIN_A)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["existingSpecId"] == "openapi-2"
    assert client.get("/api/openapi/imports/openapi-1", headers=VIEWER_A).status_code == 404
    # 删掉 openapi-2 后恢复 openapi-1 成功
    assert client.delete("/api/openapi/imports/openapi-2", headers=ADMIN_A).status_code == 200
    restored = client.post("/api/openapi/imports/openapi-1/restore", headers=ADMIN_A)
    assert restored.status_code == 200 and restored.json() == {"restored": True}
    assert client.get("/api/openapi/imports/openapi-1", headers=VIEWER_A).status_code == 200


def test_restore_missing_or_active_returns_404():
    assert client.post("/api/openapi/imports/openapi-nope/restore", headers=ADMIN_A).status_code == 404
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)
    # 对未删规格 restore 也 404（无可恢复项）
    assert client.post("/api/openapi/imports/openapi-1/restore", headers=ADMIN_A).status_code == 404


def test_restore_requires_administer():
    client.post("/api/openapi/imports", json=_content(), headers=OPERATOR_A)
    client.delete("/api/openapi/imports/openapi-1", headers=ADMIN_A)
    denied = client.post("/api/openapi/imports/openapi-1/restore", headers=OPERATOR_A)
    assert denied.status_code == 403

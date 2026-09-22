"""ImportStore 与导入 API 适配器执行测试（docs/42 §5，U390 中段）。

外呼经 httpx MockTransport；出向域名经假 resolver 返回公网 IP，零真实网络。
"""

from __future__ import annotations

import json

import httpx
import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.httpapi.resilience import RetryPolicy
from atlas.httpapi.service import HttpApiClient
from atlas.openapi.adapter import ImportedApiHarnessAdapter
from atlas.openapi.parser import parse_document
from atlas.openapi.store import (
    MAX_OPERATIONS_PER_SPEC,
    MAX_SPECS_PER_TENANT,
    ImportStore,
    ImportStoreError,
)
from atlas.security.egress import EgressGuard

PUBLIC_RESOLVER = lambda host: ["93.184.216.34"]

SPEC = {
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
                     "schema": {"type": "integer"}},
                    {"name": "tags", "in": "query", "schema": {
                        "type": "array", "items": {"type": "string"}}},
                ],
            },
            "post": {
                "operationId": "createPet",
                "summary": "Create a pet",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }}},
                },
            },
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "getPetById",
                "summary": "Get a pet",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    {"name": "x-trace", "in": "header",
                     "schema": {"type": "string"}},
                ],
            }
        },
    },
}


def _parsed():
    return parse_document(json.dumps(SPEC))


def make_client(handler, *, egress=None):
    return HttpApiClient(
        base_url="https://petstore.example.com/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        egress=egress,
        retry=RetryPolicy(sleep=lambda _: None),
    )


def make_adapter(handler, imported=None, *, egress=None):
    imported = imported or ImportStore().add(_parsed())
    client = make_client(handler, egress=egress)
    return ImportedApiHarnessAdapter(
        imported,
        client=client,
        granted_permissions={Permission.READ, Permission.WRITE},
    )


# --- ImportStore ---

def test_add_assigns_sequential_ids_and_persists_fields():
    store = ImportStore()
    imported = store.add(_parsed())
    assert imported.spec_id == "openapi-1"
    assert imported.title == "Petstore"
    assert imported.base_url == "https://petstore.example.com/v1"
    assert [op.name for op in imported.operations] == [
        "list_pets", "create_pet", "get_pet_by_id"
    ]
    assert imported.created_at
    assert store.add(_parsed()).spec_id == "openapi-2"


def test_add_filters_out_skipped_operations():
    document = {
        "openapi": "3.0.0",
        "info": {"title": "mix"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [
            {"name": "q", "in": "query", "schema": {"oneOf": [
                {"type": "string"}, {"type": "integer"}]}}]}}},
    }
    imported = ImportStore().add(parse_document(json.dumps(document)))
    assert imported.operations == []


def test_list_get_delete():
    store = ImportStore()
    imported = store.add(_parsed())
    assert store.list() == [imported]
    assert store.get("openapi-1") == imported
    assert store.get("missing") is None
    assert store.delete("openapi-1") is True
    assert store.delete("openapi-1") is False
    assert store.list() == []


def test_spec_limit_enforced():
    store = ImportStore()
    for _ in range(MAX_SPECS_PER_TENANT):
        store.add(_parsed())
    with pytest.raises(ImportStoreError) as exc:
        store.add(_parsed())
    assert exc.value.code == "OPENAPI_LIMIT_EXCEEDED"
    assert exc.value.status_code == 422


def test_operation_limit_enforced():
    paths = {}
    for i in range(MAX_OPERATIONS_PER_SPEC + 1):
        paths[f"/r{i}"] = {"get": {"operationId": f"op{i}"}}
    document = {
        "openapi": "3.0.0",
        "info": {"title": "large"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": paths,
    }
    with pytest.raises(ImportStoreError) as exc:
        ImportStore().add(parse_document(json.dumps(document)))
    assert exc.value.code == "OPENAPI_LIMIT_EXCEEDED"


# --- adapter capabilities ---

def test_capabilities_built_from_descriptors():
    adapter = make_adapter(lambda r: httpx.Response(200))
    capabilities = adapter.list_capabilities()
    by_name = {c.name: c for c in capabilities}
    assert set(by_name) == {"list_pets", "create_pet", "get_pet_by_id"}
    assert by_name["list_pets"].permission is Permission.READ
    assert by_name["list_pets"].is_idempotent is True
    assert by_name["create_pet"].permission is Permission.WRITE
    assert by_name["create_pet"].is_idempotent is False
    assert adapter.adapter_id == "openapi:openapi-1"
    assert adapter.adapter_type == "api"


# --- execution ---

def test_get_renders_query_parameters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"pets": []}, request=request)

    adapter = make_adapter(handler)
    result = adapter.execute(ActionRequest(
        capability_name="list_pets", parameters={"limit": 10}))

    assert result.status is ActionStatus.SUCCESS
    assert seen["url"] == "https://petstore.example.com/v1/pets?limit=10"
    assert result.output["body"] == {"pets": []}


def test_array_query_parameter_expanded():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[], request=request)

    adapter = make_adapter(handler)
    adapter.execute(ActionRequest(
        capability_name="list_pets",
        parameters={"tags": ["cat", "dog"]},
    ))

    assert "tags=cat" in seen["url"] and "tags=dog" in seen["url"]


def test_path_template_substituted_and_encoded():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={}, request=request)

    adapter = make_adapter(handler)
    adapter.execute(ActionRequest(
        capability_name="get_pet_by_id",
        parameters={"petId": "a/b 1"},
    ))

    assert seen["url"] == "https://petstore.example.com/v1/pets/a%2Fb%201"


def test_missing_path_parameter_fails():
    adapter = make_adapter(lambda r: httpx.Response(200))
    result = adapter.execute(ActionRequest(
        capability_name="get_pet_by_id", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "OPENAPI_INVALID_PARAMETER"


def test_header_parameters_sent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-trace"] = request.headers.get("x-trace")
        return httpx.Response(200, json={}, request=request)

    adapter = make_adapter(handler)
    adapter.execute(ActionRequest(
        capability_name="get_pet_by_id",
        parameters={"petId": 1, "x-trace": "trace-1"},
    ))

    assert seen["x-trace"] == "trace-1"


def test_body_sent_as_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content-type"] = request.headers.get("content-type")
        seen["content"] = request.content
        return httpx.Response(201, json={"id": 1}, request=request)

    adapter = make_adapter(handler)
    result = adapter.execute(ActionRequest(
        capability_name="create_pet",
        parameters={"body": {"name": "Rex"}},
    ))

    assert seen["content-type"] == "application/json"
    assert json.loads(seen["content"]) == {"name": "Rex"}
    assert result.output["status"] == 201


def test_4xx_response_is_still_success():
    adapter = make_adapter(
        lambda r: httpx.Response(404, json={"detail": "gone"}, request=r))
    result = adapter.execute(ActionRequest(
        capability_name="get_pet_by_id", parameters={"petId": 99}))

    assert result.status is ActionStatus.SUCCESS
    assert result.output["status"] == 404
    assert result.output["body"] == {"detail": "gone"}


def test_timeout_mapped_to_failed_observation():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = make_adapter(handler)
    result = adapter.execute(ActionRequest(
        capability_name="list_pets", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "HTTP_TIMEOUT"


def test_egress_denied_mapped_to_failed_observation():
    guard = EgressGuard(
        allowlist=["other.example.com"], resolver=PUBLIC_RESOLVER
    )
    adapter = make_adapter(
        lambda r: httpx.Response(200, json={}, request=r), egress=guard)
    result = adapter.execute(ActionRequest(
        capability_name="list_pets", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "EGRESS_DENIED"


def test_unknown_capability_fails():
    adapter = make_adapter(lambda r: httpx.Response(200))
    result = adapter.execute(ActionRequest(
        capability_name="nope", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_observe_reports_spec_metadata():
    adapter = make_adapter(lambda r: httpx.Response(200))
    observation = adapter.observe()

    assert observation.url == "https://petstore.example.com/v1"
    assert observation.data["spec_id"] == "openapi-1"

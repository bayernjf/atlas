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


def _parsed_variant(index: int):
    """同结构、不同 base_url → 指纹不同，可重复导入（docs/56 去重后旧用例适配）。"""
    import copy

    doc = copy.deepcopy(SPEC)
    doc["servers"][0]["url"] = f"https://petstore{index}.example.com/v1"
    return parse_document(json.dumps(doc))


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
    assert store.add(_parsed_variant(2)).spec_id == "openapi-2"


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
    for i in range(MAX_SPECS_PER_TENANT):
        store.add(_parsed_variant(i))
    with pytest.raises(ImportStoreError) as exc:
        store.add(_parsed_variant(99))
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


# --- security scheme credential injection（docs/44） -----------------------

from atlas.security.secrets import PlaintextSecretProvider

SECURE_SPEC_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Secured", "version": "1.0.0"},
    "servers": [{"url": "https://secured.example.com"}],
    "paths": {
        "/header": {
            "get": {
                "operationId": "headerOp",
                "parameters": [
                    {
                        "name": "X-API-Key",
                        "in": "header",
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
        "/query": {"get": {"operationId": "queryOp"}},
        "/bearer": {"get": {"operationId": "bearerOp"}},
        "/open": {"get": {"operationId": "openOp"}},
    },
    "components": {
        "securitySchemes": {
            "KeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "KeyQuery": {"type": "apiKey", "in": "query", "name": "key"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
    },
    "security": [{"KeyHeader": []}],
}


class CountingProvider:
    def __init__(self):
        self.decrypt_calls = 0

    def encrypt(self, plaintext: str) -> str:
        return f"env:{plaintext}"

    def decrypt(self, envelope: str) -> str:
        self.decrypt_calls += 1
        assert envelope.startswith("env:")
        return envelope[4:]

    def get_secret(self, name: str) -> str:
        raise KeyError(name)


def _secure_imported(envelopes=None):
    parsed = parse_document(json.dumps(SECURE_SPEC_DOC))
    provider = PlaintextSecretProvider()
    sealed = {name: provider.encrypt(value) for name, value in (envelopes or {}).items()}
    store = ImportStore()
    return store.add(parsed, envelopes=sealed)


def _secure_adapter(handler, imported=None, provider=None):
    imported = imported or _secure_imported()
    client = HttpApiClient(
        base_url="https://secured.example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        retry=RetryPolicy(sleep=lambda _: None),
    )
    return ImportedApiHarnessAdapter(
        imported,
        client=client,
        secret_provider=provider or PlaintextSecretProvider(),
        granted_permissions={Permission.READ, Permission.WRITE},
    )


def test_api_key_header_injected():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={}, request=request)

    adapter = _secure_adapter(handler, _secure_imported({"KeyHeader": "secret-key"}))
    result = adapter.execute(ActionRequest(capability_name="header_op", parameters={}))
    assert result.status is ActionStatus.SUCCESS
    assert seen["key"] == "secret-key"


def test_api_key_query_injected():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={}, request=request)

    doc = json.loads(json.dumps(SECURE_SPEC_DOC))
    doc["security"] = [{"KeyQuery": []}]
    parsed = parse_document(json.dumps(doc))
    provider = PlaintextSecretProvider()
    imported = ImportStore().add(
        parsed, envelopes={"KeyQuery": provider.encrypt("query-secret")}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(ActionRequest(capability_name="query_op", parameters={}))
    assert result.status is ActionStatus.SUCCESS
    assert seen["url"] == "https://secured.example.com/query?key=query-secret"


def test_bearer_header_injected():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={}, request=request)

    doc = json.loads(json.dumps(SECURE_SPEC_DOC))
    doc["security"] = [{"BearerAuth": []}]
    parsed = parse_document(json.dumps(doc))
    provider = PlaintextSecretProvider()
    imported = ImportStore().add(
        parsed, envelopes={"BearerAuth": provider.encrypt("tok-123")}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(ActionRequest(capability_name="bearer_op", parameters={}))
    assert result.status is ActionStatus.SUCCESS
    assert seen["auth"] == "Bearer tok-123"


def test_basic_header_injected():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={}, request=request)

    doc = {
        "openapi": "3.0.3",
        "info": {"title": "Basic", "version": "1.0"},
        "servers": [{"url": "https://secured.example.com"}],
        "paths": {"/basic": {"get": {"operationId": "basic_op"}}},
        "components": {
            "securitySchemes": {"BasicAuth": {"type": "http", "scheme": "basic"}}
        },
        "security": [{"BasicAuth": []}],
    }
    parsed = parse_document(json.dumps(doc))
    provider = PlaintextSecretProvider()
    plaintext = json.dumps({"username": "alice", "password": "wonderland"})
    imported = ImportStore().add(
        parsed, envelopes={"BasicAuth": provider.encrypt(plaintext)}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(ActionRequest(capability_name="basic_op", parameters={}))
    assert result.status is ActionStatus.SUCCESS
    assert seen["auth"] == "Basic YWxpY2U6d29uZGVybGFuZA=="


def test_basic_corrupt_envelope_fails_without_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200)

    doc = {
        "openapi": "3.0.3",
        "info": {"title": "Basic", "version": "1.0"},
        "servers": [{"url": "https://secured.example.com"}],
        "paths": {"/basic": {"get": {"operationId": "basic_op"}}},
        "components": {
            "securitySchemes": {"BasicAuth": {"type": "http", "scheme": "basic"}}
        },
        "security": [{"BasicAuth": []}],
    }
    parsed = parse_document(json.dumps(doc))
    provider = PlaintextSecretProvider()
    imported = ImportStore().add(
        parsed, envelopes={"BasicAuth": provider.encrypt("not-json")}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(ActionRequest(capability_name="basic_op", parameters={}))
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "SECRET_DECRYPT_ERROR"
    assert called == []


def test_missing_credential_fails_without_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200)

    adapter = _secure_adapter(handler)
    result = adapter.execute(ActionRequest(capability_name="header_op", parameters={}))
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "OPENAPI_CREDENTIAL_MISSING"
    assert "KeyHeader" in result.error.message
    assert called == []


def test_empty_group_allows_anonymous():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={}, request=request)

    doc = json.loads(json.dumps(SECURE_SPEC_DOC))
    doc["security"] = [{"KeyHeader": []}, {}]
    imported = ImportStore().add(parse_document(json.dumps(doc)))
    adapter = _secure_adapter(handler, imported, PlaintextSecretProvider())
    result = adapter.execute(ActionRequest(capability_name="header_op", parameters={}))
    assert result.status is ActionStatus.SUCCESS
    assert seen["url"] == "https://secured.example.com/header"


def test_explicit_user_header_wins_over_credential():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={}, request=request)

    adapter = _secure_adapter(handler, _secure_imported({"KeyHeader": "stored"}))
    result = adapter.execute(
        ActionRequest(
            capability_name="header_op", parameters={"X-API-Key": "user-value"}
        )
    )
    assert result.status is ActionStatus.SUCCESS
    assert seen["key"] == "user-value"


def test_envelopes_decrypted_per_call():
    counting = CountingProvider()
    imported = _secure_imported({"KeyHeader": "abc"})
    # replace envelopes with counting-provider envelopes
    imported.credential_envelopes = {"KeyHeader": counting.encrypt("abc")}
    adapter = _secure_adapter(lambda r: httpx.Response(200, json={}, request=r), imported, counting)
    for _ in range(3):
        result = adapter.execute(ActionRequest(capability_name="header_op", parameters={}))
        assert result.status is ActionStatus.SUCCESS
    assert counting.decrypt_calls == 3

def _md5(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode()).hexdigest()


def _digest_doc():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Digest", "version": "1.0"},
        "servers": [{"url": "https://secured.example.com"}],
        "paths": {"/digest": {"get": {"operationId": "digest_op"}}},
        "components": {
            "securitySchemes": {"DigestAuth": {"type": "http", "scheme": "digest"}}
        },
        "security": [{"DigestAuth": []}],
    }


def test_digest_challenge_response_completes():
    import hashlib

    seen = {}
    realm, nonce, username, password = "atlas", "0a4f113b", "alice", "wonderland"

    def handler(request):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Digest "):
            challenge = (
                f'Digest realm="{realm}", nonce="{nonce}", '
                'qop="auth", algorithm=MD5'
            )
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": challenge},
                request=request,
            )
        fields = {}
        for item in auth[len("Digest "):].split(","):
            if "=" in item:
                key, val = item.split("=", 1)
                fields[key.strip()] = val.strip().strip('"')
        uri = request.url.raw_path.decode()
        ha1 = _md5(f"{username}:{realm}:{password}")
        ha2 = _md5(f"{request.method}:{uri}")
        expected = _md5(
            f"{ha1}:{nonce}:{fields['nc']}:{fields['cnonce']}:auth:{ha2}"
        )
        seen["valid"] = fields["response"] == expected
        seen["username"] = fields.get("username")
        seen["uri"] = fields.get("uri")
        return httpx.Response(200, json={"ok": True}, request=request)

    parsed = parse_document(json.dumps(_digest_doc()))
    provider = PlaintextSecretProvider()
    plaintext = json.dumps({"username": username, "password": password})
    imported = ImportStore().add(
        parsed, envelopes={"DigestAuth": provider.encrypt(plaintext)}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(
        ActionRequest(capability_name="digest_op", parameters={})
    )
    assert result.status is ActionStatus.SUCCESS
    assert seen["valid"] is True
    assert seen["username"] == username
    assert seen["uri"] == "/digest"
    assert result.output["status"] == 200


def test_digest_corrupt_envelope_fails_without_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200)

    parsed = parse_document(json.dumps(_digest_doc()))
    provider = PlaintextSecretProvider()
    imported = ImportStore().add(
        parsed, envelopes={"DigestAuth": provider.encrypt(json.dumps({"username": "alice"}))}
    )
    adapter = _secure_adapter(handler, imported, provider)
    result = adapter.execute(
        ActionRequest(capability_name="digest_op", parameters={})
    )
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "SECRET_DECRYPT_ERROR"
    assert called == []

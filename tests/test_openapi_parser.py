import pytest

from atlas.openapi.errors import OpenApiError
from atlas.openapi.parser import parse_document

PETSTORE = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://petstore.example.com/v1"}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List pets",
                "parameters": [
                    {"name": "limit", "in": "query", "required": False,
                     "schema": {"type": "integer", "minimum": 1, "maximum": 100}}
                ],
            },
            "post": {
                "operationId": "createPet",
                "summary": "Create a pet",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Pet"}
                        }
                    },
                },
            },
        },
        "/pets/{petId}": {
            "parameters": [
                {"name": "petId", "in": "path", "required": True,
                 "schema": {"type": "integer"}}
            ],
            "get": {
                "operationId": "getPetById",
                "summary": "Get a pet",
                "parameters": [
                    {"name": "x-trace", "in": "header", "required": False,
                     "schema": {"type": "string"}}
                ],
            },
            "delete": {"summary": "Delete a pet"},
        },
    },
    "components": {
        "schemas": {
            "Pet": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "id": {"type": "integer", "format": "int64"},
                    "name": {"type": "string", "minLength": 1},
                    "tag": {"type": "string", "nullable": True},
                    "status": {"type": "string", "enum": ["available", "sold"]},
                },
            }
        }
    },
}


def _parse(document):
    import json

    return parse_document(json.dumps(document))


def test_petstore_full_parse():
    spec = _parse(PETSTORE)
    assert spec.title == "Petstore"
    assert spec.version == "1.0.0"
    assert spec.base_url == "https://petstore.example.com/v1"
    names = [op.name for op in spec.operations]
    assert names == ["list_pets", "create_pet", "get_pet_by_id", "delete_pets_pet_id"]


def test_parameters_grouped_into_input_schema():
    spec = _parse(PETSTORE)
    get_pet = next(op for op in spec.operations if op.name == "get_pet_by_id")
    assert set(get_pet.input_schema["properties"]) == {"petId", "x-trace"}
    assert get_pet.input_schema["required"] == ["petId"]
    assert get_pet.locations == {"petId": "path", "x-trace": "header"}


def test_request_body_becomes_body_property():
    spec = _parse(PETSTORE)
    create = next(op for op in spec.operations if op.name == "create_pet")
    body = create.input_schema["properties"]["body"]
    assert body["properties"]["name"] == {"type": "string", "minLength": 1}
    assert body["properties"]["id"] == {"type": "integer"}
    assert "required" in create.input_schema
    assert create.input_schema["required"] == ["body"]


def test_format_and_nullable_stripped():
    spec = _parse(PETSTORE)
    create = next(op for op in spec.operations if op.name == "create_pet")
    tag = create.input_schema["properties"]["body"]["properties"]["tag"]
    assert tag == {"type": "string"}


def test_permission_and_idempotency_inference():
    spec = _parse(PETSTORE)
    by_name = {op.name: op for op in spec.operations}
    assert by_name["list_pets"].permission == "read"
    assert by_name["list_pets"].idempotent is True
    assert by_name["create_pet"].permission == "write"
    assert by_name["create_pet"].idempotent is False
    assert by_name["delete_pets_pet_id"].permission == "write"


def test_name_synthesis_without_operation_id():
    spec = _parse(PETSTORE)
    delete_op = next(op for op in spec.operations if op.method == "delete")
    assert delete_op.name == "delete_pets_pet_id"


def test_invalid_json_rejected():
    with pytest.raises(OpenApiError) as exc:
        parse_document("{not json")
    assert exc.value.code == "OPENAPI_INVALID_DOCUMENT"


def test_yaml_rejected_with_conversion_hint():
    yaml_doc = "openapi: 3.0.0\ninfo:\n  title: x\n"
    with pytest.raises(OpenApiError) as exc:
        parse_document(yaml_doc)
    assert exc.value.code == "OPENAPI_INVALID_DOCUMENT"
    assert "YAML" in exc.value.message


def test_swagger_2_unsupported():
    swagger = {"swagger": "2.0", "info": {"title": "old"}, "host": "example.com"}
    with pytest.raises(OpenApiError) as exc:
        parse_document(__import__("json").dumps(swagger))
    assert exc.value.code == "OPENAPI_UNSUPPORTED_VERSION"


def test_missing_servers_rejected():
    doc = {k: v for k, v in PETSTORE.items() if k != "servers"}
    with pytest.raises(OpenApiError) as exc:
        _parse(doc)
    assert exc.value.code == "OPENAPI_INVALID_DOCUMENT"
    assert "servers" in exc.value.message


def test_relative_server_url_rejected():
    doc = {**PETSTORE, "servers": [{"url": "/v1"}]}
    with pytest.raises(OpenApiError) as exc:
        _parse(doc)
    assert "绝对地址" in exc.value.message


def test_missing_title_defaults():
    doc = {**PETSTORE, "info": {"version": "1"}}
    spec = _parse(doc)
    assert spec.title == "未命名 API"


def test_ref_inlined():
    spec = _parse(PETSTORE)
    create = next(op for op in spec.operations if op.name == "create_pet")
    body = create.input_schema["properties"]["body"]
    assert body["type"] == "object"
    assert "$ref" not in str(body)


def test_ref_depth_limit():
    schemas = {}
    for i in range(10):
        schemas[f"S{i}"] = {
            "type": "object",
            "properties": {"next": {"$ref": f"#/components/schemas/S{i+1}"}}
            if i < 9
            else {"type": "object"},
        }
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "deep"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"post": {"requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S0"}}}}}}},
        "components": {"schemas": schemas},
    }
    spec = _parse(doc)
    assert spec.operations[0].skipped is True
    assert "深度" in spec.operations[0].skip_reason


def test_ref_cycle_guarded():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "cycle"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"post": {"requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/A"}}}}}}},
        "components": {"schemas": {
            "A": {"type": "object", "properties": {"b": {"$ref": "#/components/schemas/B"}}},
            "B": {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/A"}}},
        }},
    }
    spec = _parse(doc)
    assert spec.operations[0].skipped is True
    assert "环" in spec.operations[0].skip_reason


def test_external_ref_skipped():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "ext"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [
            {"name": "q", "in": "query", "schema": {"$ref": "https://other.example.com/schemas/X"}}]}}},
    }
    spec = _parse(doc)
    assert spec.operations[0].skipped is True
    assert "外部" in spec.operations[0].skip_reason


def test_all_of_merged():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "allof"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"post": {"requestBody": {
            "content": {"application/json": {"schema": {"allOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"type": "object", "properties": {"b": {"type": "integer"}}, "required": ["b"]},
            ]}}}}}}},
    }
    spec = _parse(doc)
    body = spec.operations[0].input_schema["properties"]["body"]
    assert set(body["properties"]) == {"a", "b"}
    assert set(body["required"]) == {"a", "b"}


def test_one_of_operation_skipped():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "oneof"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [
            {"name": "q", "in": "query",
             "schema": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}]}}},
    }
    spec = _parse(doc)
    assert spec.operations[0].skipped is True
    assert "oneOf" in spec.operations[0].skip_reason


def test_form_data_operation_skipped():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "form"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/upload": {"post": {"requestBody": {
            "content": {"multipart/form-data": {"schema": {"type": "object"}}}}}}},
    }
    spec = _parse(doc)
    assert spec.operations[0].skipped is True
    assert "application/json" in spec.operations[0].skip_reason


def test_operation_parameter_ref_resolved():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "pref"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [{"$ref": "#/components/parameters/Q"}]}}},
    "components": {"parameters": {"Q": {
        "name": "q", "in": "query", "schema": {"type": "string"}}}},
    }
    spec = _parse(doc)
    op = spec.operations[0]
    assert op.skipped is False
    assert "q" in op.input_schema["properties"]


def test_path_level_parameter_inherited():
    spec = _parse(PETSTORE)
    get_pet = next(op for op in spec.operations if op.name == "get_pet_by_id")
    assert "petId" in get_pet.input_schema["properties"]


def test_operation_level_parameter_overrides_path_level():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "override"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {
            "parameters": [{"name": "q", "in": "query",
                            "schema": {"type": "string", "maxLength": 5}}],
            "get": {"parameters": [{"name": "q", "in": "query",
                                    "schema": {"type": "integer"}}]},
        }},
    }
    spec = _parse(doc)
    prop = spec.operations[0].input_schema["properties"]["q"]
    assert prop == {"type": "integer"}


def test_name_dedupe_suffixes():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "dedupe"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {
            "/a": {"get": {"operationId": "sameName"}},
            "/b": {"get": {"operationId": "sameName"}},
        },
    }
    spec = _parse(doc)
    assert [op.name for op in spec.operations] == ["same_name", "same_name_2"]


def test_operation_id_sanitized():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "san"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"operationId": "Get /Users-Now!"}}},
    }
    spec = _parse(doc)
    name = spec.operations[0].name
    assert name == "get_users_now"
    assert len(name) <= 63


def test_exclusive_minimum_boolean_converted():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "excl"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"get": {"parameters": [
            {"name": "n", "in": "query",
             "schema": {"type": "integer", "minimum": 1, "exclusiveMinimum": True}}]}}},
    }
    spec = _parse(doc)
    schema = spec.operations[0].input_schema["properties"]["n"]
    assert schema["exclusiveMinimum"] == 1


def test_array_items_converted():
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "arr"},
        "servers": [{"url": "https://x.example.com"}],
        "paths": {"/a": {"post": {"requestBody": {
            "content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"tags": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "minItems": 1,
                }},
            }}}}}}},
    }
    spec = _parse(doc)
    tags = spec.operations[0].input_schema["properties"]["body"]["properties"]["tags"]
    assert tags["items"] == {"type": "string"}


def test_parser_is_pure():
    import json

    first = parse_document(json.dumps(PETSTORE))
    second = parse_document(json.dumps(PETSTORE))
    assert first.model_dump() == second.model_dump()


def test_description_falls_back_to_summary():
    spec = _parse(PETSTORE)
    delete_op = next(op for op in spec.operations if op.method == "delete")
    assert delete_op.description == "Delete a pet"


# --- securitySchemes / security（docs/44） ---------------------------------

def _security_doc(**extra):
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Secured", "version": "1.0.0"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/things": {
                "get": {"operationId": "getThings"},
                "post": {"operationId": "postThings"},
            },
        },
        "components": {
            "securitySchemes": {
                "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "ApiKeyQuery": {"type": "apiKey", "in": "query", "name": "api_key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
                "BasicAuth": {"type": "http", "scheme": "basic"},
                "CookieKey": {"type": "apiKey", "in": "cookie", "name": "session"},
                "OAuth": {
                    "type": "oauth2",
                    "flows": {"implicit": {"authorizationUrl": "https://x", "scopes": {}}},
                },
            },
        },
    }
    document.update(extra)
    return document


def test_supported_security_schemes_are_collected():
    spec = _parse(_security_doc())
    assert set(spec.security_schemes) == {"ApiKeyHeader", "ApiKeyQuery", "BearerAuth"}
    header = spec.security_schemes["ApiKeyHeader"]
    assert header.kind == "api_key" and header.location == "header" and header.param == "X-API-Key"
    query = spec.security_schemes["ApiKeyQuery"]
    assert query.location == "query" and query.param == "api_key"
    bearer = spec.security_schemes["BearerAuth"]
    assert bearer.kind == "bearer" and bearer.param == "Authorization" and bearer.prefix == "Bearer "


def test_unsupported_schemes_are_ignored():
    spec = _parse(_security_doc())
    assert "BasicAuth" not in spec.security_schemes
    assert "CookieKey" not in spec.security_schemes
    assert "OAuth" not in spec.security_schemes


def test_global_security_applies_to_operations():
    doc = _security_doc(security=[{"ApiKeyHeader": []}, {"BearerAuth": []}])
    spec = _parse(doc)
    for op in spec.operations:
        assert op.security == [["ApiKeyHeader"], ["BearerAuth"]]


def test_operation_security_overrides_global():
    doc = _security_doc(security=[{"ApiKeyHeader": []}])
    doc["paths"]["/things"]["get"]["security"] = [{"BearerAuth": []}]
    spec = _parse(doc)
    get_op = next(op for op in spec.operations if op.method == "get")
    post_op = next(op for op in spec.operations if op.method == "post")
    assert get_op.security == [["BearerAuth"]]
    assert post_op.security == [["ApiKeyHeader"]]


def test_empty_security_means_anonymous():
    doc = _security_doc(security=[{"ApiKeyHeader": []}])
    doc["paths"]["/things"]["get"]["security"] = []
    spec = _parse(doc)
    get_op = next(op for op in spec.operations if op.method == "get")
    assert get_op.security == []


def test_empty_group_is_preserved():
    doc = _security_doc(security=[{"ApiKeyHeader": []}, {}])
    spec = _parse(doc)
    assert spec.operations[0].security == [["ApiKeyHeader"], []]


def test_unsupported_requirement_groups_are_dropped():
    doc = _security_doc(security=[{"OAuth": ["read"]}, {"ApiKeyQuery": []}])
    spec = _parse(doc)
    assert spec.operations[0].security == [["ApiKeyQuery"]]


def test_security_scheme_ref_is_resolved():
    doc = _security_doc()
    schemes = doc["components"]["securitySchemes"]
    schemes["AliasKey"] = {"$ref": "#/components/securitySchemes/ApiKeyHeader"}
    doc["security"] = [{"AliasKey": []}]
    spec = _parse(doc)
    assert "AliasKey" in spec.security_schemes
    assert spec.operations[0].security == [["AliasKey"]]


def test_and_group_requires_all_schemes():
    doc = _security_doc(security=[{"ApiKeyHeader": [], "BearerAuth": []}])
    spec = _parse(doc)
    assert spec.operations[0].security == [["ApiKeyHeader", "BearerAuth"]]


def test_security_applies_to_skipped_operations_too():
    doc = _security_doc(security=[{"ApiKeyHeader": []}])
    doc["paths"]["/things"]["get"]["parameters"] = [
        {"name": "bad", "in": "path"}
    ]
    spec = _parse(doc)
    get_op = next(op for op in spec.operations if op.method == "get")
    assert get_op.skipped is True
    assert get_op.security == [["ApiKeyHeader"]]

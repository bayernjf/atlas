from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from .errors import OpenApiError, UnsupportedSchema
from .models import HTTP_METHODS, OperationDescriptor, ParsedSpec, SecurityScheme
from .schema import _resolve_ref, convert_schema

_INVALID_MESSAGE = "仅支持 OpenAPI 3.x JSON 文档，请先将 YAML 转为 JSON"
_SAFE_METHODS = {"get", "head"}
_NAME_RE = re.compile(r"[^a-z0-9]+")


def parse_document(raw_text: str) -> ParsedSpec:
    try:
        document = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OpenApiError("OPENAPI_INVALID_DOCUMENT", _INVALID_MESSAGE) from exc
    if not isinstance(document, dict):
        raise OpenApiError("OPENAPI_INVALID_DOCUMENT", _INVALID_MESSAGE)

    version_field = document.get("openapi")
    if not isinstance(version_field, str) or not version_field.startswith("3."):
        raise OpenApiError(
            "OPENAPI_UNSUPPORTED_VERSION", "仅支持 OpenAPI 3.0/3.1 文档（Swagger 2.0 暂不支持）"
        )

    base_url = _base_url(document)
    info = document.get("info")
    title = ""
    spec_version = ""
    if isinstance(info, dict):
        raw_title = info.get("title")
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()
        raw_version = info.get("version")
        if isinstance(raw_version, str):
            spec_version = raw_version
    if not title:
        title = "未命名 API"

    components = document.get("components")
    if not isinstance(components, dict):
        components = {}
    schemes = _security_schemes(components)
    global_security = document.get("security")

    operations: list[OperationDescriptor] = []
    paths = document.get("paths")
    if isinstance(paths, dict):
        for raw_path, item in paths.items():
            if not isinstance(raw_path, str) or not raw_path.startswith("/"):
                continue
            if not isinstance(item, dict):
                continue
            if "$ref" in item:
                try:
                    item = _resolve_ref(item["$ref"], components)
                except UnsupportedSchema:
                    continue
                if not isinstance(item, dict):
                    continue
            operations.extend(
                _operations_for_path(
                    raw_path, item, components, schemes, global_security
                )
            )

    _dedupe_names(operations)
    return ParsedSpec(
        title=title,
        version=spec_version,
        base_url=base_url,
        operations=operations,
        security_schemes=schemes,
    )


def _security_schemes(components: dict[str, Any]) -> dict[str, SecurityScheme]:
    raw_schemes = components.get("securitySchemes")
    if not isinstance(raw_schemes, dict):
        return {}
    result: dict[str, SecurityScheme] = {}
    for name, raw in raw_schemes.items():
        if not isinstance(name, str):
            continue
        if not isinstance(raw, dict):
            continue
        if "$ref" in raw:
            try:
                raw = _resolve_ref(raw["$ref"], components)
            except UnsupportedSchema:
                continue
            if not isinstance(raw, dict):
                continue
        scheme_type = raw.get("type")
        if scheme_type == "apiKey":
            location = raw.get("in")
            param = raw.get("name")
            if location not in ("header", "query") or not isinstance(param, str) or not param:
                continue
            result[name] = SecurityScheme(
                name=name, kind="api_key", location=location, param=param
            )
        elif scheme_type == "http":
            http_scheme = str(raw.get("scheme", "")).lower()
            if http_scheme == "bearer":
                result[name] = SecurityScheme(
                    name=name,
                    kind="bearer",
                    location=None,
                    param="Authorization",
                    prefix="Bearer ",
                )
            elif http_scheme == "basic":
                result[name] = SecurityScheme(
                    name=name,
                    kind="basic",
                    location=None,
                    param="Authorization",
                    prefix="Basic ",
                )
    return result


def _effective_security(
    operation: dict[str, Any],
    global_security: Any,
    schemes: dict[str, SecurityScheme],
) -> list[list[str]]:
    raw = operation.get("security") if isinstance(operation.get("security"), list) else global_security
    if not isinstance(raw, list):
        return []
    groups: list[list[str]] = []
    for requirement in raw:
        if not isinstance(requirement, dict):
            continue
        if not requirement:
            groups.append([])
            continue
        names = [name for name in requirement if name in schemes]
        if names:
            groups.append(names)
    return groups


def _base_url(document: dict[str, Any]) -> str:
    servers = document.get("servers")
    if not isinstance(servers, list) or not servers:
        raise OpenApiError("OPENAPI_INVALID_DOCUMENT", "文档缺少 servers，无法确定服务地址")
    first = servers[0]
    url = first.get("url") if isinstance(first, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise OpenApiError("OPENAPI_INVALID_DOCUMENT", "文档缺少 servers，无法确定服务地址")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OpenApiError(
            "OPENAPI_INVALID_DOCUMENT", "servers[0].url 必须是包含 Host 的绝对地址"
        )
    return url.strip().rstrip("/")


def _resolve_parameter(raw: Any, components: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UnsupportedSchema("参数必须是对象")
    if "$ref" in raw:
        target = _resolve_ref(raw["$ref"], components)
        if not isinstance(target, dict):
            raise UnsupportedSchema("参数 $ref 目标必须是对象")
        return target
    return raw


def _operations_for_path(
    path: str,
    item: dict[str, Any],
    components: dict[str, Any],
    schemes: dict[str, SecurityScheme],
    global_security: Any,
) -> list[OperationDescriptor]:
    path_parameters = item.get("parameters")
    if not isinstance(path_parameters, list):
        path_parameters = []

    result: list[OperationDescriptor] = []
    for method in HTTP_METHODS:
        operation = item.get(method)
        if not isinstance(operation, dict):
            continue
        result.append(
            _build_operation(
                method,
                path,
                operation,
                path_parameters,
                components,
                _effective_security(operation, global_security, schemes),
            )
        )
    return result


def _build_operation(
    method: str,
    path: str,
    operation: dict[str, Any],
    path_parameters: list[Any],
    components: dict[str, Any],
    security: list[list[str]],
) -> OperationDescriptor:
    op_parameters = operation.get("parameters")
    if not isinstance(op_parameters, list):
        op_parameters = []

    try:
        parameters = _merge_parameters(path_parameters, op_parameters, components)
        properties: dict[str, Any] = {}
        required: list[str] = []
        locations: dict[str, str] = {}
        for param in parameters:
            name = param.get("name")
            location = param.get("in")
            if not isinstance(name, str) or location not in ("path", "query", "header"):
                raise UnsupportedSchema("参数缺少合法 name/in")
            schema = param.get("schema")
            if not isinstance(schema, dict):
                raise UnsupportedSchema(f"参数 {name} 缺少 JSON schema")
            converted = convert_schema(schema, components)
            if param.get("description") and "description" not in converted:
                converted["description"] = param["description"]
            properties[name] = converted
            locations[name] = location
            if location == "path" or param.get("required") is True:
                required.append(name)

        body_schema = None
        body_required = False
        request_body = operation.get("requestBody")
        if isinstance(request_body, dict):
            if "$ref" in request_body:
                request_body = _resolve_ref(request_body["$ref"], components)
            content = request_body.get("content") if isinstance(request_body, dict) else None
            if not isinstance(content, dict) or "application/json" not in content:
                raise UnsupportedSchema("请求体仅支持 application/json")
            media = content["application/json"]
            raw_schema = media.get("schema") if isinstance(media, dict) else None
            if not isinstance(raw_schema, dict):
                raise UnsupportedSchema("请求体缺少 JSON schema")
            body_schema = convert_schema(raw_schema, components)
            body_required = request_body.get("required") is True

        if body_schema is not None:
            properties["body"] = body_schema
            if body_required:
                required.append("body")

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            input_schema["required"] = required
    except UnsupportedSchema as exc:
        return OperationDescriptor(
            name=_candidate_name(operation, method, path),
            method=method,
            path=path,
            summary=_summary(operation),
            description=_description(operation),
            input_schema={"type": "object", "properties": {}},
            security=security,
            skipped=True,
            skip_reason=exc.reason,
        )

    return OperationDescriptor(
        name=_candidate_name(operation, method, path),
        method=method,
        path=path,
        summary=_summary(operation),
        description=_description(operation),
        permission="read" if method in _SAFE_METHODS else "write",
        idempotent=method in _SAFE_METHODS,
        input_schema=input_schema,
        locations=locations,
        security=security,
    )


def _merge_parameters(
    path_level: list[Any], op_level: list[Any], components: dict[str, Any]
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in path_level:
        param = _resolve_parameter(raw, components)
        key = (param.get("name", ""), param.get("in", ""))
        merged[key] = param
    for raw in op_level:
        param = _resolve_parameter(raw, components)
        key = (param.get("name", ""), param.get("in", ""))
        merged[key] = param
    return list(merged.values())


def _candidate_name(operation: dict[str, Any], method: str, path: str) -> str:
    raw = operation.get("operationId")
    if isinstance(raw, str) and raw.strip():
        candidate = _sanitize(raw.strip())
        if candidate:
            return candidate
    segments = [method]
    segments.extend(seg for seg in re.findall(r"[A-Za-z0-9]+", path) if seg)
    return _sanitize("_".join(segments)) or f"{method}_root"


def _sanitize(raw: str) -> str:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    lowered = split_camel.lower()
    cleaned = _NAME_RE.sub("_", lowered).strip("_")
    if not cleaned:
        return ""
    if cleaned[0].isdigit():
        cleaned = f"op_{cleaned}"
    return cleaned[:63]


def _summary(operation: dict[str, Any]) -> str:
    raw = operation.get("summary")
    return raw.strip() if isinstance(raw, str) else ""


def _description(operation: dict[str, Any]) -> str:
    raw = operation.get("description")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _summary(operation)


def _dedupe_names(operations: list[OperationDescriptor]) -> None:
    used: set[str] = set()
    for operation in operations:
        base = operation.name
        name = base
        counter = 2
        while name in used:
            suffix = f"_{counter}"
            name = f"{base[:63 - len(suffix)]}{suffix}"
            counter += 1
        operation.name = name
        used.add(name)

from __future__ import annotations

from typing import Any

from .errors import UnsupportedSchema

MAX_REF_DEPTH = 8

_PASSTHROUGH_KEYS = {
    "enum",
    "const",
    "description",
    "default",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
}

_DROP_KEYS = {
    "format",
    "title",
    "example",
    "examples",
    "deprecated",
    "nullable",
    "readOnly",
    "writeOnly",
    "discriminator",
    "xml",
    "externalDocs",
    "$comment",
    "$schema",
    "type",
}

_HARD_UNSUPPORTED = {
    "anyOf",
    "not",
    "multipleOf",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "contains",
    "patternProperties",
    "unevaluatedProperties",
    "if",
    "then",
    "else",
}

_COMPONENTS_PREFIX = "#/components/"


def _resolve_ref(ref: str, components: dict[str, Any]) -> Any:
    if not isinstance(ref, str) or not ref.startswith(_COMPONENTS_PREFIX):
        raise UnsupportedSchema("不支持外部 $ref（仅解析本文档 #/components 内引用）")
    target: Any = components
    for segment in ref[len(_COMPONENTS_PREFIX):].split("/"):
        if not isinstance(target, dict) or segment not in target:
            raise UnsupportedSchema(f"$ref 目标不存在：{ref}")
        target = target[segment]
    return target


def _convert_type(node: dict[str, Any]) -> str | None:
    raw = node.get("type")
    if raw is None:
        return None
    if isinstance(raw, list):
        chosen = next((t for t in raw if t != "null"), None)
        if chosen is None:
            raise UnsupportedSchema("不支持仅为 null 的类型")
        raw = chosen
    if raw not in ("string", "integer", "number", "boolean", "object", "array"):
        raise UnsupportedSchema(f"不支持的类型：{raw}")
    return raw


def convert_schema(
    node: Any,
    components: dict[str, Any],
    *,
    seen: frozenset[str] = frozenset(),
    depth: int = 0,
) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise UnsupportedSchema("schema 必须是对象")

    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith(_COMPONENTS_PREFIX):
            raise UnsupportedSchema("不支持外部 $ref（仅解析本文档 #/components 内引用）")
        if ref in seen:
            raise UnsupportedSchema("$ref 引用环")
        if depth >= MAX_REF_DEPTH:
            raise UnsupportedSchema("$ref 深度超限")
        return convert_schema(
            _resolve_ref(ref, components),
            components,
            seen=seen | {ref},
            depth=depth + 1,
        )

    if "oneOf" in node:
        raise UnsupportedSchema("不支持的关键字 oneOf")

    unknown_hard = _HARD_UNSUPPORTED & set(node)
    if unknown_hard:
        raise UnsupportedSchema(f"不支持的关键字：{sorted(unknown_hard)[0]}")

    if "allOf" in node:
        return _convert_all_of(node, components, seen, depth)

    result: dict[str, Any] = {}
    node_type = _convert_type(node)
    if node_type is not None:
        result["type"] = node_type

    for key in _PASSTHROUGH_KEYS:
        if key in node:
            result[key] = node[key]

    for key in ("exclusiveMinimum", "exclusiveMaximum"):
        if key not in node:
            continue
        value = node[key]
        if value is True:
            bound = node.get("minimum" if key == "exclusiveMinimum" else "maximum")
            if bound is None:
                raise UnsupportedSchema(f"{key} 为布尔值但缺少边界值")
            value = bound
        if value is not False:
            result[key] = value

    if "properties" in node:
        properties = node["properties"]
        if not isinstance(properties, dict):
            raise UnsupportedSchema("properties 必须是对象")
        result["properties"] = {
            name: convert_schema(sub, components, seen=seen, depth=depth)
            for name, sub in properties.items()
        }

    if "required" in node:
        required = node["required"]
        if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
            raise UnsupportedSchema("required 必须是字符串数组")
        result["required"] = required

    if "items" in node:
        items = node["items"]
        if isinstance(items, list):
            items = items[0] if items else {}
        result["items"] = convert_schema(items, components, seen=seen, depth=depth)

    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        result["additionalProperties"] = convert_schema(
            additional, components, seen=seen, depth=depth
        )
    elif additional is bool:
        result["additionalProperties"] = additional

    return result


def _convert_all_of(
    node: dict[str, Any],
    components: dict[str, Any],
    seen: frozenset[str],
    depth: int,
) -> dict[str, Any]:
    branches = node["allOf"]
    if not isinstance(branches, list) or not branches:
        raise UnsupportedSchema("allOf 必须是非空数组")
    converted = [
        convert_schema(sub, components, seen=seen, depth=depth) for sub in branches
    ]
    result: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for branch in converted:
        if "type" in branch and branch["type"] != "object":
            raise UnsupportedSchema("allOf 含非对象分支")
        for key, value in branch.items():
            if key == "properties":
                properties.update(value)
            elif key == "required":
                required.extend(k for k in value if k not in required)
            elif key == "type":
                continue
            else:
                raise UnsupportedSchema(f"allOf 分支含未合并约束：{key}")
    result["type"] = "object"
    if properties:
        result["properties"] = properties
    if required:
        result["required"] = required
    return result

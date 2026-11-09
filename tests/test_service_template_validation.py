# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.template_validation.

纯函数直调，不启 TestClient：声明形状校验（A1 E-5 + 打包 ZX 递归）、
instantiate 值校验（E-6）与模板搜索匹配的等价行为。
"""

from __future__ import annotations

from atlas.services.template_validation import (
    template_matches_query,
    validate_instantiate_values,
    validate_template_params,
)


def test_valid_structured_decl_passes() -> None:
    errors = validate_template_params(
        {
            "city": {"type": "string", "label": "城市", "required": True},
            "level": {"type": "select", "options": ["P0", "P1"]},
            "conf": {
                "type": "object",
                "properties": {
                    "retries": {"type": "number"},
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
            },
            "mode": {"type": "string", "visibleWhen": {"field": "level", "equals": "P0"}},
        }
    )
    assert errors == []


def test_decl_shape_errors_are_aggregated_in_chinese() -> None:
    errors = validate_template_params(
        {
            "bad_type": {"type": "function"},
            "unknown_key": {"type": "string", "rogue": 1},
            "long_label": {"type": "string", "label": "x" * 41},
            "empty_options": {"type": "select", "options": []},
            "bad_required": {"type": "string", "required": "yes"},
        }
    )
    assert any("type 必须是" in e for e in errors)
    assert any("含未知字段" in e for e in errors)
    assert any("label 长度须在 40 字符以内" in e for e in errors)
    assert any("options 必须是非空字符串列表" in e for e in errors)
    assert any("required 必须是布尔值" in e for e in errors)


def test_decl_depth_limit_blocks_pathological_nesting() -> None:
    leaf = {"type": "string"}
    # 5 层嵌套：top(0) → a(1) → b(2) → c(3) → d(4)，第 5 层（d 的子级）越限。
    params = {"top": {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "object", "properties": {"c": {"type": "object", "properties": {"d": {"type": "object", "properties": {"e": leaf}}}}}}}}}}}
    errors = validate_template_params(params)
    assert any("嵌套深度超过上限（4）" in e for e in errors), errors


def test_instantiate_values_required_type_and_unknown() -> None:
    params = {
        "city": {"type": "string", "required": True},
        "count": {"type": "number"},
        "flag": {"type": "boolean"},
        "level": {"type": "select", "options": ["P0", "P1"]},
    }
    assert validate_instantiate_values(params, {}) == ["参数 city 为必填"]
    assert validate_instantiate_values(params, {"city": "北京", "count": "3"}) == [
        "参数 count 必须是数字"
    ]
    assert validate_instantiate_values(params, {"city": "北京", "flag": 1}) == [
        "参数 flag 必须是布尔值"
    ]
    assert validate_instantiate_values(params, {"city": "北京", "level": "P9"}) == [
        "参数 level 的值不在可选范围内"
    ]
    assert validate_instantiate_values(params, {"city": "北京", "ghost": 1}) == [
        "未知参数：ghost"
    ]


def test_instantiate_values_object_children_and_array_bounds() -> None:
    params = {
        "conf": {
            "type": "object",
            "properties": {
                "must": {"type": "string", "required": True},
                "extra_forbidden": {"type": "number"},
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
    }
    errors = validate_instantiate_values(
        params,
        {
            "conf": {"must": "v", "rogue": 1},
            "tags": ["a", "b", "c"],
        },
    )
    assert any("conf 含未知子字段：rogue" in e for e in errors)
    assert any("tags 最多允许 2 项" in e for e in errors)

    missing = validate_instantiate_values(params, {"conf": {}, "tags": []})
    assert "参数 conf.must 为必填" in missing
    assert "参数 tags 至少需要 1 项" in missing


def test_template_matches_query_casefolds_across_fields() -> None:
    template = {
        "name": "退款处理",
        "description": "Customer Refund",
        "tags": ["After-Sales"],
        "category": "电商",
    }
    assert template_matches_query(template, "退款")
    assert template_matches_query(template, "refund")
    assert template_matches_query(template, "after-sales")
    assert template_matches_query(template, "电商")
    assert not template_matches_query(template, "invoice")

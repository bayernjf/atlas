"""Template parameter validation, extracted verbatim from api/main.py (Tier0 review P2).

Pure functions only: each validator derives its output from the arguments and
reports failures as a list of Chinese error strings (empty list = pass). The
HTTP 422 shaping stays in the route handlers; these functions never raise.
"""

from __future__ import annotations

from typing import Any

_TEMPLATE_PARAM_TYPES = {"string", "number", "boolean", "select", "object", "array"}
_TEMPLATE_PARAM_KEYS = {
    "type",
    "label",
    "required",
    "default",
    "hint",
    "options",
    # 打包 ZX（docs/106）：结构化声明面——嵌套 object / 数组 / 条件显隐
    "properties",
    "items",
    "minItems",
    "maxItems",
    "visibleWhen",
}
_TEMPLATE_PARAM_MAX_DEPTH = 4


def template_matches_query(template: dict[str, Any], query: str) -> bool:
    """A1 搜索：name/description/tags/category 任一 casefold 子串命中（docs/97 E-3）。"""
    haystacks = [
        str(template.get("name") or ""),
        str(template.get("description") or ""),
        " ".join(template.get("tags") or []),
        str(template.get("category") or ""),
    ]
    return any(query in (part.casefold()) for part in haystacks)


def validate_param_decl(name: str, pdecl: Any, errors: list[str], depth: int = 0) -> None:
    """打包 ZX（docs/106 §2.2）：TemplateParam 声明形状校验（含递归）。

    与 A1 E-5 同构的标量四型校验逐字保留；object 递归 properties、array 递归 items、
    visibleWhen 校验判别字段形状；深度上限 _TEMPLATE_PARAM_MAX_DEPTH 防病态嵌套。
    """
    if not isinstance(pdecl, dict):
        errors.append(f"参数 {name} 的声明必须是对象")
        return
    unknown = set(pdecl) - _TEMPLATE_PARAM_KEYS
    if unknown:
        errors.append(f"参数 {name} 含未知字段：{', '.join(sorted(unknown))}")
    ptype = pdecl.get("type", "string")
    if ptype not in _TEMPLATE_PARAM_TYPES:
        errors.append(
            f"参数 {name} 的 type 必须是 string/number/boolean/select/object/array 之一"
        )
    label = pdecl.get("label")
    if label is not None and not isinstance(label, str):
        errors.append(f"参数 {name} 的 label 必须是字符串")
    elif isinstance(label, str) and len(label) > 40:
        errors.append(f"参数 {name} 的 label 长度须在 40 字符以内")
    if "required" in pdecl and not isinstance(pdecl["required"], bool):
        errors.append(f"参数 {name} 的 required 必须是布尔值")
    if ptype == "select":
        options = pdecl.get("options")
        if (
            not isinstance(options, list)
            or not options
            or len(options) > 20
            or not all(isinstance(o, str) for o in options)
        ):
            errors.append(f"参数 {name} 的 options 必须是非空字符串列表（≤20 项）")
    if ptype == "object":
        properties = pdecl.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"参数 {name} 的 properties 必须是对象")
        elif depth + 1 > _TEMPLATE_PARAM_MAX_DEPTH:
            errors.append(f"参数 {name} 的嵌套深度超过上限（{_TEMPLATE_PARAM_MAX_DEPTH}）")
        else:
            for subname, subdecl in properties.items():
                validate_param_decl(f"{name}.{subname}", subdecl, errors, depth + 1)
    if ptype == "array":
        items = pdecl.get("items")
        if not isinstance(items, dict):
            errors.append(f"参数 {name} 的 items 必须是对象")
        elif depth + 1 > _TEMPLATE_PARAM_MAX_DEPTH:
            errors.append(f"参数 {name} 的嵌套深度超过上限（{_TEMPLATE_PARAM_MAX_DEPTH}）")
        else:
            validate_param_decl(f"{name}[]", items, errors, depth + 1)
        for key in ("minItems", "maxItems"):
            value = pdecl.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                errors.append(f"参数 {name} 的 {key} 必须是非负整数")
        min_items, max_items = pdecl.get("minItems"), pdecl.get("maxItems")
        if (
            isinstance(min_items, int)
            and isinstance(max_items, int)
            and min_items > max_items
        ):
            errors.append(f"参数 {name} 的 minItems 不能大于 maxItems")
    visible_when = pdecl.get("visibleWhen")
    if visible_when is not None:
        if not isinstance(visible_when, dict) or not isinstance(
            visible_when.get("field"), str
        ):
            errors.append(f"参数 {name} 的 visibleWhen 必须是 {{field, equals}} 对象")
        elif not visible_when["field"]:
            errors.append(f"参数 {name} 的 visibleWhen.field 不能为空")


def validate_template_params(params: dict[str, Any]) -> list[str]:
    """A1 参数化向导声明形状校验（docs/97 E-5）+ 打包 ZX（docs/106 §2.2）结构化递归：
    type 枚举、label≤40、required 布尔、select 须 options 非空字符串列表 ≤20、未知键拒绝、
    object/array 递归、visibleWhen 形状；返回中文错误列表（空＝通过）。"""
    errors: list[str] = []
    if not isinstance(params, dict):
        return ["参数声明必须是对象"]
    for pname, pdecl in params.items():
        validate_param_decl(pname, pdecl, errors)
    return errors


def validate_param_value(pname: str, pdecl: dict[str, Any], value: Any, errors: list[str]) -> None:
    """打包 ZX（docs/106 §2.2）：instantiate 值校验递归核心。

    标量四型与 A1 E-6 逐字保留；object 须 dict 且按 properties 递归子字段、
    array 须 list 且按 items 递归元素、minItems/maxItems 门控、visibleWhen 不参与值校验。
    """
    ptype = pdecl.get("type", "string")
    if ptype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"参数 {pname} 必须是数字")
    elif ptype == "boolean":
        if not isinstance(value, bool):
            errors.append(f"参数 {pname} 必须是布尔值")
    elif ptype == "select":
        options = pdecl.get("options") or []
        if value not in options:
            errors.append(f"参数 {pname} 的值不在可选范围内")
    elif ptype == "object":
        if not isinstance(value, dict):
            errors.append(f"参数 {pname} 必须是对象")
            return
        sub_params = pdecl.get("properties") or {}
        for sub_name, sub_decl in sub_params.items():
            if sub_name not in value:
                if sub_decl.get("required"):
                    errors.append(f"参数 {pname}.{sub_name} 为必填")
                continue
            validate_param_value(f"{pname}.{sub_name}", sub_decl, value[sub_name], errors)
        for sub_name in value:
            if sub_name not in sub_params:
                errors.append(f"参数 {pname} 含未知子字段：{sub_name}")
    elif ptype == "array":
        if not isinstance(value, list):
            errors.append(f"参数 {pname} 必须是数组")
            return
        items = pdecl.get("items") or {}
        min_items = pdecl.get("minItems")
        max_items = pdecl.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"参数 {pname} 至少需要 {min_items} 项")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"参数 {pname} 最多允许 {max_items} 项")
        for index, item in enumerate(value):
            validate_param_value(f"{pname}[{index}]", items, item, errors)


def validate_instantiate_values(
    params: dict[str, Any], values: dict[str, Any]
) -> list[str]:
    """A1 instantiate 值校验（docs/97 E-6）+ 打包 ZX（docs/106 §2.2）递归：
    required 缺失/类型不符/select 范围/object 子字段/array 元素/未知参数名。"""
    errors: list[str] = []
    for pname, pdecl in params.items():
        present = pname in values
        if pdecl.get("required") and not present:
            errors.append(f"参数 {pname} 为必填")
            continue
        if not present:
            continue
        validate_param_value(pname, pdecl, values[pname], errors)
    for pname in values:
        if pname not in params:
            errors.append(f"未知参数：{pname}")
    return errors

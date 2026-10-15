# -*- coding: utf-8 -*-
"""NL 造工具的适配器定义类型与校验（docs/133 打包 BQ＝D71；docs/127 里程碑 F）。

``AdapterDefinition`` 是 LLM 结构化输出产出的**草稿**形状（JSONB 持久化在
``adapter_drafts`` 表/内存 store）：与 ``adapter_schema``（docs/03）同构——
auth_type/capabilities/input·output schema/permission/is_idempotent，另带
HTTP 执行所需的 ``method``/``path``（NL 从业务描述里生成，如"查订单"→ GET /orders）。

**F-P2 最小权限**：草稿 ``permission`` 缺省 ``read``；``auth_type`` 非 ``none``
（要凭据才能真实执行）须 admin 显式确认才可采纳（端点层把关，本模块只校验形状）。

schema 形状校验复用 ``harness/base._validate_schema_subset``（04 §4.9 白名单，同一份
校验不漂移）；未知 keyword/非法 type/根不是 object 均拒绝——照 ``Capability`` 构造期纪律。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import _validate_schema_subset

ALLOWED_AUTH_TYPES = ("none", "header", "query")
ALLOWED_PERMISSIONS = ("read", "write", "delete", "financial")
ALLOWED_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class AdapterDefinitionError(ValueError):
    """草稿形状/字段非法；API 层折算 422（照 import 的形状异常纪律）。"""


@dataclass
class AdapterCapability:
    """草稿里的一条可执行能力：Capability 同构字段 ＋ HTTP 执行映射（method/path）。"""

    name: str
    description: str
    method: str = "GET"
    path: str = "/"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permission: str = "read"
    is_idempotent: bool = False

    def __post_init__(self) -> None:
        if self.input_schema:
            _validate_schema_subset(self.input_schema, field="input_schema", root="object")
        if self.output_schema:
            _validate_schema_subset(self.output_schema, field="output_schema", root="object")


@dataclass
class AdapterDefinition:
    """NL 生成的适配器草稿（docs/133 §2.2；持久化形态＝dict/JSONB）。"""

    name: str
    description: str
    base_url: str
    auth_type: str = "none"
    permission: str = "read"
    capabilities: list[AdapterCapability] = field(default_factory=list)


def _require(raw: dict[str, Any], key: str, *, kind: type | None = None) -> Any:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise AdapterDefinitionError(f"适配器定义缺少字段：{key}")
    if kind is not None and not isinstance(value, kind):
        raise AdapterDefinitionError(f"字段 {key} 类型非法（期望 {kind.__name__}）")
    return value


def validate_adapter_definition(raw: dict[str, Any]) -> AdapterDefinition:
    """把 NL 结构化输出校验成 AdapterDefinition；任一违例抛 AdapterDefinitionError（422）。"""
    if not isinstance(raw, dict):
        raise AdapterDefinitionError("适配器定义必须是对象")

    name = _require(raw, "name")
    description = _require(raw, "description")
    base_url = _require(raw, "base_url")
    if not base_url.startswith(("http://", "https://")):
        raise AdapterDefinitionError("base_url 必须以 http(s):// 开头")

    auth_type = str(raw.get("auth_type") or "none").lower()
    if auth_type not in ALLOWED_AUTH_TYPES:
        raise AdapterDefinitionError(
            f"auth_type 非法：{auth_type!r}（允许 {sorted(ALLOWED_AUTH_TYPES)}）"
        )
    permission = str(raw.get("permission") or "read").lower()
    if permission not in ALLOWED_PERMISSIONS:
        raise AdapterDefinitionError(
            f"permission 非法：{permission!r}（允许 {sorted(ALLOWED_PERMISSIONS)}）"
        )

    capabilities_raw = raw.get("capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise AdapterDefinitionError("适配器定义至少需要一条 capability（capabilities 不能为空）")

    capabilities: list[AdapterCapability] = []
    for index, cap in enumerate(capabilities_raw):
        if not isinstance(cap, dict):
            raise AdapterDefinitionError(f"capabilities[{index}] 必须是对象")
        cap_name = _require(cap, "name")
        cap_desc = _require(cap, "description")
        method = str(cap.get("method") or "GET").upper()
        if method not in ALLOWED_HTTP_METHODS:
            raise AdapterDefinitionError(
                f"capabilities[{index}].method 非法：{method!r}"
            )
        path = _require(cap, "path")
        if not path.startswith("/"):
            raise AdapterDefinitionError(
                f"capabilities[{index}].path 必须以 / 开头：{path!r}"
            )
        cap_permission = str(cap.get("permission") or "read").lower()
        if cap_permission not in ALLOWED_PERMISSIONS:
            raise AdapterDefinitionError(
                f"capabilities[{index}].permission 非法：{cap_permission!r}"
            )
        input_schema = cap.get("input_schema") or {}
        output_schema = cap.get("output_schema") or {}
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise AdapterDefinitionError(
                f"capabilities[{index}] 的 input/output schema 必须是对象"
            )
        try:
            capabilities.append(
                AdapterCapability(
                    name=cap_name,
                    description=cap_desc,
                    method=method,
                    path=path,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    permission=cap_permission,
                    is_idempotent=bool(cap.get("is_idempotent", False)),
                )
            )
        except ValueError as exc:
            raise AdapterDefinitionError(
                f"capabilities[{index}] schema 形状非法：{exc}"
            ) from exc

    return AdapterDefinition(
        name=name,
        description=description,
        base_url=base_url,
        auth_type=auth_type,
        permission=permission,
        capabilities=capabilities,
    )


def definition_to_dict(definition: AdapterDefinition) -> dict[str, Any]:
    """JSONB 持久化形态（dataclass → dict）。"""
    return {
        "name": definition.name,
        "description": definition.description,
        "base_url": definition.base_url,
        "auth_type": definition.auth_type,
        "permission": definition.permission,
        "capabilities": [
            {
                "name": cap.name,
                "description": cap.description,
                "method": cap.method,
                "path": cap.path,
                "input_schema": cap.input_schema,
                "output_schema": cap.output_schema,
                "permission": cap.permission,
                "is_idempotent": cap.is_idempotent,
            }
            for cap in definition.capabilities
        ],
    }

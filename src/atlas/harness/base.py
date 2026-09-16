"""Harness 契约层：适配器接口与通用数据结构（06 §6.4 / 12 §2）。

Demo 阶段用 Python 实现 06 文档 Go 接口的同构版本（10 文档 T5）；
Go 化留待 Phase 2。适配器负责连接具体平台并向网关注册能力（工具）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class Permission(str, Enum):
    """工具权限枚举（03 adapter_schema / 12 §一致性清单）。"""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    FINANCIAL = "financial"


@dataclass
class StructuredError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Capability:
    """适配器声明的一个可执行操作，即 04 §4.3 工具定义。

    input_schema/output_schema 为 04 §4.9 定义的 JSON Schema 子集 dict，
    空 dict 表示未声明；构造期按白名单校验形状，未知 keyword 直接拒绝。
    """

    name: str
    description: str
    action: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permission: Permission = Permission.READ
    timeout: float = 30.0
    is_idempotent: bool = False

    def __post_init__(self) -> None:
        if self.input_schema:
            _validate_schema_subset(self.input_schema, field="input_schema", root="object")
        if self.output_schema:
            _validate_schema_subset(self.output_schema, field="output_schema", root="object")


_ALLOWED_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "items",
    "additionalProperties",
    "enum",
    "const",
    "oneOf",
    "description",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
}

_ALLOWED_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}


def _validate_schema_subset(schema: dict[str, Any], *, field: str, root: str | None) -> None:
    """校验 04 §4.9 的 JSON Schema 子集；违例 ValueError（含字段与 JSON Pointer 路径）。"""

    def walk(node: Any, pointer: str, *, object_root: bool = False) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"Capability {field}{pointer} 必须是 JSON Schema 对象")
        unknown = set(node) - _ALLOWED_SCHEMA_KEYWORDS
        if unknown:
            raise ValueError(
                f"Capability {field}{pointer} 含子集外 keyword：{sorted(unknown)}"
            )
        node_type = node.get("type")
        if node_type is not None and node_type not in _ALLOWED_SCHEMA_TYPES:
            raise ValueError(f"Capability {field}{pointer}.type 非法：{node_type!r}")
        if object_root and node_type not in (None, "object"):
            raise ValueError(f"Capability {field} 根 schema 必须描述 object")
        if object_root and node_type is None and not ({"properties", "required"} & set(node)):
            raise ValueError(
                f"Capability {field} 根 schema 省略 type 时须以 properties/required 描述 object"
            )
        if "properties" in node:
            properties = node["properties"]
            if not isinstance(properties, dict):
                raise ValueError(f"Capability {field}{pointer}.properties 必须是对象")
            for name, sub in properties.items():
                walk(sub, f"{pointer}/properties/{name}")
        if "required" in node and not (
            isinstance(node["required"], list) and all(isinstance(k, str) for k in node["required"])
        ):
            raise ValueError(f"Capability {field}{pointer}.required 必须是字符串数组")
        if "items" in node and not isinstance(node["items"], dict):
            raise ValueError(f"Capability {field}{pointer}.items 必须是单个 schema 对象")
        if "items" in node:
            walk(node["items"], f"{pointer}/items")
        additional = node.get("additionalProperties")
        if additional is not None and not isinstance(additional, (bool, dict)):
            raise ValueError(f"Capability {field}{pointer}.additionalProperties 必须是 bool 或 schema")
        if isinstance(additional, dict):
            walk(additional, f"{pointer}/additionalProperties")
        if "oneOf" in node:
            variants = node["oneOf"]
            if not isinstance(variants, list) or not variants:
                raise ValueError(f"Capability {field}{pointer}.oneOf 必须是非空 schema 数组")
            for index, sub in enumerate(variants):
                walk(sub, f"{pointer}/oneOf/{index}")
        for keyword in ("enum",):
            if keyword in node and not isinstance(node[keyword], list):
                raise ValueError(f"Capability {field}{pointer}.{keyword} 必须是数组")
        for keyword in ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"):
            if keyword not in node:
                continue
            value = node[keyword]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Capability {field}{pointer}.{keyword} 必须是数值")
        for keyword in ("exclusiveMinimum", "exclusiveMaximum"):
            value = node.get(keyword)
            if value is not None and not isinstance(value, (int, float, bool)):
                raise ValueError(f"Capability {field}{pointer}.{keyword} 必须是数值或布尔")
        if "pattern" in node and not isinstance(node["pattern"], str):
            raise ValueError(f"Capability {field}{pointer}.pattern 必须是字符串")

    walk(schema, "", object_root=root == "object")


@dataclass
class ActionRequest:
    capability_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    timeout: float | None = None


@dataclass
class Observation:
    url: str = ""
    title: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None


@dataclass
class ActionResult:
    status: ActionStatus
    output: Any = None
    screenshots: list[bytes] = field(default_factory=list)
    error: StructuredError | None = None

    @classmethod
    def success(cls, output: Any = None, *, screenshots: list[bytes] | None = None) -> "ActionResult":
        return cls(ActionStatus.SUCCESS, output=output, screenshots=screenshots or [])

    @classmethod
    def partial(cls, output: Any = None, *, error: StructuredError | None = None) -> "ActionResult":
        return cls(ActionStatus.PARTIAL, output=output, error=error)

    @classmethod
    def failed(cls, error: StructuredError) -> "ActionResult":
        return cls(ActionStatus.FAILED, error=error)


# (adapter_id, capability_name, request) -> None；默认审计接收器
AuditSink = Callable[[str, str, ActionRequest], None]


class HarnessAdapter(ABC):
    """HarnessAdapter 同构接口（06 §6.4）。

    execute 为模板方法：先做权限校验（拒绝时写审计，06 安全清单 / I8），
    再进入子类的 _execute。
    """

    adapter_id: str
    adapter_type: str  # 03 adapter_schema.type: web/api/mobile/...

    def __init__(
        self,
        *,
        granted_permissions: set[Permission] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.granted_permissions = granted_permissions or {Permission.READ}
        self.audit_sink = audit_sink

    @abstractmethod
    def list_capabilities(self) -> list[Capability]:
        raise NotImplementedError

    @abstractmethod
    def _execute(self, request: ActionRequest) -> ActionResult:
        raise NotImplementedError

    def execute(self, request: ActionRequest) -> ActionResult:
        capability = self._find_capability(request.capability_name)
        if capability is None:
            return ActionResult.failed(
                StructuredError(
                    "UNKNOWN_CAPABILITY",
                    f"capability {request.capability_name!r} is not provided by {self.adapter_id!r}",
                )
            )
        if capability.permission not in self.granted_permissions:
            if self.audit_sink is not None:
                self.audit_sink(self.adapter_id, capability.name, request)
            return ActionResult.failed(
                StructuredError(
                    "PERMISSION_DENIED",
                    f"permission {capability.permission.value!r} required for {capability.name!r}",
                    {"required": capability.permission.value},
                )
            )
        return self._execute(request)

    @abstractmethod
    def observe(self) -> Observation:
        raise NotImplementedError

    def _find_capability(self, name: str) -> Capability | None:
        return next((c for c in self.list_capabilities() if c.name == name), None)

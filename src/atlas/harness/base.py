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
    """适配器声明的一个可执行操作，即 04 §4.3 工具定义。"""

    name: str
    description: str
    action: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permission: Permission = Permission.READ
    timeout: float = 30.0
    is_idempotent: bool = False


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

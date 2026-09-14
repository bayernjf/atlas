"""Harness 契约层单元测试：注册表/发现/心跳（04 §4.4）与权限审计（I8）。"""

from __future__ import annotations

import pytest

from atlas.harness.base import (
    ActionRequest,
    ActionStatus,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
)
from atlas.harness.registry import AdapterRegistry


class _FakeAdapter(HarnessAdapter):
    adapter_type = "api"

    def __init__(self, adapter_id: str = "fake", capabilities=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.adapter_id = adapter_id
        self._capabilities = capabilities or [
            Capability("read_thing", "read", action="read_thing")
        ]

    def list_capabilities(self) -> list[Capability]:
        return self._capabilities

    def _execute(self, request: ActionRequest):
        from atlas.harness.base import ActionResult

        return ActionResult.success({"echo": request.parameters})

    def observe(self) -> Observation:
        return Observation(url="fake://")


def test_register_and_discover():
    registry = AdapterRegistry()
    adapter = _FakeAdapter(
        capabilities=[
            Capability("read_thing", "读取", action="read_thing", permission=Permission.READ),
            Capability(
                "write_thing",
                "写入",
                action="write_thing",
                permission=Permission.WRITE,
                is_idempotent=True,
            ),
        ]
    )
    registry.register(adapter)

    discovered = registry.list_adapters()
    assert discovered == [
        {
            "id": "fake",
            "type": "api",
            "healthy": True,
            "tools": [
                {
                    "name": "read_thing",
                    "description": "读取",
                    "permission": "read",
                    "idempotent": False,
                },
                {
                    "name": "write_thing",
                    "description": "写入",
                    "permission": "write",
                    "idempotent": True,
                },
            ],
        }
    ]


def test_duplicate_registration_rejected():
    registry = AdapterRegistry()
    registry.register(_FakeAdapter())
    with pytest.raises(ValueError):
        registry.register(_FakeAdapter())


def test_heartbeat_ttl_marks_unhealthy():
    clock = {"now": 100.0}
    registry = AdapterRegistry(ttl_seconds=30, clock=lambda: clock["now"])
    registry.register(_FakeAdapter())
    assert registry.is_healthy("fake")

    clock["now"] += 31
    assert registry.is_healthy("fake") is False

    registry.heartbeat("fake")
    assert registry.is_healthy("fake")


def test_list_adapters_refreshes_in_process_heartbeat():
    clock = {"now": 100.0}
    registry = AdapterRegistry(ttl_seconds=30, clock=lambda: clock["now"])
    registry.register(_FakeAdapter())
    clock["now"] += 31
    assert registry.is_healthy("fake") is False

    assert registry.list_adapters()[0]["healthy"] is True
    assert registry.is_healthy("fake") is True


def test_unregister_and_unknown_health():
    registry = AdapterRegistry()
    registry.register(_FakeAdapter())
    registry.unregister("fake")
    assert registry.is_healthy("fake") is False


def test_permission_denied_writes_audit():
    audit: list[tuple[str, str, ActionRequest]] = []
    adapter = _FakeAdapter(
        capabilities=[Capability("refund", "退款", action="refund", permission=Permission.FINANCIAL)],
        granted_permissions={Permission.READ},
        audit_sink=lambda adapter_id, name, req: audit.append((adapter_id, name, req)),
    )

    result = adapter.execute(ActionRequest("refund", parameters={"amount": 299}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"
    assert audit[0][0] == "fake"
    assert audit[0][1] == "refund"


def test_unknown_capability():
    adapter = _FakeAdapter()
    result = adapter.execute(ActionRequest("nope"))
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_granted_capability_executes():
    adapter = _FakeAdapter(granted_permissions={Permission.READ})
    result = adapter.execute(ActionRequest("read_thing", parameters={"x": 1}))
    assert result.status is ActionStatus.SUCCESS
    assert result.output == {"echo": {"x": 1}}

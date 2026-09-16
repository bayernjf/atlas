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
                    "input_schema": {},
                    "output_schema": {},
                },
                {
                    "name": "write_thing",
                    "description": "写入",
                    "permission": "write",
                    "idempotent": True,
                    "input_schema": {},
                    "output_schema": {},
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


# --- 04 §4.9 Capability JSON Schema 子集（U32） ---


def test_capability_accepts_empty_and_subset_schemas():
    assert Capability("a", "a", action="a").input_schema == {}
    Capability(
        "b",
        "b",
        action="b",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout": {"type": "number", "exclusiveMinimum": True},
                "body": {},
                "to": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            },
            "required": ["url"],
        },
        output_schema={
            "properties": {"rows": {"type": "array", "items": {"type": "object"}}},
        },
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/defs/x"},
        {"format": "email"},
        {"type": "object", "properties": {"x": {"patternProperties": {"^a": {}}}}},
        {"type": "object", "properties": {"x": {"x-widget": "secret"}}},
        {"type": "str"},
        {"type": "object", "properties": []},
        {"type": "object", "required": ["x", 1]},
        {"type": "array", "items": []},
        {"type": "object", "oneOf": []},
        {"type": "object", "enum": "not-a-list"},
        {"type": "object", "minimum": "1"},
        {"type": "string", "minLength": True},
        {"type": "string", "pattern": 123},
    ],
)
def test_capability_rejects_schema_outside_subset(schema):
    with pytest.raises(ValueError):
        Capability("a", "a", action="a", input_schema=schema)


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_capability_requires_object_root(field):
    with pytest.raises(ValueError):
        Capability("a", "a", action="a", **{field: {"type": "array"}})
    with pytest.raises(ValueError):
        Capability("a", "a", action="a", **{field: {"description": "无 type 无 properties"}})


def test_demo_capabilities_project_subset_schemas():
    from atlas.graph.loader import build_demo_registry

    projected = {
        (adapter["id"], tool["name"]): tool
        for adapter in build_demo_registry().list_adapters()
        for tool in adapter["tools"]
    }
    assert set((adapter_id, name) for adapter_id, name in projected) == {
        ("shop", "login"),
        ("shop", "list_pending_refunds"),
        ("shop", "execute_refund"),
        ("shop", "request_human_approval"),
        ("shop", "process_refund"),
        ("http", "request"),
        ("database", "query"),
        ("database", "execute"),
        ("message", "send"),
    }

    expected_output_keys = {
        ("shop", "login"): {"logged_in"},
        ("shop", "list_pending_refunds"): {"orders"},
        ("shop", "execute_refund"): {"order_id", "status"},
        ("shop", "request_human_approval"): {"order_id", "status"},
        ("shop", "process_refund"): {"order_id", "status"},
        ("http", "request"): {"status", "headers", "body"},
        ("database", "query"): {"columns", "rows", "row_count", "truncated"},
        ("database", "execute"): {"rowcount"},
        ("message", "send"): {"id", "channel", "to", "subject", "body", "sent_at"},
    }
    for key, tool in projected.items():
        assert isinstance(tool["input_schema"], dict)
        assert isinstance(tool["output_schema"], dict)
        assert set(tool["output_schema"]["properties"]) == expected_output_keys[key]

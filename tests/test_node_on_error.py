"""打包 AJ（docs/121）：节点级 on_error 真执行（U1324–U1331）。

基线：retry.on_error/max_retries 自 W7 在 DSL 声明但 loader 从未读取；
本套件钉住 stop/continue/jump_to 三值语义、max_retries 真重试、backoff 解析、
DSL 编译期三码校验、控制流穿透与模板回归。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import GraphDSL, parse_graph, validate_graph_report
from atlas.graph.loader import RunNodeFailed, run_graph
from atlas.harness.base import ActionResult, ActionStatus, Capability
from atlas.harness.registry import AdapterRegistry
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)


class _FailAdapter:
    adapter_id = "x"
    adapter_type = "test"
    calls = 0

    def list_capabilities(self):
        return [Capability(name="y", description="d", action="act")]

    def execute(self, req):
        _FailAdapter.calls += 1
        return ActionResult(status=ActionStatus.FAILED, output={}, error=None)


class _FlakyAdapter:
    """第一次抛异常、其后成功（U1327 重试路径）。"""

    adapter_id = "flaky"
    adapter_type = "test"
    calls = 0

    def list_capabilities(self):
        return [Capability(name="y", description="d", action="act")]

    def execute(self, req):
        _FlakyAdapter.calls += 1
        if _FlakyAdapter.calls == 1:
            raise RuntimeError("transient boom")
        return ActionResult(status=ActionStatus.SUCCESS, output={"ok": True}, error=None)


def _registry(*adapters) -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def _graph(on_error, *, error_target=None, max_retries=0, backoff="1ms"):
    retry = {"on_error": on_error, "max_retries": max_retries, "backoff": backoff}
    if error_target:
        retry["error_target"] = error_target
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "t", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "a", "type": "tool_call", "name": "a",
                 "config": {"tool": "x/y"}, "retry": retry},
                # b/c 是被观察的下游/落点：统一 continue，不被 a 的 stop 语义干扰
                {"id": "b", "type": "tool_call", "name": "b",
                 "config": {"tool": "x/y"}, "retry": {"on_error": "continue"}},
                {"id": "c", "type": "tool_call", "name": "c",
                 "config": {"tool": "x/y"}, "retry": {"on_error": "continue"}},
            ],
            "edges": [
                {"id": "e1", "source": "t", "target": "a"},
                {"id": "e2", "source": "a", "target": "b"},
                {"id": "e3", "source": "b", "target": "c"},
            ],
        }
    )


# --- U1324 stop ---------------------------------------------------------------


def test_u1324_stop_aborts_run_and_skips_downstream():
    registry = _registry(_FailAdapter())
    with pytest.raises(RunNodeFailed) as excinfo:
        run_graph(_graph("stop"), registry=registry)
    assert excinfo.value.node_id == "a"


def test_u1324_stop_maps_to_structured_500_via_api():
    graph = {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "ghost/nope"}, "retry": {"on_error": "stop"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    graph_id = client.post(
        "/api/graphs", json=graph, headers=DEFAULT_AUTH_HEADER
    ).json()["id"]
    resp = client.post(
        f"/api/graphs/{graph_id}/run", json={"inputs": {}}, headers=DEFAULT_AUTH_HEADER
    )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["code"] == "NODE_EXECUTION_FAILED"
    assert detail["nodeId"] == "tool-1"


# --- U1325 continue -----------------------------------------------------------


def test_u1325_continue_runs_downstream_and_completes():
    registry = _registry(_FailAdapter())
    result = run_graph(_graph("continue"), registry=registry)
    assert result["status"] == "completed"
    assert set(result["outputs"]) == {"t", "a", "b", "c"}
    assert result["outputs"]["a"]["attempts"] == 1


# --- U1326 jump_to ------------------------------------------------------------


def test_u1326_jump_to_routes_to_error_target_on_failure():
    registry = _registry(_FailAdapter())
    result = run_graph(_graph("jump_to", error_target="c"), registry=registry)
    assert "c" in result["outputs"]
    assert "b" not in result["outputs"]  # 正常后继零执行
    # 内部路由键在路由消费后剔除，不进持久产出
    assert "__on_error_target" not in result["outputs"]["a"]


# --- U1327 retry --------------------------------------------------------------


def test_u1327_retry_succeeds_on_second_attempt():
    _FlakyAdapter.calls = 0
    registry = _registry(_FlakyAdapter())
    graph = _graph("stop", max_retries=1)
    for node in graph.nodes:
        if node.id == "a":
            node.config["tool"] = "flaky/y"
    result = run_graph(graph, registry=registry)
    assert result["status"] == "completed"
    assert result["outputs"]["a"]["attempts"] == 2


def test_u1327_max_retries_exhausted_falls_back_to_on_error():
    _FailAdapter.calls = 0
    registry = _registry(_FailAdapter())
    result = run_graph(_graph("continue", max_retries=2), registry=registry)
    assert result["outputs"]["a"]["attempts"] == 3  # 1 + 2 retries
    assert _FailAdapter.calls >= 3


def test_u1327_backoff_parses_ms_and_seconds():
    from atlas.graph.loader import _parse_backoff_seconds

    assert _parse_backoff_seconds("500ms") == 0.5
    assert _parse_backoff_seconds("2.5s") == 2.5
    assert _parse_backoff_seconds("1h") == 0.0  # 非法兜底（编译期已拦）


# --- U1328 DSL 校验 -----------------------------------------------------------


def _codes(graph_dict: dict) -> list[str]:
    _, _, codes, _ = validate_graph_report(GraphDSL.model_validate(graph_dict))
    return codes


def test_u1328_jump_to_requires_error_target():
    graph = {
        "version": 1,
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t", "config": {"triggerType": "manual"}},
            {"id": "a", "type": "tool_call", "name": "a", "config": {"tool": "x/y"},
             "retry": {"on_error": "jump_to"}},
        ],
        "edges": [],
    }
    assert "NODE_ERROR_TARGET_REQUIRED" in _codes(graph)


def test_u1328_jump_to_target_must_exist_and_not_self():
    base = {
        "version": 1,
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t", "config": {"triggerType": "manual"}},
            {"id": "a", "type": "tool_call", "name": "a", "config": {"tool": "x/y"},
             "retry": {"on_error": "jump_to", "error_target": "ghost"}},
        ],
        "edges": [],
    }
    assert "NODE_ERROR_TARGET_INVALID" in _codes(base)
    base["nodes"][1]["retry"]["error_target"] = "a"
    assert "NODE_ERROR_TARGET_INVALID" in _codes(base)


def test_u1328_backoff_format_is_validated():
    graph = {
        "version": 1,
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t", "config": {"triggerType": "manual"}},
            {"id": "a", "type": "tool_call", "name": "a", "config": {"tool": "x/y"},
             "retry": {"backoff": "1h"}},
        ],
        "edges": [],
    }
    assert "NODE_RETRY_BACKOFF_INVALID" in _codes(graph)


def test_u1328_condition_node_cannot_use_jump_to():
    graph = {
        "version": 1,
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t", "config": {"triggerType": "manual"}},
            {"id": "cond", "type": "condition", "name": "c",
             "config": {"branches": [{"label": "b", "expression": "1 == 1", "target": "t"}],
                        "defaultTarget": "t"},
             "retry": {"on_error": "jump_to", "error_target": "t"}},
        ],
        "edges": [{"id": "e1", "source": "cond", "target": "t"}],
    }
    assert "NODE_ERROR_TARGET_INVALID" in _codes(graph)


# --- U1329 控制流穿透 ----------------------------------------------------------


def test_u1329_control_flow_exceptions_passthrough():
    """RunCancelled 在 on_error=continue 节点上仍穿透（不被 retry 吞、不转 FAILED 产出）。"""
    from atlas.collaboration.cancellations import RunCancelled

    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(RunCancelled):
        run_graph(_graph("continue"), registry=_registry(_FailAdapter()),
                  is_cancelled=is_cancelled)


# --- U1330 模板回归 ------------------------------------------------------------


def test_u1330_builtin_templates_pass_dsl_validation():
    from atlas.template.catalog import TEMPLATES

    for template in TEMPLATES:
        graph = GraphDSL.model_validate(template.graph)
        _, _, codes, _ = validate_graph_report(graph)
        backoff_issues = [c for c in codes if c == "NODE_RETRY_BACKOFF_INVALID"]
        assert not backoff_issues, f"{template.id}: {backoff_issues}"


# --- U1331 前端序列化（前端 vitest 已覆盖；此处钉住后端接受的 snake_case 形状） --


def test_u1331_backend_accepts_serialized_retry_shape():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "t", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "a", "type": "tool_call", "name": "a", "config": {"tool": "x/y"},
                 "retry": {"max_retries": 2, "backoff": "500ms", "timeout": 30,
                           "on_error": "jump_to", "error_target": "t"}},
            ],
            "edges": [{"id": "e1", "source": "t", "target": "a"}],
        }
    )
    retry = graph.nodes[1].retry
    assert retry.max_retries == 2
    assert retry.backoff == "500ms"
    assert retry.on_error == "jump_to"
    assert retry.error_target == "t"

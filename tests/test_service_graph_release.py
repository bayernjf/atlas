# -*- coding: utf-8 -*-
"""Fast unit tests for atlas.services.graph_release.

纯函数/参数化直调，不启 TestClient：graphVersion 标注、灰度配置与投影、
入站事件版本路由（M9）、debug 断点校验、tool_metric 载荷与草稿试编译。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from atlas.routing import RolloutState
from atlas.services import graph_release


# --- graph_version -----------------------------------------------------------

def test_graph_version_pins_release_or_marks_draft() -> None:
    assert graph_release.graph_version("g1", 3) == "g1@3"
    assert graph_release.graph_version("g1", None) == "g1@draft"


# --- rollout -----------------------------------------------------------------

def test_rollout_projection_is_camelcase_with_nullable_config() -> None:
    state = RolloutState(graph_id="g1")
    projected = graph_release.rollout_projection(state)
    assert projected["graphId"] == "g1"
    assert projected["config"] is None
    assert projected["startedAt"] is None
    assert "stable" in projected and "traffic" in projected


def test_rollout_config_or_422_rejects_non_object_and_bad_shapes() -> None:
    with pytest.raises(HTTPException) as not_obj:
        graph_release.rollout_config_or_422(["nope"])
    assert not_obj.value.status_code == 422
    assert not_obj.value.detail == "rollout 配置必须是 JSON 对象"

    with pytest.raises(HTTPException) as bad:
        graph_release.rollout_config_or_422({"strategy": "bogus"})
    assert bad.value.status_code == 422
    assert "灰度配置非法" in bad.value.detail


# --- resolve_event_version (M9 inbound routing) ------------------------------

def _routing_services(resolved):
    return SimpleNamespace(routing_store=SimpleNamespace(resolve=lambda *a, **k: resolved))


def _principal(tenant="t1"):
    return SimpleNamespace(tenant_id=tenant)


def test_resolve_event_version_without_event_keeps_legacy_path() -> None:
    services = _routing_services(None)
    payload = {"releaseVersion": 2}
    assert graph_release.resolve_event_version(services, _principal(), "g1", payload) == (2, None)


def test_resolve_event_version_rejects_conflicts() -> None:
    services = _routing_services(None)
    with pytest.raises(HTTPException) as both:
        graph_release.resolve_event_version(
            services, _principal(), "g1", {"event": {"payload": {}}, "releaseVersion": 1}
        )
    assert both.value.status_code == 422
    assert "互斥" in both.value.detail

    with pytest.raises(HTTPException) as debug:
        graph_release.resolve_event_version(
            services, _principal(), "g1", {"event": {}, "debug": {}}
        )
    assert debug.value.status_code == 422

    with pytest.raises(HTTPException) as shape:
        graph_release.resolve_event_version(services, _principal(), "g1", {"event": "x"})
    assert shape.value.status_code == 422


def test_resolve_event_version_409_when_no_releasable_version() -> None:
    services = _routing_services((None, None))
    with pytest.raises(HTTPException) as exc:
        graph_release.resolve_event_version(
            services, _principal(), "g1", {"event": {"payload": {"k": "v"}}}
        )
    assert exc.value.status_code == 409
    assert "无版本可路由" in exc.value.detail


def test_resolve_event_version_routes_to_pinned_release() -> None:
    seen: dict[str, object] = {}

    def resolve(graph_id, *, tenant, event):
        seen.update(graph_id=graph_id, tenant=tenant, event=event)
        return (4, "segment")

    services = SimpleNamespace(routing_store=SimpleNamespace(resolve=resolve))
    version, resolved = graph_release.resolve_event_version(
        services, _principal("t9"), "g1", {"event": {"channel": "webhook", "payload": {}}}
    )
    assert (version, resolved) == (4, 4)
    assert seen == {"graph_id": "g1", "tenant": "t9", "event": seen["event"]}


# --- validate_debug ----------------------------------------------------------

def _graph(*node_ids: str):
    return SimpleNamespace(nodes=[SimpleNamespace(id=nid, type="wait") for nid in node_ids])


def test_validate_debug_normalizes_points() -> None:
    normalized = graph_release.validate_debug(
        _graph("a", "b"),
        {"breakpoints": [
            {"node_id": "a", "hitCount": 2, "logMessage": " hit ", "onException": True},
            {"node_id": "b", "expression": "{{status}} == 'ok'"},
        ]},
    )
    assert normalized == [
        {"node_id": "a", "expression": None, "hitCount": 2, "logMessage": " hit ", "onException": True},
        {"node_id": "b", "expression": "{{status}} == 'ok'", "hitCount": None, "logMessage": None, "onException": False},
    ]


def test_validate_debug_aggregates_errors_into_422() -> None:
    with pytest.raises(HTTPException) as exc:
        graph_release.validate_debug(
            _graph("a"),
            {"breakpoints": [
                {"node_id": "ghost"},
                {"node_id": "a", "hitCount": 0},
                {"node_id": "a", "onException": "yes"},
            ]},
        )
    assert exc.value.status_code == 422
    assert "断点节点不存在：ghost" in exc.value.detail
    assert "hitCount 必须为正整数" in exc.value.detail
    assert "onException 必须为布尔值" in exc.value.detail


def test_validate_debug_rejects_non_object_body() -> None:
    with pytest.raises(HTTPException) as exc:
        graph_release.validate_debug(_graph("a"), ["x"])
    assert exc.value.status_code == 422
    assert exc.value.detail == "debug 必须为对象：{breakpoints: [...]}"


# --- tool_call_from_event ----------------------------------------------------

def test_tool_call_from_event_shape() -> None:
    payload = graph_release.tool_call_from_event(
        {"node_id": "n1", "tool": "http", "duration_ms": "12.5", "action_status": "ok"}
    )
    assert payload == {
        "node_id": "n1", "tool": "http", "duration_ms": 12.5,
        "action_status": "ok", "error_code": None,
    }


# --- load_graph_or_404 / promote_compile --------------------------------------

def test_load_graph_or_404_parses_or_raises_404() -> None:
    raw = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool-1", "type": "tool_call", "name": "动作",
             "config": {"tool": "op-do"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    services = SimpleNamespace(graph_store=SimpleNamespace(get=lambda gid, rv: raw))
    graph = graph_release.load_graph_or_404(services, "g1", None)
    assert [node.id for node in graph.nodes] == ["trigger-1", "tool-1"]

    missing = SimpleNamespace(graph_store=SimpleNamespace(get=lambda gid, rv: None))
    with pytest.raises(HTTPException) as exc:
        graph_release.load_graph_or_404(missing, "g1", None)
    assert exc.value.status_code == 404
    assert "Graph 不存在" in exc.value.detail


def test_promote_compile_reports_validation_issues_without_raising(monkeypatch) -> None:
    from atlas.graph.dsl import GraphValidationError

    def boom(*args, **kwargs):
        raise GraphValidationError(
            errors=["e1"], codes=["C1"], params=[{"k": "v"}], locations=[{"index": 0}],
        )

    monkeypatch.setattr(graph_release, "parse_graph", lambda graph: object())
    monkeypatch.setattr(graph_release, "compile_graph", boom)
    report = graph_release.promote_compile(
        object(), {}, graph_id="g1", runtime_registry_of=lambda services: None
    )
    assert report == {
        "ok": False, "issues": ["e1"], "codes": ["C1"],
        "params": [{"k": "v"}], "locations": [{"index": 0}],
    }


def test_promote_compile_ok_report(monkeypatch) -> None:
    monkeypatch.setattr(graph_release, "parse_graph", lambda graph: object())
    monkeypatch.setattr(graph_release, "compile_graph", lambda *a, **k: object())
    report = graph_release.promote_compile(
        object(), {}, graph_id="g1", runtime_registry_of=lambda services: None
    )
    assert report["ok"] is True and report["issues"] == []


def test_rollout_config_valid_body_parses() -> None:
    try:
        config = graph_release.rollout_config_or_422({"traffic": 10})
    except (HTTPException, ValidationError) as exc:  # pragma: no cover - shape guard
        raise AssertionError(f"valid config rejected: {exc}")
    assert config is not None

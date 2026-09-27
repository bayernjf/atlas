# -*- coding: utf-8 -*-
"""R8（docs/77 §4）守护：prod 档下裸工具名必须显式 FAILED，不再静默"模拟成功"。

口径走 docs/75 的 demo 面总开关（`security/bootstrap.demo_surface_enabled`）：非 prod 恒开、
prod 仅 `ATLAS_ENABLE_DEMO_MOCK=1` 显式开。本批**只收紧 prod**——demo/dev 的既有 SIMULATED
契约（`monitoring/records.py:40`）零变化，那批用裸名的既有测试也因此在 dev 档不受影响。
"""
from __future__ import annotations

import os

import pytest

from atlas.graph.dsl import NodeDSL
from atlas.graph.loader import _execute_tool, _tool_metric_event, build_demo_registry


@pytest.fixture(autouse=True)
def _neutral_profile():
    """每条用例自带档位，不继承机器上残留的 ATLAS_ENV。"""
    saved = os.environ.get("ATLAS_ENV")
    os.environ["ATLAS_ENV"] = "dev"
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    yield
    if saved is None:
        os.environ.pop("ATLAS_ENV", None)
    else:
        os.environ["ATLAS_ENV"] = saved
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)


def _bare_node(tool: str = "op-approve") -> NodeDSL:
    return NodeDSL(id="tool-x", type="tool_call", name="x", config={"tool": tool})


def test_u939_prod_bare_tool_name_fails_instead_of_simulating(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    output = _execute_tool(_bare_node(), {}, build_demo_registry())
    assert output["result"]["status"] == "FAILED"
    assert output["action_status"] == "FAILED"
    assert "adapter/capability" in output["result"]["error"]


def test_u940_demo_flag_reopens_the_simulated_path(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_ENABLE_DEMO_MOCK", "1")
    output = _execute_tool(_bare_node(), {}, build_demo_registry())
    assert output["result"]["status"] == "SIMULATED"


def test_u941_dev_keeps_the_simulated_contract():
    output = _execute_tool(_bare_node(), {}, build_demo_registry())
    assert output["result"]["status"] == "SIMULATED"


def test_u942_prod_shaped_tool_name_still_reaches_the_registry(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    node = NodeDSL(
        id="tool-login",
        type="tool_call",
        name="login",
        config={"tool": "shop/login", "params": "ignored"},
    )
    output = _execute_tool(node, {}, build_demo_registry())
    assert output["result"] == {"logged_in": True}


def test_u943_prod_bare_tool_name_is_failed_not_simulated_in_metrics(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    output = _execute_tool(_bare_node(), {}, build_demo_registry())
    event = _tool_metric_event(
        node_id="tool-x", tool_name="op-approve", output=output, duration_ms=1.0
    )
    assert event["action_status"] == "FAILED"
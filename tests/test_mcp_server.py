"""MCP server 面 v1 验收（docs/91 §6 八层）。

覆盖：协议面 discover/tools、每工具 happy path、只读不变量、失败口径（ToolError→isError）、
租户绑定 fail-closed、stdio 入口 stdout 纯净性、resumeToken 收窄、内存档自报。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from atlas.iam.principals import SEED_TENANTS
from atlas.mcp import TOOL_NAMES
from atlas.mcp.server import TenantBindingError, build_server, resolve_tenant_id


def _server(tenant_id: str):
    return build_server(tenant_id)


def _text(result) -> str:
    """从工具结果里取首段 text content。"""
    contents = result.content if result.content else []
    texts = [c.text for c in contents if getattr(c, "text", None) is not None]
    return texts[0] if texts else ""


def _call_sync(server, name: str, arguments: dict | None = None) -> object:
    async def _run():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(_run())


def _list_tools_sync(server) -> list:
    async def _run():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return asyncio.run(_run())


# --- 协议面 ----------------------------------------------------------------


def test_u_mcp_1_tools_list_exposes_seven_frozen_tools():
    tools = _list_tools_sync(_server("t1"))
    names = [t.name for t in tools]
    assert names == list(TOOL_NAMES)
    # v1 不出 outputSchema / structuredContent（docs/91 §7 第 3 项）
    for t in tools:
        assert getattr(t, "output_schema", None) in (None, {})
    # 工具名 snake_case、不带斜杠（docs/77 R8 教训）
    for name in names:
        assert name.startswith("atlas_") and "/" not in name


def test_u_mcp_2_each_tool_returns_parseable_text():
    from atlas.mcp.server import _REGISTRY

    server = _server("t1")
    # 注入一张图，供 atlas_get_graph 走 happy path
    gstore = _REGISTRY.get("t1").graph_store
    gid = gstore.save({"name": "seed", "nodes": [], "edges": []})
    tpls = json.loads(_text(_call_sync(server, "atlas_list_templates")))["items"]
    tpl_id = tpls[0]["id"]
    args = {
        "atlas_get_graph": {"graph_id": gid},
        "atlas_get_template": {"template_id": tpl_id},
        "atlas_list_runs": {"limit": 5},
    }
    for name in TOOL_NAMES:
        result = _call_sync(server, name, args.get(name))
        assert result.is_error is False, f"{name} 不应报工具错误"
        payload = json.loads(_text(result))
        assert isinstance(payload, dict)
        # 列表类工具统一带 backend 自报（§7 第 2 项）；读取单体的工具回实体本体。
        if name.startswith("atlas_list_"):
            assert "backend" in payload


# --- 失败口径 --------------------------------------------------------------


def test_u_mcp_3_get_graph_unknown_id_is_tool_error():
    result = _call_sync(_server("t1"), "atlas_get_graph", {"graph_id": "no-such"})
    assert result.is_error is True
    assert "no-such" in _text(result)


def test_u_mcp_4_get_template_unknown_id_is_tool_error():
    result = _call_sync(_server("t1"), "atlas_get_template", {"template_id": "no-such"})
    assert result.is_error is True


def test_u_mcp_5_list_runs_rejects_bad_status():
    result = _call_sync(_server("t1"), "atlas_list_runs", {"status": "bogus"})
    assert result.is_error is True


def test_u_mcp_6_list_runs_rejects_out_of_range_limit():
    for limit in (0, 201):
        result = _call_sync(_server("t1"), "atlas_list_runs", {"limit": limit})
        assert result.is_error is True, f"limit={limit} 应被拒"


def test_u_mcp_7_list_runs_accepts_valid_limit_and_status():
    result = _call_sync(_server("t1"), "atlas_list_runs", {"status": "running", "limit": 5})
    assert result.is_error is False
    payload = json.loads(_text(result))
    # limit 透传到投影（run_store.list 的契约由 run store 单测覆盖，这里只看形态不崩）
    assert payload["backend"]


# --- 只读不变量 ------------------------------------------------------------


def test_u_mcp_8_read_only_invariant_no_mutation():
    # 比对 server 实际使用的模块级 registry，调用前后 run/图计数不变。
    from atlas.mcp.server import _REGISTRY

    server = _server("t1")
    svc = _REGISTRY.get("t1")
    gstore = svc.graph_store
    gid = gstore.save({"name": "seed", "nodes": [], "edges": []})
    tpls = json.loads(_text(_call_sync(server, "atlas_list_templates")))["items"]
    tpl_id = tpls[0]["id"]
    args = {
        "atlas_get_graph": {"graph_id": gid},
        "atlas_get_template": {"template_id": tpl_id},
        "atlas_list_runs": {"limit": 5},
    }
    runs_before = len(svc.run_store.list())
    graphs_before = len(gstore.list())
    for name in TOOL_NAMES:
        r = _call_sync(server, name, args.get(name))
        assert r.is_error is False
    runs_after = len(svc.run_store.list())
    graphs_after = len(gstore.list())
    assert runs_after == runs_before, "MCP 只读面不应产生任何 run"
    assert graphs_after == graphs_before, "MCP 只读面不应改动图存储"


# --- 租户绑定 fail-closed --------------------------------------------------


def test_u_mcp_9_tenant_binding_fail_closed(monkeypatch):
    monkeypatch.delenv("ATLAS_MCP_TENANT_ID", raising=False)
    with pytest.raises(TenantBindingError):
        resolve_tenant_id()
    with pytest.raises(TenantBindingError):
        resolve_tenant_id({"ATLAS_MCP_TENANT_ID": "ghost-tenant"})
    # 已知租户通过；且不猜测、不落默认
    assert resolve_tenant_id({"ATLAS_MCP_TENANT_ID": "t1"}) == "t1"
    assert "t2" in SEED_TENANTS


# --- resumeToken 收窄 ------------------------------------------------------


def test_u_mcp_10_interruptions_strip_resume_token():
    # MCP 面必须把 include_resume_token=False 传给只读投影（§7 刻意收窄）：
    # 即便底层行带有 resumeToken，对外面也不携带可用于续跑的令牌。
    import atlas.mcp.server as server_mod

    captured = {}

    def fake_view(*, backend, tenant_id, engine, run_status_of, include_resume_token):
        captured["include_resume_token"] = include_resume_token
        return {
            "backend": backend,
            "visibility": "tenant-scoped",
            "items": [
                {"runId": "r1", "state": "awaiting"},
            ],
        }

    monkeypatch_view = pytest.MonkeyPatch()
    monkeypatch_view.setattr(server_mod, "interruption_view", fake_view)
    try:
        result = _call_sync(_server("t1"), "atlas_list_interruptions")
    finally:
        monkeypatch_view.undo()
    assert captured.get("include_resume_token") is False, "MCP 面必须收窄 resumeToken"
    payload = json.loads(_text(result))
    assert payload["items"], "应至少有一条帧被返回"
    for item in payload["items"]:
        assert "resumeToken" not in item, "MCP 面不得外带 resumeToken"


# --- 内存档自报 ------------------------------------------------------------


def test_u_mcp_11_memory_backend_self_reports(monkeypatch):
    # 默认 STORAGE_BACKEND=memory：列表面必须显式自报 backend，且挂起面自报
    # visibility=frames-not-persisted（"空"≠"平台没数据"）。
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "memory")
    from atlas.iam.registry import STORAGE_BACKEND

    # 重新导入以拿到 memory 档（测试默认就是 memory，这里仅显式保证）
    assert STORAGE_BACKEND == "memory"
    graphs = json.loads(_text(_call_sync(_server("t1"), "atlas_list_graphs")))
    assert graphs["backend"] == "memory"
    interrupts = json.loads(_text(_call_sync(_server("t1"), "atlas_list_interruptions")))
    assert interrupts["visibility"] == "frames-not-persisted"


# --- stdio 入口 stdout 纯净性 ---------------------------------------------


def test_u_mcp_12_stdio_entrypoint_stdout_is_pure_on_missing_tenant():
    env = {k: v for k, v in os.environ.items() if k != "ATLAS_MCP_TENANT_ID"}
    proc = subprocess.run(
        [sys.executable, "-m", "atlas.mcp"],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 2, f"缺租户应 fail-closed 退出 2，实际 {proc.returncode}"
    assert proc.stdout == "", "stdio 入口 stdout 必须纯净（只走 stderr）"
    assert proc.stderr.strip(), "拒绝原因应写 stderr"


def test_u_mcp_13_stdio_entrypoint_rejects_unknown_tenant():
    env = dict(os.environ, ATLAS_MCP_TENANT_ID="ghost")
    proc = subprocess.run(
        [sys.executable, "-m", "atlas.mcp"],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""

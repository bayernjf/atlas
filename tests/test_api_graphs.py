"""Graph 保存/读取/编译/运行 REST 测试（docs/12 §5，FastAPI TestClient 进程内）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import compile_graph
from atlas.security.bootstrap import demo_surface_enabled

client = TestClient(app)


def _sample_graph():
    return {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
             "config": {"promptTemplate": "限额 {{global.approval_limit}} 内自动通过"}},
            # 打包 AJ（docs/121 §5 D-7）：沿用「FAILED 也 completed」旧语义，显式 continue
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "web-playwright/click"}, "retry": {"on_error": "continue"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def test_health():
    assert client.get("/api/health").json() == {
        "status": "ok",
        "demo_surface": demo_surface_enabled(),
    }


def test_save_get_compile_run_lifecycle():
    saved = client.post("/api/graphs", json=_sample_graph())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]
    assert saved.json()["version"] == 1

    fetched = client.get(f"/api/graphs/{graph_id}")
    assert fetched.status_code == 200
    assert len(fetched.json()["nodes"]) == 3

    compiled = client.post(f"/api/graphs/{graph_id}/compile")
    assert compiled.status_code == 200
    body = compiled.json()
    assert [node["id"] for node in body["nodes"]] == ["trigger-1", "ai_decision-1", "tool_call-1"]
    assert body["entrypoints"] == ["trigger-1"]
    assert body["terminals"] == ["tool_call-1"]
    assert body["edges"][0] == {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"}

    run = client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {"approval_limit": "999"}})
    assert run.status_code == 200
    result = run.json()
    assert result["status"] == "completed"
    assert "限额 999 内自动通过" == result["outputs"]["ai_decision-1"]["prompt_rendered"]
    assert result["trace"][-1].startswith("tool_call-1")


def test_get_compile_run_unknown_graph_returns_404():
    assert client.get("/api/graphs/nope").status_code == 404
    assert client.post("/api/graphs/nope/compile").status_code == 404
    assert client.post("/api/graphs/nope/run").status_code == 404


def test_save_invalid_graph_returns_422_with_error_list():
    bad = _sample_graph()
    bad["nodes"][1]["config"] = {"promptTemplate": ""}
    bad["edges"][0]["target"] = "ghost"
    saved = client.post("/api/graphs", json=bad)
    assert saved.status_code == 422
    detail = saved.json()["detail"]
    assert isinstance(detail, list)
    assert any("提示词" in error for error in detail)
    assert any("ghost" in error for error in detail)


# --- D78：compile 端点 prod 档缺 registry 500（打包 AT 落码时发现） -----------------


def test_d78_compile_endpoint_injects_runtime_registry_in_prod(monkeypatch):
    """prod 且演示面关闭：compile 端点注入本租户 registry ⇒ 200（修前 500）。

    `_resolve_registry(None)` 在 prod 拒演示兜底（loader.py:409-412），compile 端点
    与 run 端点同构注入即可，不削弱 loader 的 prod 拒绝本身（见反向门）。

    prod 档还需过首登强制改密门（打包 AV）：打桩 `user_store.get` 返回带轮换位的
    admin-a 副本——真改密会 revoke 跨测试模块收集期签发的会话令牌（同
    test_demo_surface_prod_gate.py U1391 的既定打法）。"""
    from atlas.iam import deps as iam_deps

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    real_get = iam_deps.user_store.get

    def _rotated_get(tenant_id: str, username: str):
        account = real_get(tenant_id, username)
        if account is not None and username == "admin-a":
            return account.model_copy(
                update={"password_rotated_at": "2026-01-01T00:00:00+00:00"}
            )
        return account

    monkeypatch.setattr(iam_deps.user_store, "get", _rotated_get)
    saved = client.post("/api/graphs", json=_sample_graph())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]
    compiled = client.post(f"/api/graphs/{graph_id}/compile")
    assert compiled.status_code == 200
    body = compiled.json()
    assert [node["id"] for node in body["nodes"]] == [
        "trigger-1", "ai_decision-1", "tool_call-1"
    ]
    assert body["entrypoints"] == ["trigger-1"]


def test_d78_compile_graph_without_registry_still_fails_closed_in_prod(monkeypatch):
    """反向门：D78 只补端点注入，`_resolve_registry` 的 prod 拒绝一字不动。

    若本文件的正例靠"关掉 loader 的门"变绿，这条必须红。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    with pytest.raises(RuntimeError):
        compile_graph(parse_graph(_sample_graph()), graph_id="d78-guard")

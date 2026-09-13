"""Graph 保存/读取/编译/运行 REST 测试（docs/12 §5，FastAPI TestClient 进程内）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import app

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
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


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

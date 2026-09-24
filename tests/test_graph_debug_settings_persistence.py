"""G4（docs/60 §5）：断点随 Graph JSON 顶层 ``debugSettings`` 持久化的后端坐实。

- GraphDSL 解析对未知顶层字段 debugSettings 容忍（Pydantic 默认 ignore），不报错；
- 图存储透传原始 dict（save 存 raw），GET 原样回含 debugSettings；
- 持久化断点不参与服务端执行：普通运行（body 无 debug）即使图里带行断点也不暂停。
"""

from __future__ import annotations

from atlas.graph.dsl import GraphDSL, parse_graph

# 复用 test_api_graphs 的模块级 client（conftest autouse 已为其注入 admin 会话）。
from tests.test_api_graphs import client  # noqa: E402


def _graph_with_debug_settings():
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
        # docs/60 §5.1：前端随图持久化的断点（含一个普通行断点）。
        "debugSettings": {
            "breakpoints": [
                {"nodeId": "ai_decision-1"},
                {"nodeId": "tool_call-1", "expression": "x > 1", "hitCount": 2,
                 "logMessage": "hit", "onException": True},
            ]
        },
    }


def test_parse_graph_tolerates_debug_settings():
    graph = parse_graph(_graph_with_debug_settings())
    assert isinstance(graph, GraphDSL)
    assert [node.id for node in graph.nodes] == [
        "trigger-1",
        "ai_decision-1",
        "tool_call-1",
    ]
    # 执行侧模型不暴露该字段：持久化断点不会被编译/运行读取。
    assert not hasattr(graph, "debugSettings")


def test_save_get_preserves_debug_settings():
    saved = client.post("/api/graphs", json=_graph_with_debug_settings())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]

    fetched = client.get(f"/api/graphs/{graph_id}")
    assert fetched.status_code == 200
    raw = fetched.json()
    assert raw["debugSettings"]["breakpoints"][0] == {"nodeId": "ai_decision-1"}
    assert raw["debugSettings"]["breakpoints"][1]["onException"] is True


def test_normal_run_ignores_persisted_debug_settings():
    saved = client.post("/api/graphs", json=_graph_with_debug_settings())
    graph_id = saved.json()["id"]

    # 普通运行 body 不带 debug：图里持久化的行断点不得导致暂停。
    run = client.post(f"/api/graphs/{graph_id}/run", json={"inputs": {"approval_limit": "999"}})
    assert run.status_code == 200
    result = run.json()
    assert result["status"] == "completed"
    assert result["trace"][-1].startswith("tool_call-1")

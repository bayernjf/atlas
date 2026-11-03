"""图规模与请求体上限测试（2026-11-02 评审 P2-4）。

保存图的认证面此前只做 pydantic 形状校验：超大图造成内存放大、编译期
全图扫描变慢。现在：节点 >500 / 边 >2000 由 validate_graph_report 报精确码
（GRAPH_NODES_LIMIT_EXCEEDED / GRAPH_EDGES_LIMIT_EXCEEDED），/api/ 写请求体
按 Content-Length 预拒 413（默认 2 MiB，ATLAS_MAX_BODY_BYTES 可调）。
"""

from __future__ import annotations

# 复用 test_api_graphs 的模块级 client（conftest autouse 夹具已为其注入 t1 admin 会话）。
from tests.test_api_graphs import client


def _chain_graph(n_nodes: int) -> dict:
    nodes = [
        {"id": "trigger-1", "type": "trigger", "name": "触发",
         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/limit"}},
    ]
    for i in range(1, n_nodes):
        nodes.append({"id": f"tool-{i}", "type": "tool_call", "name": f"工具{i}",
                      "config": {"tool": "web-playwright/click"}, "retry": {"on_error": "continue"}})
    edges = []
    for i in range(1, n_nodes):
        source = "trigger-1" if i == 1 else f"tool-{i - 1}"
        edges.append({"id": f"e{i}", "source": source, "target": f"tool-{i}"})
    return {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": nodes,
        "edges": edges,
    }


def test_graph_at_node_limit_saves():
    resp = client.post("/api/graphs", json=_chain_graph(500))
    assert resp.status_code == 200, resp.text


def test_graph_over_node_limit_422_with_precise_code():
    resp = client.post("/api/graphs", json=_chain_graph(501))
    assert resp.status_code == 422
    body = resp.json()
    assert "GRAPH_NODES_LIMIT_EXCEEDED" in body["codes"]
    idx = body["codes"].index("GRAPH_NODES_LIMIT_EXCEEDED")
    assert body["params"][idx]["max"] == 500
    assert body["params"][idx]["count"] == 501


def test_graph_over_edge_limit_422_with_precise_code():
    # 链 500 边 + 1600 条重边凑到 2100 > 2000；其余校验码并存无妨，规模码必须在。
    graph = _chain_graph(501)
    extra = [
        {"id": f"x{i}", "source": "trigger-1", "target": f"tool-{(i % 499) + 1}"}
        for i in range(1, 1601)
    ]
    graph["edges"] = graph["edges"] + extra
    resp = client.post("/api/graphs", json=graph)
    assert resp.status_code == 422
    codes = resp.json()["codes"]
    assert "GRAPH_EDGES_LIMIT_EXCEEDED" in codes
    assert "GRAPH_NODES_LIMIT_EXCEEDED" in codes


def test_api_body_cap_413_when_content_length_exceeds(monkeypatch):
    from atlas.api import main as api_main

    monkeypatch.setattr(api_main, "MAX_API_BODY_BYTES", 64)
    resp = client.post("/api/graphs", json=_chain_graph(3))
    assert resp.status_code == 413


def test_api_body_cap_leaves_reads_and_small_writes_alone():
    assert client.post("/api/graphs", json=_chain_graph(3)).status_code == 200
    assert client.get("/api/graphs").status_code == 200

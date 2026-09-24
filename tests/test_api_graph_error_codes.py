"""Graph 422 响应体错误码契约测试（docs/17 §2.4）。

保存非法图时，422 body 除中文 detail 列表外，必须并行下发等长的 codes/params，
供前端按 code 映射本地文案；locations 稀疏侧车 index 不得越界。
"""

from __future__ import annotations

# 复用 test_api_graphs 的模块级 client（conftest autouse 夹具已为其注入 t1 admin 会话）。
from tests.test_api_graphs import client


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


def test_invalid_graph_422_carries_aligned_codes_and_params():
    bad = _sample_graph()
    bad["nodes"][1]["config"] = {"promptTemplate": ""}
    bad["edges"][0]["target"] = "ghost"

    resp = client.post("/api/graphs", json=bad)
    assert resp.status_code == 422
    body = resp.json()

    detail = body["detail"]
    codes = body["codes"]
    params = body["params"]
    assert isinstance(detail, list) and detail, "detail 为非空中文数组"
    assert len(codes) == len(detail), "codes 与 detail 等长、下标对齐"
    assert len(params) == len(detail), "params 与 detail 等长、下标对齐"
    assert all(isinstance(code, str) and code for code in codes)
    assert all(isinstance(prm, dict) for prm in params)

    # 代表性码：缺提示词 + 边目标缺失。
    assert "NODE_AI_PROMPT_REQUIRED" in codes
    assert "EDGE_TARGET_MISSING" in codes
    edge_idx = codes.index("EDGE_TARGET_MISSING")
    assert params[edge_idx]["target"] == "ghost"

    for loc in body.get("locations", []):
        assert 0 <= loc["index"] < len(detail)


def test_valid_graph_200_has_no_error_codes():
    resp = client.post("/api/graphs", json=_sample_graph())
    assert resp.status_code == 200

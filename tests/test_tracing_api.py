"""M10 批 3：两真实运行入口挂 trace——sync/stream RunRecord.trace_id 与 SSE 超集（U50 端到端）。

契约：04 §5.15 / 06 §6.15 / 12 SSE 端点注记。
- sync /run 与非 debug /run/stream 的 RunRecord 带 traceId；
- stream node_start/node_end 带 traceId/spanId/parentSpanId，终帧 result 带
  traceId/spanId/graphVersion（草稿 graphId@draft），完整 traceTree 不进 SSE。
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from atlas.api.main import app
from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _simple_graph() -> dict:
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "新退款",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
             "config": {"promptTemplate": "{{trigger-1.context.payload.reason}}"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "处理",
             "config": {"tool": "shop/process_refund"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def _parse_sse(text_lines: list[str]) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    event = None
    for line in text_lines:
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            frames.append((event, data))
    return frames


def test_sync_run_records_trace_id():
    graph_id = client.post("/api/graphs", json=_simple_graph(), headers=DEFAULT_AUTH_HEADER).json()["id"]
    resp = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "12349", "reason": "商品破损", "amount": 899}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    # sync 响应体不塞完整 span 树（RunGraphResponse 固定字段）
    assert "traceTree" not in resp.json()

    runs = client.get("/api/monitoring/runs", headers=DEFAULT_AUTH_HEADER).json()["items"]
    latest = next(r for r in runs if r["graph_id"] == graph_id)
    assert HEX32.match(latest["trace_id"])


def test_stream_run_sse_is_superset_and_omits_tree():
    graph_id = client.post("/api/graphs", json=_simple_graph(), headers=DEFAULT_AUTH_HEADER).json()["id"]
    with client.stream(
        "POST",
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {"order_id": "12345", "reason": "不想要了", "amount": 299}},
        headers=DEFAULT_AUTH_HEADER,
    ) as response:
        assert response.status_code == 200
        frames = _parse_sse(list(response.iter_lines()))

    by_event = {}
    for name, data in frames:
        by_event.setdefault(name, []).append(data)

    assert "node_start" in by_event and "node_end" in by_event
    trace_ids = set()
    for name in ("node_start", "node_end"):
        for data in by_event[name]:
            assert HEX32.match(data["traceId"])
            assert HEX16.match(data["spanId"])
            assert HEX16.match(data["parentSpanId"])
            assert "traceTree" not in data
            trace_ids.add(data["traceId"])

    result_frames = by_event.get("result", [])
    assert result_frames
    result = result_frames[-1]
    assert HEX32.match(result["traceId"])
    assert HEX16.match(result["spanId"])
    assert "parentSpanId" not in result  # root 无父
    assert result["graphVersion"] == f"{graph_id}@draft"
    assert "traceTree" not in result  # 完整树不进 SSE
    trace_ids.add(result["traceId"])

    # 整条流共享同一 traceId
    assert len(trace_ids) == 1

    # RunRecord 落 traceId
    runs = client.get("/api/monitoring/runs", headers=DEFAULT_AUTH_HEADER).json()["items"]
    latest = next(r for r in runs if r["graph_id"] == graph_id)
    assert latest["trace_id"] == result["traceId"]

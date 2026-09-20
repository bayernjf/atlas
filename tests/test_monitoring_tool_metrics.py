"""docs/28 §4.1 ⑧ 适配器调用级埋点（批 3 D28）。

覆盖：
- _tool_metric_event 三出口归一（SIMULATED / SUCCESS / FAILED＋error_code）；
- executor 对工具节点无条件 emit tool_metric（debug 流同样发，采集在入口层决定）；
- mock 命中不发（未真实调用）；子图内部工具的 tool_metric 被命名空间 wrapper 白名单吞掉
  （只统计顶层，与 collect_steps/node_end 口径一致）；
- summarize_tools 纯函数：调用/失败/模拟计数、错误码分布、真实调用 nearest-rank 分位，
  SIMULATED 不纳延迟分位，样本 0 给 None；
- RunRecord.tool_calls 纯超集默认空列表。
"""

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import _tool_metric_event, run_graph
from atlas.monitoring.metrics import summarize, summarize_tools
from atlas.monitoring.records import RunRecord, ToolCallMetric


def _metric(node_id, tool, status, duration, code=None):
    return ToolCallMetric(
        node_id=node_id, tool=tool, duration_ms=duration, action_status=status, error_code=code
    )


# ---------- _tool_metric_event 归一 ----------

def test_tool_metric_event_simulated():
    output = {"result": {"status": "SIMULATED", "tool": "local/op"}, "params_rendered": "x"}
    event = _tool_metric_event(
        node_id="t1", tool_name="local/op", output=output, duration_ms=0.012
    )
    assert event["type"] == "tool_metric"
    assert event["node_id"] == "t1"
    assert event["tool"] == "local/op"
    assert event["action_status"] == "SIMULATED"
    assert event["error_code"] is None


def test_tool_metric_event_success_uses_action_status():
    # 通用 JSON 通道成功：外层 action_status=SUCCESS，result 为适配器输出（可能无 status）
    output = {"result": {"channel": "email", "sent": True}, "action_status": "SUCCESS"}
    event = _tool_metric_event(
        node_id="t1", tool_name="message/send", output=output, duration_ms=9.5
    )
    assert event["action_status"] == "SUCCESS"
    assert event["error_code"] is None


def test_tool_metric_event_failed_carries_code():
    output = {
        "result": {"status": "FAILED", "code": "INVALID_PARAMETER", "message": "bad json"},
        "action_status": "FAILED",
    }
    event = _tool_metric_event(
        node_id="t1", tool_name="http/request", output=output, duration_ms=2.25
    )
    assert event["action_status"] == "FAILED"
    assert event["error_code"] == "INVALID_PARAMETER"


def test_tool_metric_event_unregistered_adapter_failed_without_code():
    # 未注册适配器：FAILED 但无 code（error 文本而非 code）
    output = {"result": {"status": "FAILED", "error": "适配器未注册：shop"}}
    event = _tool_metric_event(
        node_id="t1", tool_name="shop/refund", output=output, duration_ms=0.1
    )
    assert event["action_status"] == "FAILED"
    assert event["error_code"] is None


# ---------- executor emit ----------

def _single_tool_graph(tool="local-op", params="done"):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "tr",
                 "config": {"triggerType": "manual"}},
                {"id": "tool-1", "type": "tool_call", "name": "工具",
                 "config": {"tool": tool, "params": params}},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
    )


def test_executor_emits_tool_metric_for_simulated_call():
    events: list[dict] = []
    result = run_graph(_single_tool_graph(), graph_id="g", emit=events.append)
    assert result["status"] == "completed"
    metrics = [e for e in events if e.get("type") == "tool_metric"]
    assert len(metrics) == 1
    metric = metrics[0]
    assert metric["node_id"] == "tool-1"
    assert metric["tool"] == "local-op"
    assert metric["action_status"] == "SIMULATED"
    assert metric["error_code"] is None
    assert metric["duration_ms"] >= 0


def test_mock_hit_does_not_emit_tool_metric():
    events: list[dict] = []
    run_graph(
        _single_tool_graph(tool="message/send"),
        graph_id="g",
        emit=events.append,
        tool_mocks={"tool-1": {"result": {"status": "mocked"}}},
    )
    assert [e for e in events if e.get("type") == "tool_metric"] == []


def _child_with_tool():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "c-tool", "type": "tool_call", "name": "子工具",
                 "config": {"tool": "child-op", "params": "x"}},
            ],
            "edges": [{"id": "ce1", "source": "c-trigger", "target": "c-tool"}],
        }
    )


def _parent_with_subgraph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": "g-child", "inputs": {}}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "after-op", "params": "y"}},
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )


def test_subgraph_interior_tool_metric_is_swallowed():
    events: list[dict] = []
    result = run_graph(
        _parent_with_subgraph(),
        graph_id="g-parent",
        graph_resolver={"g-child": _child_with_tool()}.get,
        emit=events.append,
    )
    assert result["status"] == "completed"
    metrics = [e for e in events if e.get("type") == "tool_metric"]
    # 仅顶层 tool-after 一条；子层 c-tool 的 tool_metric 被命名空间白名单吞掉
    assert [m["node_id"] for m in metrics] == ["tool-after"]
    assert all("subgraphPath" not in m for m in metrics)


# ---------- 聚合纯函数 ----------

def test_summarize_tools_aggregates_counts_codes_and_percentiles():
    runs = [
        RunRecord(
            id="run-1", graph_id="g", mode="sync", status="completed",
            started_at="2026-09-20T00:00:00+00:00", finished_at="2026-09-20T00:00:01+00:00",
            duration_ms=10.0, nodes=[],
            tool_calls=[
                _metric("t1", "message/send", "SUCCESS", 8.0),
                _metric("t2", "message/send", "FAILED", 30.0, code="STRING"),
                _metric("t3", "message/send", "SUCCESS", 12.0),
            ],
        ),
        RunRecord(
            id="run-2", graph_id="g", mode="stream", status="completed",
            started_at="2026-09-20T00:01:00+00:00", finished_at="2026-09-20T00:01:01+00:00",
            duration_ms=5.0, nodes=[],
            tool_calls=[_metric("t4", "local/op", "SIMULATED", 0.01)],
        ),
    ]
    tools = summarize_tools(runs)
    by_tool = {item["tool"]: item for item in tools}
    msg = by_tool["message/send"]
    assert msg["calls"] == 3
    assert msg["failed"] == 1
    assert msg["simulated"] == 0
    assert msg["error_codes"] == {"STRING": 1}
    # 真实三次 8/12/30：p50=12, p95=30（nearest-rank）
    assert msg["p50"] == 12.0
    assert msg["p95"] == 30.0
    sim = by_tool["local/op"]
    assert sim["calls"] == 1
    assert sim["simulated"] == 1
    assert sim["failed"] == 0
    assert sim["p50"] is None and sim["p95"] is None  # SIMULATED 不纳分位


def test_summarize_tools_empty_when_no_runs():
    assert summarize_tools([]) == []
    assert summarize([])["tools"] == []


def test_run_record_tool_calls_defaults_empty_and_superset():
    record = RunRecord(
        id="run-1", graph_id="g", mode="sync", status="completed",
        started_at="2026-09-20T00:00:00+00:00", finished_at="2026-09-20T00:00:01+00:00",
        duration_ms=1.0, nodes=[],
    )
    assert record.tool_calls == []
    # dict 入参经 pydantic 转 ToolCallMetric
    record2 = RunRecord(
        id="run-2", graph_id="g", mode="sync", status="completed",
        started_at="2026-09-20T00:00:00+00:00", finished_at="2026-09-20T00:00:01+00:00",
        duration_ms=1.0, nodes=[],
        tool_calls=[{"node_id": "t", "tool": "x/y", "duration_ms": 1.5, "action_status": "SUCCESS"}],
    )
    assert isinstance(record2.tool_calls[0], ToolCallMetric)
    assert record2.tool_calls[0].error_code is None

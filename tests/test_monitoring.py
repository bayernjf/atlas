"""U30 基础监控告警纯逻辑与进程内存储（契约 04 §5.13，06 §6.11）。"""

import pytest

from atlas.monitoring import (
    MonitoringStore,
    NodeResult,
    RUN_RING_SIZE,
    RunRecord,
    RuleConfig,
    evaluate_rules,
    extract_node_results,
    percentile,
    rules_from_raw,
    summarize,
    validate_rules,
)

GRAPH = {
    "id": "g1",
    "nodes": [
        {"id": "trigger-1", "type": "trigger"},
        {"id": "query-1", "type": "tool_call"},
        {"id": "parallel-1", "type": "parallel"},
        {"id": "subgraph-1", "type": "subgraph"},
    ],
}

HEALTHY_OUTPUTS = {
    "trigger-1": {"context": {"payload": {}}},
    "query-1": {"result": {"status": "SUCCESS", "data": []}},
}
FAILED_TOOL_OUTPUTS = {
    "trigger-1": {"context": {"payload": {}}},
    "query-1": {"result": {"status": "FAILED", "code": "INVALID_PARAMETER", "message": "参数不是合法 JSON"}},
}
FOLDED_FAILURE_OUTPUTS = {
    "trigger-1": {"context": {"payload": {}}},
    "parallel-1": {"mode": "parallel", "status": "failed", "error": "branch notify failed"},
    "subgraph-1": {"mode": "subgraph", "status": "failed", "error": "subgraph boom"},
}


def _node(node_id: str, status: str = "success", error: str | None = None) -> NodeResult:
    return NodeResult(node_id=node_id, node_type="tool_call", status=status, error=error)


def _record(
    run_id: str = "run-1",
    *,
    graph_id: str = "g1",
    status: str = "completed",
    nodes: list[NodeResult] | None = None,
    duration_ms: float = 10.0,
    error: str | None = None,
) -> RunRecord:
    return RunRecord(
        id=run_id,
        graph_id=graph_id,
        mode="sync",
        status=status,
        started_at="2026-09-15T00:00:00+00:00",
        finished_at="2026-09-15T00:00:01+00:00",
        duration_ms=duration_ms,
        nodes=nodes or [],
        error=error,
    )


# --- extract_node_results -------------------------------------------------

def test_extract_success_and_action_failure_shapes():
    nodes = extract_node_results(GRAPH, HEALTHY_OUTPUTS)
    assert [(n.node_id, n.status) for n in nodes] == [("trigger-1", "success"), ("query-1", "success")]

    nodes = extract_node_results(GRAPH, FAILED_TOOL_OUTPUTS)
    failed = next(n for n in nodes if n.node_id == "query-1")
    assert failed.status == "failed"
    assert failed.node_type == "tool_call"
    assert failed.error == "参数不是合法 JSON"


def test_extract_folded_parallel_subgraph_failure_and_error_fallback():
    nodes = extract_node_results(GRAPH, FOLDED_FAILURE_OUTPUTS)
    by_id = {n.node_id: n for n in nodes}
    assert by_id["parallel-1"].status == "failed"
    assert by_id["parallel-1"].error == "branch notify failed"
    assert by_id["subgraph-1"].status == "failed"
    assert by_id["subgraph-1"].error == "subgraph boom"

    code_only = {"query-1": {"result": {"status": "FAILED", "code": "DB_SQL_ERROR"}}}
    assert extract_node_results(GRAPH, code_only)[0].error == "DB_SQL_ERROR"
    bare_fold = {"parallel-1": {"status": "failed"}}
    assert extract_node_results(GRAPH, bare_fold)[0].error == "节点执行失败"


def test_extract_skips_outputs_unknown_to_graph():
    outputs = {**HEALTHY_OUTPUTS, "__join__": {"synthetic": True}}
    nodes = extract_node_results(GRAPH, outputs)
    assert all(n.node_id != "__join__" for n in nodes)


# --- percentile / summarize ----------------------------------------------

def test_percentile_nearest_rank_and_empty():
    assert percentile([], 50) is None
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 100
    assert percentile([7], 95) == 7


def test_summarize_global_per_graph_and_failed_nodes_top():
    runs = [
        _record("run-1", duration_ms=10, nodes=[_node("a")]),
        _record("run-2", duration_ms=30, graph_id="g2", nodes=[_node("a"), _node("b", "failed", "boom")]),
        _record("run-3", duration_ms=20, graph_id="g2", nodes=[_node("b", "failed", "boom2")]),
    ]
    summary = summarize(runs)
    assert summary["total"] == 3 and summary["healthy"] == 1 and summary["unhealthy"] == 2
    assert summary["success_rate"] == pytest.approx(1 / 3)
    assert summary["p50"] == 20 and summary["p95"] == 30
    per_graph = {row["graph_id"]: row for row in summary["per_graph"]}
    assert per_graph["g1"]["success_rate"] == 1.0
    assert per_graph["g2"]["healthy"] == 0 and per_graph["g2"]["total"] == 2
    top = summary["failed_nodes"]
    assert top[0]["node_id"] == "b" and top[0]["count"] == 2 and top[0]["last_error"] == "boom2"
    assert summarize([])["success_rate"] is None and summarize([])["p50"] is None


# --- rule evaluation ------------------------------------------------------

def test_run_error_rule_only_on_uncaught_error():
    rules = RuleConfig()
    error_record = _record(status="error", error="ValueError: bad")
    events = evaluate_rules(record=error_record, healthy=False, recent_by_graph=[error_record], streak=1, rules=rules)
    assert {e.rule_id for e in events} == {"run_error"}
    assert events[0].severity == "critical" and "ValueError: bad" in events[0].message

    healthy = _record(nodes=[_node("a")])
    assert evaluate_rules(record=healthy, healthy=True, recent_by_graph=[healthy], streak=0, rules=rules) == []


def test_node_failed_rule_warning_message():
    record = _record(nodes=[_node("a"), _node("b", "failed", "boom"), _node("c", "failed")])
    events = evaluate_rules(record=record, healthy=False, recent_by_graph=[record], streak=1, rules=RuleConfig())
    warning = next(e for e in events if e.rule_id == "node_failed")
    assert warning.severity == "warning" and "b" in warning.message and "c" in warning.message


def test_consecutive_rule_fires_at_threshold_then_every_failure():
    rules = RuleConfig()
    history = [_record(f"run-{i}", nodes=[_node("b", "failed")]) for i in range(1, 4)]
    first_two = evaluate_rules(record=history[1], healthy=False, recent_by_graph=history[:2], streak=2, rules=rules)
    assert not [e for e in first_two if e.rule_id == "consecutive_failures"]
    at_threshold = evaluate_rules(record=history[2], healthy=False, recent_by_graph=history, streak=3, rules=rules)
    event = next(e for e in at_threshold if e.rule_id == "consecutive_failures")
    assert event.severity == "critical" and "连续 3 次" in event.message
    forth = evaluate_rules(record=_record("run-4", nodes=[_node("b", "failed")]),
                           healthy=False, recent_by_graph=history, streak=4, rules=rules)
    assert any(e.rule_id == "consecutive_failures" for e in forth)


def test_failure_rate_window_min_samples_and_threshold():
    rules = RuleConfig()
    healthy = _record("run-1", nodes=[_node("a")])
    failing = [_record(f"run-{i}", nodes=[_node("b", "failed")]) for i in range(2, 6)]
    recent = [healthy, *failing]
    events = evaluate_rules(record=failing[-1], healthy=False, recent_by_graph=recent, streak=4, rules=rules)
    rate_event = next(e for e in events if e.rule_id == "failure_rate")
    assert "4/5" in rate_event.message or "80%" in rate_event.message
    only_four = recent[:4]
    events = evaluate_rules(record=only_four[-1], healthy=False, recent_by_graph=only_four, streak=3, rules=rules)
    assert not [e for e in events if e.rule_id == "failure_rate"]


def test_healthy_run_emits_no_rule_events():
    record = _record(nodes=[_node("a")])
    assert evaluate_rules(record=record, healthy=True, recent_by_graph=[record], streak=0, rules=RuleConfig()) == []


# --- store: ring, streak, merge, alert lifecycle, rules ------------------

def _store_record(store: MonitoringStore, *, graph_id: str = "g1", status: str = "completed",
                  failed: bool = False, error: str | None = None, mode: str = "sync"):
    nodes = [NodeResult(node_id="n1", node_type="tool_call",
                        status="failed" if failed else "success",
                        error="boom" if failed else None)]
    return store.record_run(
        graph_id=graph_id, mode=mode, status=status,
        started_at="2026-09-15T00:00:00+00:00", duration_ms=12.0, nodes=nodes, error=error,
    )


def test_store_records_run_and_healthy_metrics():
    store = MonitoringStore()
    record = _store_record(store)
    assert record.id == "run-1" and record.status == "completed"
    metrics = store.snapshot_metrics()
    assert metrics["total"] == 1 and metrics["healthy"] == 1 and metrics["success_rate"] == 1.0
    assert metrics["p50"] == 12.0


def test_store_node_failure_raises_warning_alert():
    store = MonitoringStore()
    _store_record(store, failed=True)
    alerts = store.list_alerts()
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.rule_id == "node_failed" and alert.severity == "warning"
    assert alert.status == "open" and alert.count == 1 and alert.last_run_id == "run-1"


def test_store_error_run_raises_run_error_alert():
    store = MonitoringStore()
    _store_record(store, status="error", error="ValueError: x")
    alerts = store.list_alerts()
    assert alerts[0].rule_id == "run_error" and alerts[0].severity == "critical"


def test_store_streak_merge_and_healthy_reset():
    store = MonitoringStore()
    for _ in range(3):
        _store_record(store, failed=True)
    consecutive = [a for a in store.list_alerts() if a.rule_id == "consecutive_failures"]
    assert len(consecutive) == 1
    assert consecutive[0].count == 1 and consecutive[0].last_run_id == "run-3"
    # 第四、五次失败：连续规则与节点失败规则均合并计数
    _store_record(store, failed=True)
    _store_record(store, failed=True)
    alerts = {a.rule_id: a for a in store.list_alerts()}
    assert alerts["consecutive_failures"].count == 3
    assert alerts["node_failed"].count == 5
    # 健康运行重置 streak：再来 threshold-1 次失败不发新连续告警
    _store_record(store)
    _store_record(store, failed=True)
    _store_record(store, failed=True)
    assert len([a for a in store.list_alerts() if a.rule_id == "consecutive_failures"]) == 1


def test_store_resolved_alert_allows_new_alert_but_acknowledged_merges():
    store = MonitoringStore()
    _store_record(store, status="error", error="x")
    alert = store.list_alerts()[0]
    assert store.acknowledge_alert(alert.id).status == "acknowledged"
    _store_record(store, status="error", error="x")
    same = store.list_alerts()[0]
    assert same.id == alert.id and same.count == 2 and same.status == "acknowledged"
    assert store.resolve_alert(alert.id).status == "resolved"
    _store_record(store, status="error", error="x")
    open_run_errors = [a for a in store.list_alerts(status="open") if a.rule_id == "run_error"]
    assert len(open_run_errors) == 1 and open_run_errors[0].id != alert.id


def test_alert_lifecycle_unknown_and_illegal_transitions():
    store = MonitoringStore()
    _store_record(store, status="error", error="x")
    alert = store.list_alerts()[0]
    assert store.acknowledge_alert("alt-missing") is None
    assert store.acknowledge_alert(alert.id) is not False
    assert store.acknowledge_alert(alert.id) is False  # 非 open
    assert store.resolve_alert("alt-missing") is None
    assert store.resolve_alert(alert.id) is not False
    assert store.resolve_alert(alert.id) is False  # 已 resolved


def test_ring_eviction():
    store = MonitoringStore()
    for _ in range(RUN_RING_SIZE + 5):
        _store_record(store)
    runs = store.list_runs(limit=RUN_RING_SIZE)
    assert len(runs) == RUN_RING_SIZE
    assert runs[0].id == f"run-{RUN_RING_SIZE + 5}"


def test_list_runs_filters_and_limits():
    store = MonitoringStore()
    _store_record(store, graph_id="g1")
    _store_record(store, graph_id="g2")
    _store_record(store, graph_id="g1")
    g1 = store.list_runs(graph_id="g1")
    assert [r.id for r in g1] == ["run-3", "run-1"]
    assert [r.id for r in store.list_runs(limit=2)] == ["run-3", "run-2"]


def test_rules_get_update_validate_and_reset_defaults():
    store = MonitoringStore()
    updated = store.update_rules({
        "run_error": {"enabled": True},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 2},
        "failure_rate": {"enabled": True, "window": 10, "min_samples": 3, "rate": 0.8},
    })
    assert updated.consecutive_failures.threshold == 2
    assert not store.get_rules().node_failed.enabled
    # 关闭节点失败规则后不再产生该告警
    _store_record(store, failed=True)
    assert not [a for a in store.list_alerts() if a.rule_id == "node_failed"]

    with pytest.raises(ValueError, match="threshold"):
        store.update_rules({
            "run_error": {"enabled": "yes"},
            "node_failed": {"enabled": False},
            "consecutive_failures": {"enabled": True, "threshold": 0},
            "failure_rate": {"enabled": True, "window": 10, "min_samples": 3, "rate": 2},
        })
    store.reset()
    rules = store.get_rules()
    assert rules.node_failed.enabled and rules.consecutive_failures.threshold == 3
    assert store.list_runs() == [] and store.list_alerts() == []


def test_validate_rules_messages_and_from_raw():
    errors = validate_rules({
        "run_error": {"enabled": "yes"},
        "node_failed": {"enabled": False},
        "consecutive_failures": {"enabled": True, "threshold": 0},
        "failure_rate": {"enabled": True, "window": 10, "min_samples": 3, "rate": 2},
    })
    joined = "；".join(errors)
    assert "run_error.enabled" in joined
    assert "threshold" in joined
    assert "rate" in joined
    assert validate_rules("not-a-dict")
    raw = {
        "run_error": {"enabled": True},
        "node_failed": {"enabled": True},
        "consecutive_failures": {"enabled": True, "threshold": 2},
        "failure_rate": {"enabled": False, "window": 20, "min_samples": 5, "rate": 0.5},
    }
    assert validate_rules(raw) == []
    rules = rules_from_raw(raw)
    assert rules.failure_rate.rate == 0.5 and not rules.failure_rate.enabled

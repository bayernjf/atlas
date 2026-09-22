"""DSL → LangGraph 编译与运行测试（docs/08 §7.1 W7-W8）。"""

from __future__ import annotations

import json

import httpx
import pytest

from atlas.graph.dsl import GraphDSL, GraphValidationError, NodeDSL, parse_graph
from atlas.graph.loader import (
    _execute_tool,
    build_demo_registry,
    compile_graph,
    interpolate,
    resolve_path,
    run_graph,
)
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.httpapi.service import HttpApiClient
from atlas.shop.adapter import ShopHarnessAdapter


def _sample_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "company_name", "type": "string", "value": "Atlas", "scope": "global"},
                {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"},
            ],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"}},
                {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
                 "config": {"promptTemplate": "公司 {{global.company_name}} 限额 {{global.approval_limit}} 缺失 {{trigger-1.context.payload.missing}}",
                            "confidenceThreshold": 0.6, "model": "demo"}},
                {"id": "tool_call-1", "type": "tool_call", "name": "工具",
                 "config": {"tool": "web-playwright/click",
                            "params": "依据 {{ai_decision-1.decision}} 执行"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
                {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
            ],
        }
    )


def test_interpolate_resolves_dotted_paths_and_preserves_missing():
    assert interpolate("公司 {{global.company_name}}", {"global": {"company_name": "Atlas"}}) == "公司 Atlas"
    template = "限额 {{global.missing}} 与 {{order.items[0].price}}"
    context = {"order": {"items": [{"price": 299}]}}
    assert interpolate(template, context) == "限额 {{global.missing}} 与 299"


def test_resolve_path_bracket_and_missing_segments():
    assert resolve_path("order.items[1].price", {"order": {"items": [{"price": 1}, {"price": 2}]}}) == 2
    assert resolve_path("order.items[9].price", {"order": {"items": []}}) is None
    assert resolve_path("x.y", {"x": 1}) is None


def test_compile_produces_graph_and_runs_in_edge_order():
    result = run_graph(_sample_graph())
    assert result["status"] == "completed"
    assert list(result["outputs"].keys()) == ["trigger-1", "ai_decision-1", "tool_call-1"]
    assert result["outputs"]["trigger-1"]["context"]["webhookUrl"] == "/hooks/approval"
    decision = result["outputs"]["ai_decision-1"]
    assert decision["decision"]["action"] == "request_human_approval"
    assert decision["decision"]["source"] == "rule"
    assert "公司 Atlas 限额 500 缺失 {{trigger-1.context.payload.missing}}" == decision["prompt_rendered"]
    # 未在 Demo 注册表中的适配器 → 结构化失败，不抛异常
    tool_output = result["outputs"]["tool_call-1"]["result"]
    assert tool_output["status"] == "FAILED"
    assert "web-playwright" in tool_output["error"]
    assert result["trace"][0].startswith("trigger-1")


def test_compiled_graph_has_start_and_end_wiring():
    compiled = compile_graph(_sample_graph())
    node_ids = set(compiled.get_graph().nodes)
    assert {"trigger-1", "ai_decision-1", "tool_call-1"}.issubset(node_ids)


def test_run_with_runtime_inputs_overrides_global():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [{"name": "limit", "type": "number", "value": "100", "scope": "global"}],
            "nodes": [
                {"id": "ai_decision-1", "type": "ai_decision", "name": "d",
                 "config": {"promptTemplate": "限额 {{global.limit}}"}},
            ],
            "edges": [],
        }
    )
    result = run_graph(graph, inputs={"limit": "999"})
    assert result["outputs"]["ai_decision-1"]["prompt_rendered"] == "限额 999"


def _condition_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "退款单进入",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "condition-1", "type": "condition", "name": "金额路由",
                 "config": {
                     "branches": [
                         {"label": "大额",
                          "expression": "{{trigger-1.context.payload.amount}} > 1000",
                          "target": "tool-human"},
                     ],
                     "defaultTarget": "tool-auto",
                 }},
                {"id": "tool-human", "type": "tool_call", "name": "转人工",
                 "config": {"tool": "human-review"}},
                {"id": "tool-auto", "type": "tool_call", "name": "自动退款",
                 "config": {"tool": "auto-refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-human"},
                {"id": "e3", "source": "condition-1", "target": "tool-auto"},
            ],
        }
    )


def test_condition_routes_to_matching_branch_only():
    result = run_graph(_condition_graph(), inputs={"amount": 1500, "order_id": "12346"})
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-human"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "大额"
    assert routed["target"] == "tool-human"
    assert routed["evaluation"][0]["result"] is True
    assert routed["expression_errors"] == []
    assert result["trace"][1] == "condition-1: branch=大额 → tool-human"


def test_condition_falls_back_to_default_branch():
    result = run_graph(_condition_graph(), inputs={"amount": 500, "order_id": "12345"})
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-auto"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "__default__"
    assert routed["target"] == "tool-auto"
    assert routed["evaluation"][0]["result"] is False


def test_condition_runtime_error_fails_safe_to_default():
    result = run_graph(_condition_graph(), inputs={"order_id": "x"})  # 缺 amount
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-auto"}
    routed = result["outputs"]["condition-1"]
    assert routed["branch"] == "__default__"
    assert routed["evaluation"][0]["result"] is None
    assert routed["expression_errors"]


def test_condition_stops_at_first_true_branch():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "condition-1", "type": "condition", "name": "c",
                 "config": {
                     "branches": [
                         {"label": "first", "expression": "true", "target": "tool-a"},
                         {"label": "broken", "expression": "{{trigger-1.context.payload.missing}} > 1", "target": "tool-b"},
                     ],
                     "defaultTarget": "tool-c",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A", "config": {"tool": "a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B", "config": {"tool": "b"}},
                {"id": "tool-c", "type": "tool_call", "name": "C", "config": {"tool": "c"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-a"},
                {"id": "e3", "source": "condition-1", "target": "tool-b"},
                {"id": "e4", "source": "condition-1", "target": "tool-c"},
            ],
        }
    )
    result = run_graph(graph)
    assert set(result["outputs"].keys()) == {"trigger-1", "condition-1", "tool-a"}
    assert result["outputs"]["condition-1"]["branch"] == "first"
    assert result["outputs"]["condition-1"]["expression_errors"] == []


def _loop_graph(expression: str, max_iterations: int = 10):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "重试循环",
                 "config": {
                     "mode": "while",
                     "continueExpression": expression,
                     "maxIterations": max_iterations,
                     "bodyTarget": "tool-body",
                     "exitTarget": "tool-exit",
                 }},
                {"id": "tool-body", "type": "tool_call", "name": "循环体",
                 "config": {"tool": "body-op"}},
                {"id": "tool-exit", "type": "tool_call", "name": "退出",
                 "config": {"tool": "exit-op"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "tool-body"},
                {"id": "e3", "source": "tool-body", "target": "loop-1"},
                {"id": "e4", "source": "loop-1", "target": "tool-exit"},
            ],
        }
    )


def test_loop_runs_body_until_condition_false():
    result = run_graph(_loop_graph("{{loop-1.index}} < 3"))
    assert result["status"] == "completed"
    body_runs = sum(1 for line in result["trace"] if line.startswith("tool-body"))
    assert body_runs == 3
    assert set(result["outputs"].keys()) == {"trigger-1", "loop-1", "tool-body", "tool-exit"}
    loop_output = result["outputs"]["loop-1"]
    assert loop_output["iterations"] == 3
    assert loop_output["index"] == 3
    assert loop_output["exitReason"] == "condition_false"
    assert loop_output["target"] == "tool-exit"
    assert any("continue (3/10) → tool-body" in line for line in result["trace"])
    assert any("exit (condition_false) after 3 → tool-exit" in line for line in result["trace"])


def test_loop_fail_safe_exit_at_max_iterations():
    result = run_graph(_loop_graph("true", max_iterations=3))
    body_runs = sum(1 for line in result["trace"] if line.startswith("tool-body"))
    assert body_runs == 3
    loop_output = result["outputs"]["loop-1"]
    assert loop_output["exitReason"] == "max_iterations"
    assert loop_output["target"] == "tool-exit"
    assert loop_output["expression_errors"]
    assert "tool-exit" in result["outputs"]


def test_loop_expression_error_exits_immediately():
    result = run_graph(_loop_graph("{{trigger-1.context.payload.missing}} > 1"))
    assert "tool-body" not in result["outputs"]
    assert set(result["outputs"].keys()) == {"trigger-1", "loop-1", "tool-exit"}
    loop_output = result["outputs"]["loop-1"]
    assert loop_output["iterations"] == 0
    assert loop_output["exitReason"] == "expression_error"
    assert loop_output["expression_errors"]


def _parallel_graph(strategy: str = "all_success", b_tool: str = "op-b"):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "parallel-1", "type": "parallel", "name": "并行",
                 "config": {
                     "joinStrategy": strategy,
                     "branches": [
                         {"label": "短支", "target": "tool-a"},
                         {"label": "长支", "target": "tool-b"},
                     ],
                     "joinTarget": "tool-join",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": "op-a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": b_tool}},
                {"id": "tool-mid", "type": "tool_call", "name": "长支中段",
                 "config": {"tool": "op-mid"}},
                {"id": "tool-join", "type": "tool_call", "name": "汇聚",
                 "config": {"tool": "op-join", "params": "状态={{parallel-1.status}}"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "parallel-1"},
                {"id": "e2", "source": "parallel-1", "target": "tool-a"},
                {"id": "e3", "source": "parallel-1", "target": "tool-b"},
                {"id": "e4", "source": "tool-a", "target": "tool-join"},
                {"id": "e5", "source": "tool-b", "target": "tool-mid"},
                {"id": "e6", "source": "tool-mid", "target": "tool-join"},
            ],
        }
    )


def test_parallel_fan_out_runs_all_branches_and_joins_once():
    events: list[dict] = []
    result = run_graph(_parallel_graph(), emit=events.append)
    assert result["status"] == "completed"

    starts = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert {node: starts.count(node) for node in set(starts)} == {
        "trigger-1": 1,
        "parallel-1": 1,
        "tool-a": 1,
        "tool-b": 1,
        "tool-mid": 1,
        "tool-join": 1,
    }
    assert not any(node.startswith("__join__") for node in starts)

    # 汇聚网关在聚合完成时以 parallel 节点自身补发一次 node_end（fork 时为 running）
    parallel_ends = [
        event for event in events
        if event["type"] == "node_end" and event["node_id"] == "parallel-1"
    ]
    assert len(parallel_ends) == 2
    assert [event["output"]["status"] for event in parallel_ends] == ["running", "success"]
    assert not any(
        event.get("node_id", "").startswith("__join__") for event in events
    )

    parallel_output = result["outputs"]["parallel-1"]
    assert parallel_output["mode"] == "parallel"
    assert parallel_output["status"] == "success"
    assert set(parallel_output["result"]) == {"tool-a", "tool-b"}
    assert {branch["label"] for branch in parallel_output["branches"]} == {"短支", "长支"}
    assert result["outputs"]["tool-join"]["params_rendered"] == "状态=success"
    assert any("fork 2 branches → tool-a, tool-b" in line for line in result["trace"])
    assert any("joined (all_success) success" in line for line in result["trace"])


def test_parallel_all_success_fail_safe_join_on_failed_branch():
    events: list[dict] = []
    result = run_graph(_parallel_graph(b_tool="bogus/x"), emit=events.append)
    assert result["status"] == "completed"

    starts = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert starts.count("tool-join") == 1

    parallel_output = result["outputs"]["parallel-1"]
    assert parallel_output["status"] == "failed"
    failed = {branch["target"]: branch for branch in parallel_output["branches"]
              if branch["status"] == "failed"}
    assert set(failed) == {"tool-b"}
    assert "适配器未注册" in failed["tool-b"]["error"]
    assert result["outputs"]["tool-join"]["params_rendered"] == "状态=failed"
    assert any("joined (all_success) failed: 长支（适配器未注册：bogus）" in line
               for line in result["trace"])


def test_parallel_all_completed_marks_success_despite_failed_branch():
    result = run_graph(_parallel_graph(strategy="all_completed", b_tool="bogus/x"))
    parallel_output = result["outputs"]["parallel-1"]
    assert parallel_output["status"] == "success"
    assert parallel_output["joinStrategy"] == "all_completed"
    assert any(branch["status"] == "failed" for branch in parallel_output["branches"])
    assert "tool-join" in result["outputs"]
    assert any("joined (all_completed) success" in line for line in result["trace"])


def _wait_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待 2 秒",
                 "config": {"waitType": "duration", "durationSeconds": 2}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )


def test_wait_sleeps_then_continues_to_single_successor(monkeypatch):
    slept: list[int] = []
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: slept.append(seconds))

    result = run_graph(_wait_graph())
    assert result["status"] == "completed"
    assert slept == [2]

    wait_output = result["outputs"]["wait-1"]
    assert wait_output == {"mode": "wait", "waitType": "duration", "durationSeconds": 2}
    assert "tool-after" in result["outputs"]
    assert any("waited 2s" in line for line in result["trace"])


def test_wait_after_condition_default_branch_passes_through(monkeypatch):
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: None)
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "condition-1", "type": "condition", "name": "c",
                 "config": {
                     "branches": [
                         {"label": "大额", "expression": "1 == 2", "target": "tool-a"},
                     ],
                     "defaultTarget": "wait-1",
                 }},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": "op-a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": "op-b"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-a"},
                {"id": "e3", "source": "condition-1", "target": "wait-1"},
                {"id": "e4", "source": "wait-1", "target": "tool-b"},
            ],
        }
    )
    result = run_graph(graph)
    assert set(result["outputs"]) == {"trigger-1", "condition-1", "wait-1", "tool-b"}
    assert result["outputs"]["wait-1"]["mode"] == "wait"


def _human_graph(**config_overrides):
    config = {
        "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
        "approver": "客服主管",
        "timeoutSeconds": 300,
        "onTimeout": "reject",
        "approvedTarget": "tool-approve",
        "rejectedTarget": "tool-reject",
    }
    config.update(config_overrides)
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "human-1", "type": "human_approval", "name": "人工审批",
                 "config": config},
                {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
                 "config": {"tool": "op-approve"}},
                {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
                 "config": {"tool": "op-reject"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "human-1"},
                {"id": "e2", "source": "human-1", "target": "tool-approve"},
                {"id": "e3", "source": "human-1", "target": "tool-reject"},
            ],
        }
    )


def test_human_approval_preset_approved_runs_only_approved_branch():
    graph = _human_graph()
    result = run_graph(
        graph,
        inputs={"order_id": "12345", "approvals": {"human-1": "approved"}},
    )
    assert result["status"] == "completed"
    assert "tool-approve" in result["outputs"]
    assert "tool-reject" not in result["outputs"]

    output = result["outputs"]["human-1"]
    assert output["mode"] == "human_approval"
    assert output["decision"] == "approved"
    assert output["target"] == "tool-approve"
    assert output["resolvedBy"] == "input"
    assert output["summary"] == "订单 12345 退款审批"
    assert output["approver"] == "客服主管"
    assert len(output["token"]) == 32
    assert any("approved (input) → tool-approve" in line for line in result["trace"])


def test_human_approval_preset_rejected_runs_only_rejected_branch():
    result = run_graph(
        _human_graph(),
        inputs={"order_id": "12346", "approvals": {"human-1": "rejected"}},
    )
    assert "tool-reject" in result["outputs"]
    assert "tool-approve" not in result["outputs"]
    assert result["outputs"]["human-1"]["decision"] == "rejected"
    assert result["outputs"]["human-1"]["target"] == "tool-reject"


def test_human_approval_timeout_routes_by_on_timeout(monkeypatch):
    from atlas.collaboration.approvals import ApprovalBroker

    broker = ApprovalBroker()
    monkeypatch.setattr(broker, "wait", lambda token: None)  # 立即超时，无真实等待

    rejected = run_graph(
        _human_graph(),
        inputs={"order_id": "12347"},
        approval_broker=broker,
    )
    assert rejected["outputs"]["human-1"]["decision"] == "rejected"
    assert rejected["outputs"]["human-1"]["resolvedBy"] == "timeout"
    assert "tool-reject" in rejected["outputs"]
    assert "tool-approve" not in rejected["outputs"]

    broker2 = ApprovalBroker()
    monkeypatch.setattr(broker2, "wait", lambda token: None)
    approved = run_graph(
        _human_graph(onTimeout="approve"),
        inputs={"order_id": "12348"},
        approval_broker=broker2,
    )
    assert approved["outputs"]["human-1"]["decision"] == "approved"
    assert approved["outputs"]["human-1"]["resolvedBy"] == "timeout"
    assert "tool-approve" in approved["outputs"]


def test_human_approval_resolved_from_other_thread_routes_approved():
    import threading
    import time as time_mod

    from atlas.collaboration.approvals import ApprovalBroker

    broker = ApprovalBroker()
    captured: dict = {}

    def emit(event):
        if event.get("type") == "node_start" and event.get("node_id") == "human-1":
            captured["approval"] = event["approval"]

    def decide_later():
        # 轮询等待 node_start 投递 approval（原固定 sleep 0.05 在调度抖动下
        # 可能早于事件触发，线程 KeyError 后用例等满 300s 超时——时序 flaky）。
        deadline = time_mod.monotonic() + 2
        while "approval" not in captured and time_mod.monotonic() < deadline:
            time_mod.sleep(0.005)
        token = captured["approval"]["token"]
        assert broker.resolve(token, "approved", comment="同意退款") is True

    thread = threading.Thread(target=decide_later)
    thread.start()
    result = run_graph(
        _human_graph(),
        inputs={"order_id": "12349"},
        approval_broker=broker,
        emit=emit,
    )
    thread.join(timeout=2)
    assert result["outputs"]["human-1"]["resolvedBy"] == "human"
    assert result["outputs"]["human-1"]["decision"] == "approved"
    assert "tool-approve" in result["outputs"]
    assert captured["approval"]["timeoutSeconds"] == 300
    assert broker.list_pending() == []


def test_human_approval_node_start_carries_approval_payload_before_continuation():
    events = []
    run_graph(
        _human_graph(),
        inputs={"order_id": "12350", "approvals": {"human-1": "rejected"}},
        emit=events.append,
    )
    starts = [e for e in events if e["type"] == "node_start"]
    human_start = next(e for e in starts if e["node_id"] == "human-1")
    # docs/35 §2（T2）：approval 载荷新增 notified（未配 notifyEmails/未注入 notifier 时 False）
    assert set(human_start["approval"]) == {
        "token", "summary", "approver", "timeoutSeconds", "notified",
    }
    assert human_start["approval"]["notified"] is False
    human_index = starts.index(human_start)
    reject_start = next(e for e in starts if e["node_id"] == "tool-reject")
    assert human_index < starts.index(reject_start)


# ---------- subgraph（04 §5.7，U23/U24） ----------

def _child_graph(child_id: str = "graph-child"):
    return child_id, parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "child-trigger", "type": "trigger", "name": "子图触发",
                 "config": {"triggerType": "manual"}},
                {"id": "child-tool", "type": "tool_call", "name": "子图工具",
                 "config": {"tool": "op-child"}},
            ],
            "edges": [
                {"id": "ce1", "source": "child-trigger", "target": "child-tool"},
            ],
        }
    )


def _parent_subgraph_graph(child_id: str = "graph-child"):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {
                     "graphId": child_id,
                     "inputs": {"order_id": "{{trigger-1.context.payload.order_id}}"},
                 }},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {
                     "tool": "op-after",
                     "params": "单号 {{subgraph-1.outputs.child-trigger.context.payload.order_id}}",
                 }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )


def test_subgraph_runs_child_with_mapped_inputs_and_exposes_outputs():
    child_id, child = _child_graph()
    store = {child_id: child}
    events: list[dict] = []

    result = run_graph(
        _parent_subgraph_graph(child_id),
        inputs={"order_id": "X-1", "amount": 1},
        graph_id="graph-parent",
        graph_resolver=store.get,
        emit=events.append,
    )

    assert result["status"] == "completed"
    node = result["outputs"]["subgraph-1"]
    assert node["mode"] == "subgraph"
    assert node["graphId"] == child_id
    assert node["status"] == "success"
    # ① 映射入参到达子图 trigger context.payload
    child_outputs = node["outputs"]
    assert set(child_outputs) == {"child-trigger", "child-tool"}
    assert child_outputs["child-trigger"]["context"]["payload"] == {"order_id": "X-1"}
    # ② 父图后继经 subgraph-x.outputs.<子节点>.<路径> 引用子图产出，单出边后继恰好执行
    after = result["outputs"]["tool-after"]
    assert after["params_rendered"] == "单号 X-1"
    assert any(f"{child_id} success (2 nodes)" in line for line in result["trace"])
    # ④ A 包（docs/27 §3）：子图内部节点事件现在上屏，但带 subgraphPath 命名空间；
    #    只转发 node_start/node_end，子层 run_end 等终帧仍吞掉。
    child_events = [
        event for event in events
        if str(event.get("node_id", "")).startswith("child-")
    ]
    assert child_events, "子图内部节点事件应上屏"
    assert all(event.get("subgraphPath") == ["subgraph-1"] for event in child_events)
    assert all(event["type"] in ("node_start", "node_end") for event in child_events)
    # 顶层 node_start 集合不变（child-* 带命名空间，不计入顶层）
    parent_ids = {
        event["node_id"]
        for event in events
        if event["type"] == "node_start" and not event.get("subgraphPath")
    }
    assert parent_ids == {"trigger-1", "subgraph-1", "tool-after"}
    # 整图只有一个顶层 run_end（子层终帧被吞）
    assert len([event for event in events if event["type"] == "run_end"]) == 1


class _BoomDecisionClient:
    def decide_refund(self, **kwargs):
        raise RuntimeError("boom")


def test_subgraph_child_runtime_error_fails_safe_but_parent_completes():
    child = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "child-trigger", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "child-ai", "type": "ai_decision", "name": "决策",
                 "config": {"promptTemplate": "x"}},
            ],
            "edges": [{"id": "ce1", "source": "child-trigger", "target": "child-ai"}],
        }
    )
    store = {"graph-boom": child}
    result = run_graph(
        _parent_subgraph_graph("graph-boom"),
        inputs={"order_id": "X-2"},
        graph_id="graph-parent",
        graph_resolver=store.get,
        decision_client=_BoomDecisionClient(),
    )
    node = result["outputs"]["subgraph-1"]
    assert node["status"] == "failed"
    assert "boom" in node["error"]
    assert node["outputs"] == {}
    # fail-safe：父 run 仍 completed，唯一后继照常执行
    assert result["status"] == "completed"
    assert "tool-after" in result["outputs"]
    assert any("graph-boom failed: boom" in line for line in result["trace"])


def test_subgraph_inside_loop_body_runs_each_iteration():
    child_id, child = _child_graph()
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "重试循环",
                 "config": {"mode": "while", "continueExpression": "{{loop-1.index}} < 2",
                            "maxIterations": 3, "bodyTarget": "subgraph-1",
                            "exitTarget": "tool-exit"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": child_id, "inputs": {}}},
                {"id": "tool-body", "type": "tool_call", "name": "循环体",
                 "config": {"tool": "op-body"}},
                {"id": "tool-exit", "type": "tool_call", "name": "退出",
                 "config": {"tool": "op-exit"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "subgraph-1"},
                {"id": "e3", "source": "subgraph-1", "target": "tool-body"},
                {"id": "e4", "source": "tool-body", "target": "loop-1"},
                {"id": "e5", "source": "loop-1", "target": "tool-exit"},
            ],
        }
    )
    result = run_graph(
        graph, graph_id="graph-parent", graph_resolver={child_id: child}.get
    )
    assert result["status"] == "completed"
    assert result["outputs"]["subgraph-1"]["status"] == "success"
    assert sum(child_id in line and "success" in line for line in result["trace"]) == 2


def test_subgraph_compile_rejects_unresolvable_reference():
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(_parent_subgraph_graph("graph-missing"),
                      graph_id="graph-parent", graph_resolver={}.get)
    assert any("引用的子图不存在：graph-missing" in e for e in exc.value.errors)


def test_subgraph_outputs_unknown_inner_node_rejected_at_compile_d30_b2():
    child_id, child = _child_graph()  # 内部节点：child-trigger / child-tool
    store = {child_id: child}

    def parent(template: str):
        return parse_graph(
            {
                "version": 1,
                "variables": [],
                "nodes": [
                    {"id": "trigger-1", "type": "trigger", "name": "t",
                     "config": {"triggerType": "manual"}},
                    {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                     "config": {"graphId": child_id, "inputs": {}}},
                    {"id": "ai-1", "type": "ai_decision", "name": "后继",
                     "config": {"promptTemplate": template, "model": "demo"}},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
                    {"id": "e2", "source": "subgraph-1", "target": "ai-1"},
                ],
            }
        )

    # 合法内部节点 id 的深层路径：编译通过
    compile_graph(parent("{{subgraph-1.outputs.child-tool.x}}"),
                  graph_id="graph-parent", graph_resolver=store.get)
    # 不存在的内部节点 id：编译期 REF_PATH_NOT_FOUND
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(parent("{{subgraph-1.outputs.ghost.x}}"),
                      graph_id="graph-parent", graph_resolver=store.get)
    assert any("子图输出中不存在" in e and "ghost" in e for e in exc.value.errors)


def test_subgraph_compile_rejects_without_resolver():
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(_parent_subgraph_graph(), graph_id="graph-parent")
    assert any("子图解析器未注入" in e for e in exc.value.errors)


def test_subgraph_compile_rejects_direct_self_reference():
    graph = _parent_subgraph_graph("graph-self")
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(graph, graph_id="graph-self", graph_resolver={"graph-self": graph}.get)
    assert any("不能直接引用自身" in e for e in exc.value.errors)


def test_subgraph_compile_rejects_cross_graph_cycle():
    # A(parent)→B→A：B 内含 subgraph 引用 graph-a
    _, child_b = _child_graph("graph-b")
    graph_b = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "b-trigger", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "b-sub", "type": "subgraph", "name": "引用A",
                 "config": {"graphId": "graph-a", "inputs": {}}},
                {"id": "b-tool", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-b"}},
            ],
            "edges": [
                {"id": "be1", "source": "b-trigger", "target": "b-sub"},
                {"id": "be2", "source": "b-sub", "target": "b-tool"},
            ],
        }
    )
    graph_a = _parent_subgraph_graph("graph-b")
    store = {"graph-a": graph_a, "graph-b": graph_b}
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(graph_a, graph_id="graph-a", graph_resolver=store.get)
    assert any("跨图引用环" in e and "graph-a → graph-b → graph-a" in e for e in exc.value.errors)


def _chain_graph(graph_id: str, ref: str | None):
    nodes = [
        {"id": f"{graph_id}-trigger", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
    ]
    edges: list[dict] = []
    if ref is not None:
        nodes.append({"id": f"{graph_id}-sub", "type": "subgraph", "name": "子",
                      "config": {"graphId": ref, "inputs": {}}})
        nodes.append({"id": f"{graph_id}-after", "type": "tool_call", "name": "后继",
                      "config": {"tool": "op"}})
        edges = [
            {"id": f"{graph_id}-e1", "source": f"{graph_id}-trigger", "target": f"{graph_id}-sub"},
            {"id": f"{graph_id}-e2", "source": f"{graph_id}-sub", "target": f"{graph_id}-after"},
        ]
    return parse_graph({"version": 1, "variables": [], "nodes": nodes, "edges": edges})


def test_subgraph_depth_three_allowed_four_rejected():
    # g0→g1→g2→g3：深度恰好 3 合法
    store = {
        "g1": _chain_graph("g1", "g2"),
        "g2": _chain_graph("g2", "g3"),
        "g3": _chain_graph("g3", None),
    }
    compile_graph(_chain_graph("g0", "g1"), graph_id="g0", graph_resolver=store.get)

    # 再加一层 g3→g4：g0 编译时在深度 3 的节点引用 g4 被拒
    store["g3"] = _chain_graph("g3", "g4")
    store["g4"] = _chain_graph("g4", None)
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(_chain_graph("g0", "g1"), graph_id="g0", graph_resolver=store.get)
    assert any("嵌套深度超过上限 3" in e for e in exc.value.errors)


def test_subgraph_compile_aggregates_child_validation_errors():
    # 手工构造 GraphDSL 级非法图（缺出边的 wait；parse 阶段无法生成）
    raw_invalid = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "child-trigger", "type": "trigger", "name": "t",
             "position": {"x": 0, "y": 0}, "config": {"triggerType": "manual"}},
            {"id": "child-wait", "type": "wait", "name": "等待",
             "position": {"x": 1, "y": 0},
             "config": {"waitType": "duration", "durationSeconds": 2}},
        ],
        "edges": [],
    }
    bad_child = GraphDSL.model_validate(raw_invalid)
    store = {"graph-bad-child": bad_child}
    with pytest.raises(GraphValidationError) as exc:
        compile_graph(
            _parent_subgraph_graph("graph-bad-child"),
            graph_id="graph-parent",
            graph_resolver=store.get,
        )
    assert any(e.startswith("子图 graph-bad-child：") for e in exc.value.errors)


def _http_registry(handler):
    transport = httpx.MockTransport(handler)
    # 注入假 resolver（域名解析到公网 IP），使 SSRF egress 校验离线确定、零真实 DNS
    client = HttpApiClient(
        base_url="http://demo.test",
        client=httpx.Client(transport=transport),
        resolver=lambda host: ["93.184.216.34"],
    )
    registry = AdapterRegistry()
    registry.register(HttpApiHarnessAdapter(client=client, granted_permissions={"write"}))
    return registry


def test_http_tool_params_json_assembled_and_interpolated():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["x-order"] = request.headers.get("x-order")
        return httpx.Response(200, json=[{"id": "o-1"}], request=request)

    node = NodeDSL(
        id="tool-x",
        type="tool_call",
        name="HTTP",
        config={
            "tool": "http/request",
            "params": json.dumps(
                {
                    "url": "{{trigger-1.context.payload.path}}",
                    "headers": {"X-Order": "{{trigger-1.context.payload.order_id}}"},
                }
            ),
        },
    )
    context = {"trigger-1": {"context": {"payload": {"path": "/orders", "order_id": "o-1"}}}}

    output = _execute_tool(node, context, _http_registry(handler))

    assert output["action_status"] == "SUCCESS"
    assert output["result"]["status"] == 200
    assert output["result"]["body"] == [{"id": "o-1"}]
    assert seen["url"] == "http://demo.test/orders"
    assert seen["x-order"] == "o-1"


def test_http_tool_bad_json_params_is_failed_invalid_parameter():
    node = NodeDSL(
        id="tool-x",
        type="tool_call",
        name="HTTP",
        config={"tool": "http/request", "params": '{"url": '},
    )

    output = _execute_tool(node, {}, _http_registry(lambda r: httpx.Response(200)))

    assert output["action_status"] == "FAILED"
    assert output["result"]["code"] == "INVALID_PARAMETER"


def test_http_tool_non_object_params_is_failed_invalid_parameter():
    node = NodeDSL(
        id="tool-x",
        type="tool_call",
        name="HTTP",
        config={"tool": "http/request", "params": "[1, 2]"},
    )

    output = _execute_tool(node, {}, _http_registry(lambda r: httpx.Response(200)))

    assert output["action_status"] == "FAILED"
    assert output["result"]["code"] == "INVALID_PARAMETER"


def test_shop_dispatch_and_simulated_path_not_regressed():
    registry = AdapterRegistry()
    registry.register(ShopHarnessAdapter(granted_permissions={"read", "write", "financial"}))
    login_node = NodeDSL(
        id="tool-login",
        type="tool_call",
        name="login",
        config={"tool": "shop/login", "params": "ignored"},
    )

    output = _execute_tool(login_node, {}, registry)

    assert output["action_status"] == "SUCCESS"
    assert output["result"] == {"logged_in": True}

    simulated_node = NodeDSL(id="tool-x", type="tool_call", name="x", config={"tool": "web/click"})
    assert _execute_tool(simulated_node, {}, None)["result"]["status"] == "SIMULATED"


def test_build_demo_registry_contains_http_request():
    registry = build_demo_registry()

    assert [c.name for c in registry.get("http").list_capabilities()] == ["request"]


def test_database_tool_json_passthrough_with_interpolated_bound_params():
    registry = build_demo_registry()
    node = NodeDSL(
        id="tool-db",
        type="tool_call",
        name="DB",
        config={
            "tool": "database/query",
            "params": json.dumps(
                {
                    "sql": "SELECT order_id FROM orders WHERE amount > :min ORDER BY order_id",
                    "params": {"min": "{{trigger-1.context.payload.min_amount}}"},
                }
            ),
        },
    )
    context = {"trigger-1": {"context": {"payload": {"min_amount": 1000}}}}

    output = _execute_tool(node, context, registry)

    assert output["action_status"] == "SUCCESS"
    assert output["result"]["rows"] == [{"order_id": "12346"}]


def test_database_tool_bad_json_is_failed_invalid_parameter():
    node = NodeDSL(
        id="tool-db",
        type="tool_call",
        name="DB",
        config={"tool": "database/query", "params": "{not json"},
    )

    output = _execute_tool(node, {}, build_demo_registry())

    assert output["action_status"] == "FAILED"
    assert output["result"]["code"] == "INVALID_PARAMETER"


def test_message_tool_json_passthrough_records_message():
    registry = build_demo_registry()
    node = NodeDSL(
        id="tool-msg",
        type="tool_call",
        name="MSG",
        config={
            "tool": "message/send",
            "params": json.dumps(
                {
                    "channel": "email",
                    "to": ["ops@example.com"],
                    "subject": "订单 {{trigger-1.context.payload.order_id}}",
                    "body": "请处理",
                }
            ),
        },
    )
    context = {"trigger-1": {"context": {"payload": {"order_id": "12346"}}}}

    output = _execute_tool(node, context, registry)

    assert output["action_status"] == "SUCCESS"
    assert output["result"]["to"] == ["ops@example.com"]
    assert output["result"]["subject"] == "订单 12346"
    assert registry.get("message").service.count == 1


def test_build_demo_registry_contains_database_and_message():
    registry = build_demo_registry()

    assert [c.name for c in registry.get("database").list_capabilities()] == ["query", "execute"]
    assert [c.name for c in registry.get("message").list_capabilities()] == ["send"]


def _loop_break_graph():
    """D17/A2：体内 condition 在 index>=2 时走 break 分支直连 exitTarget，否则回边 continue。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "循环",
                 "config": {
                     "mode": "while",
                     "continueExpression": "true",
                     "maxIterations": 10,
                     "bodyTarget": "tool-body",
                     "exitTarget": "tool-exit",
                 }},
                {"id": "tool-body", "type": "tool_call", "name": "循环体",
                 "config": {"tool": "body-op"}},
                {"id": "condition-break", "type": "condition", "name": "中断判断",
                 "config": {
                     "branches": [
                         {"label": "stop", "expression": "{{loop-1.index}} >= 2",
                          "target": "tool-exit"}
                     ],
                     "defaultTarget": "loop-1",
                 }},
                {"id": "tool-exit", "type": "tool_call", "name": "退出",
                 "config": {"tool": "exit-op"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "tool-body"},
                {"id": "e3", "source": "loop-1", "target": "tool-exit"},
                {"id": "e4", "source": "tool-body", "target": "condition-break"},
                {"id": "e5", "source": "condition-break", "target": "loop-1"},
                {"id": "e6", "source": "condition-break", "target": "tool-exit"},
            ],
        }
    )


def test_loop_break_via_body_condition_exits_with_break_reason_d17_a2():
    result = run_graph(_loop_break_graph())
    assert result["status"] == "completed"
    body_runs = sum(1 for line in result["trace"] if line.startswith("tool-body"))
    assert body_runs == 2  # index 1 continue、index 2 break，未跑满 maxIterations
    loop_output = result["outputs"]["loop-1"]
    assert loop_output["exitReason"] == "break"
    assert loop_output["iterations"] == 2
    assert loop_output["target"] == "tool-exit"
    assert "tool-exit" in result["outputs"]
    # 合成 __break__ 网关不外泄为节点产出
    assert not any(key.startswith("__break__") for key in result["outputs"])
    assert set(result["outputs"].keys()) == {
        "trigger-1", "loop-1", "tool-body", "condition-break", "tool-exit"
    }
    assert any("exit (break) after 2 → tool-exit" in line for line in result["trace"])


def test_loop_break_graph_validates_d17_a2():
    # 含 condition break 出口的图应当通过 DSL 校验（旧规则会误判“循环体连到退出目标”）；
    # parse_graph 内部已跑完整静态校验，能成功构造即通过。
    assert _loop_break_graph() is not None


def test_loop_non_condition_body_node_cannot_reach_exit_target_d17_a2():
    raw = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "loop-1", "type": "loop", "name": "循环",
             "config": {"mode": "while", "continueExpression": "true",
                        "maxIterations": 3, "bodyTarget": "tool-body",
                        "exitTarget": "tool-exit"}},
            {"id": "tool-body", "type": "tool_call", "name": "循环体",
             "config": {"tool": "body-op"}},
            {"id": "tool-exit", "type": "tool_call", "name": "退出",
             "config": {"tool": "exit-op"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "loop-1"},
            {"id": "e2", "source": "loop-1", "target": "tool-body"},
            {"id": "e3", "source": "loop-1", "target": "tool-exit"},
            {"id": "e4", "source": "tool-body", "target": "loop-1"},
            # 非法：非 condition 的体内节点直连退出目标
            {"id": "e5", "source": "tool-body", "target": "tool-exit"},
        ],
    }
    # 非法逃逸在解析期静态校验即被拒。
    with pytest.raises(GraphValidationError) as excinfo:
        parse_graph(raw)
    assert any("不能直接连到退出目标" in msg for msg in excinfo.value.errors)


def _any_success_graph(a_tool: str = "op-a", b_tool: str = "op-b"):
    """D18/A1：短支 tool-a 单节点直连汇聚；长支 tool-b→tool-mid→tool-late 三节点。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "parallel-1", "type": "parallel", "name": "并行",
                 "config": {
                     "joinStrategy": "any_success",
                     "branches": [
                         {"label": "短支", "target": "tool-a"},
                         {"label": "长支", "target": "tool-b"},
                     ],
                     "joinTarget": "tool-join",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": a_tool}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": b_tool}},
                {"id": "tool-mid", "type": "tool_call", "name": "长支中段",
                 "config": {"tool": "op-mid"}},
                {"id": "tool-late", "type": "tool_call", "name": "长支末段",
                 "config": {"tool": "op-late"}},
                {"id": "tool-join", "type": "tool_call", "name": "汇聚",
                 "config": {"tool": "op-join", "params": "状态={{parallel-1.status}}"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "parallel-1"},
                {"id": "e2", "source": "parallel-1", "target": "tool-a"},
                {"id": "e3", "source": "parallel-1", "target": "tool-b"},
                {"id": "e4", "source": "tool-a", "target": "tool-join"},
                {"id": "e5", "source": "tool-b", "target": "tool-mid"},
                {"id": "e6", "source": "tool-mid", "target": "tool-late"},
                {"id": "e7", "source": "tool-late", "target": "tool-join"},
            ],
        }
    )


def test_parallel_any_success_short_circuits_unstarted_nodes_d18_a1():
    events: list[dict] = []
    result = run_graph(_any_success_graph(), emit=events.append)
    assert result["status"] == "completed"

    # 汇聚节点只执行一次（迟到的残留分支不重复放行）。
    starts = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert starts.count("tool-join") == 1

    po = result["outputs"]["parallel-1"]
    assert po["joinStrategy"] == "any_success"
    assert po["status"] == "success"
    by_target = {b["target"]: b["status"] for b in po["branches"]}
    assert by_target["tool-a"] == "success"
    assert by_target["tool-b"] == "skipped"

    # 已开始的节点（tool-b/tool-mid 与汇聚同超步前后启动）保留；未启动的 tool-late 被短路跳过。
    assert result["outputs"]["tool-late"].get("skipped") is True
    assert not result["outputs"]["tool-mid"].get("skipped")
    assert result["outputs"]["tool-join"]["params_rendered"] == "状态=success"
    assert any("joined (any_success) success" in line for line in result["trace"])


def test_parallel_any_success_failed_only_when_all_branches_fail_d18_a1():
    result = run_graph(_any_success_graph(a_tool="bogus/xa", b_tool="bogus/xb"))
    po = result["outputs"]["parallel-1"]
    assert po["status"] == "failed"
    assert {b["status"] for b in po["branches"]} == {"failed"}
    assert "tool-join" in result["outputs"]  # 汇聚节点仍执行（fail-safe）
    assert any("joined (any_success) failed" in line for line in result["trace"])


def test_parallel_any_success_succeeds_despite_one_failed_branch_d18_a1():
    result = run_graph(_any_success_graph(a_tool="op-a", b_tool="bogus/xb"))
    po = result["outputs"]["parallel-1"]
    assert po["status"] == "success"
    by_target = {b["target"]: b["status"] for b in po["branches"]}
    assert by_target["tool-a"] == "success"
    assert by_target["tool-b"] == "failed"

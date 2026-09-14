"""DSL → LangGraph 编译与运行测试（docs/08 §7.1 W7-W8）。"""

from __future__ import annotations

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import compile_graph, interpolate, resolve_path, run_graph


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
                 "config": {"promptTemplate": "公司 {{global.company_name}} 限额 {{global.approval_limit}} 缺失 {{global.missing}}",
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
    assert "公司 Atlas 限额 500 缺失 {{global.missing}}" == decision["prompt_rendered"]
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
                         {"label": "broken", "expression": "{{missing}} > 1", "target": "tool-b"},
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
    result = run_graph(_loop_graph("{{missing.path}} > 1"))
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
        time_mod.sleep(0.05)
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
    assert set(human_start["approval"]) == {"token", "summary", "approver", "timeoutSeconds"}
    human_index = starts.index(human_start)
    reject_start = next(e for e in starts if e["node_id"] == "tool-reject")
    assert human_index < starts.index(reject_start)

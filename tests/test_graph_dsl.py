"""Graph DSL 解析与静态校验测试（docs/04 §5.2 契约）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph


def make_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发", "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/approval"},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策", "position": {"x": 1, "y": 1},
             "config": {"promptTemplate": "限额 {{global.approval_limit}}", "confidenceThreshold": 0.6},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具", "position": {"x": 2, "y": 2},
             "config": {"tool": "web-playwright/click", "params": ""},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_graph_matches_serializer_shape():
    graph = parse_graph(make_graph())
    assert graph.version == 1
    assert [node.id for node in graph.nodes] == ["trigger-1", "ai_decision-1", "tool_call-1"]
    assert graph.variables[0].name == "approval_limit"
    assert graph.nodes[1].retry.on_error == "stop"


def test_reject_duplicate_node_ids():
    raw = make_graph()
    raw["nodes"][2]["id"] = "trigger-1"
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("节点 id 重复" in error for error in exc.value.errors)


def test_reject_edge_referencing_missing_node():
    raw = make_graph()
    raw["edges"][0]["target"] = "ghost"
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("target 节点不存在" in error for error in exc.value.errors)


def test_reject_unsupported_node_type_and_empty_name():
    raw = make_graph()
    raw["nodes"][1]["type"] = "parallel"
    raw["nodes"][1]["name"] = ""
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert len(exc.value.errors) >= 2


def test_reject_missing_required_config_per_kind():
    raw = make_graph()
    raw["nodes"][0]["config"] = {"triggerType": "cron"}
    raw["nodes"][1]["config"] = {"promptTemplate": "   "}
    raw["nodes"][2]["config"] = {"tool": ""}
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "Cron" in messages and "提示词" in messages and "工具" in messages


def test_schedule_trigger_type_uses_same_cron_rule_as_frontend():
    raw = make_graph()
    raw["nodes"][0]["config"] = {"triggerType": "schedule"}
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("Cron" in message for message in exc.value.errors)


def test_reject_unknown_version_and_empty_graph():
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(make_graph(version=2, nodes=[]))
    assert any("版本" in error for error in exc.value.errors)
    assert any("至少需要一个节点" in error for error in exc.value.errors)


def test_reject_self_loop_and_duplicate_variable():
    raw = make_graph()
    raw["edges"].append({"id": "e3", "source": "trigger-1", "target": "trigger-1"})
    raw["variables"].append({"name": "approval_limit", "type": "string", "value": "x"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "自环" in messages and "全局变量名重复" in messages


def _tool(node_id: str, name: str) -> dict:
    return {
        "id": node_id, "type": "tool_call", "name": name, "position": {"x": 0, "y": 0},
        "config": {"tool": "shop/process_refund", "params": ""},
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_condition_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "退款单进入", "position": {"x": 0, "y": 0},
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"},
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            {"id": "condition-1", "type": "condition", "name": "金额路由", "position": {"x": 1, "y": 0},
             "config": {
                 "branches": [
                     {"label": "大额", "expression": "{{trigger-1.context.payload.amount}} > 1000",
                      "target": "tool-human"},
                 ],
                 "defaultTarget": "tool-auto",
             },
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            _tool("tool-human", "转人工"),
            _tool("tool-auto", "自动退款"),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "condition-1"},
            {"id": "e2", "source": "condition-1", "target": "tool-human"},
            {"id": "e3", "source": "condition-1", "target": "tool-auto"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_condition_graph():
    graph = parse_graph(make_condition_graph())
    condition = next(node for node in graph.nodes if node.type == "condition")
    assert condition.config["defaultTarget"] == "tool-auto"


def test_reject_condition_missing_default_and_bad_expression():
    raw = make_condition_graph()
    raw["nodes"][1]["config"] = {
        "branches": [
            {"label": "", "expression": "amount >", "target": "tool-human"},
            {"label": "", "expression": "{{x}} == null", "target": "tool-auto"},
        ],
    }
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "默认分支" in messages and "分支名称" in messages and "语法错误" in messages


def test_reject_condition_target_without_edge_and_duplicate_target():
    raw = make_condition_graph()
    raw["nodes"][1]["config"]["branches"][0]["target"] = "tool-auto"  # 与 defaultTarget 相同
    raw["edges"][2]["target"] = "tool-human"  # default 目标 tool-auto 失去出边
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "不能与默认分支相同" in messages and "缺少到目标节点 tool-auto 的连线" in messages


def test_reject_uncovered_condition_out_edge():
    raw = make_condition_graph()
    raw["nodes"].append(_tool("tool-extra", "多余"))
    raw["edges"].append({"id": "e4", "source": "condition-1", "target": "tool-extra"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("未配置分支" in error and "tool-extra" in error for error in exc.value.errors)


def test_reject_unreachable_node():
    raw = make_condition_graph()
    raw["nodes"].append(_tool("tool-orphan", "孤立"))
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("tool-orphan 不可达" in error for error in exc.value.errors)


def _trigger(node_id: str = "trigger-1") -> dict:
    return {
        "id": node_id, "type": "trigger", "name": "触发", "position": {"x": 0, "y": 0},
        "config": {"triggerType": "manual", "cron": "", "webhookUrl": ""},
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_loop_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            _trigger(),
            {"id": "loop-1", "type": "loop", "name": "重试循环", "position": {"x": 1, "y": 0},
             "config": {
                 "mode": "while",
                 "continueExpression": "{{loop-1.index}} < 3",
                 "maxIterations": 10,
                 "bodyTarget": "tool-body",
                 "exitTarget": "tool-exit",
             },
             "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}},
            _tool("tool-body", "循环体操作"),
            _tool("tool-exit", "退出后操作"),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "loop-1"},
            {"id": "e2", "source": "loop-1", "target": "tool-body"},
            {"id": "e3", "source": "tool-body", "target": "loop-1"},
            {"id": "e4", "source": "loop-1", "target": "tool-exit"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_loop_graph_with_back_edge():
    graph = parse_graph(make_loop_graph())
    loop = next(node for node in graph.nodes if node.type == "loop")
    assert loop.config["bodyTarget"] == "tool-body"
    assert loop.config["exitTarget"] == "tool-exit"


def test_reject_loop_without_back_edge():
    raw = make_loop_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e3"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "连回循环节点的回边" in messages and "tool-body 没有回到循环节点的路径" in messages


def test_reject_loop_body_leaking_to_exit_target():
    raw = make_loop_graph()
    raw["edges"].append({"id": "e5", "source": "tool-body", "target": "tool-exit"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("退出路径只能由循环节点出发" in error for error in exc.value.errors)


def test_reject_nested_loop_in_body():
    raw = make_loop_graph()
    raw["nodes"].append({
        "id": "loop-2", "type": "loop", "name": "内层循环", "position": {"x": 2, "y": 0},
        "config": {"mode": "while", "continueExpression": "{{loop-2.index}} < 2",
                   "maxIterations": 3, "bodyTarget": "tool-body", "exitTarget": "loop-1"},
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    })
    raw["edges"] = [
        edge for edge in raw["edges"] if edge["id"] != "e3"
    ] + [
        {"id": "e5", "source": "tool-body", "target": "loop-2"},
        {"id": "e6", "source": "loop-2", "target": "loop-1"},
    ]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("不支持嵌套循环" in error and "loop-2" in error for error in exc.value.errors)


def test_reject_trigger_inside_loop_body():
    raw = make_loop_graph()
    raw["nodes"][2]["id"] = "tool-body"
    raw["nodes"][1]["config"]["bodyTarget"] = "trigger-1"
    raw["edges"] = [
        {"id": "e2", "source": "loop-1", "target": "trigger-1"},
        {"id": "e3", "source": "trigger-1", "target": "loop-1"},
        {"id": "e4", "source": "loop-1", "target": "tool-exit"},
    ]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("循环体内不能包含触发器" in error for error in exc.value.errors)


def test_reject_loop_bad_config_and_missing_edges():
    raw = make_loop_graph()
    raw["nodes"][1]["config"] = {
        "mode": "foreach",
        "continueExpression": "index >",
        "maxIterations": 0,
        "bodyTarget": "tool-exit",
        "exitTarget": "",
    }
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] not in ("e2", "e4")]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "mode=while" in messages
    assert "语法错误" in messages
    assert "1-100" in messages
    assert "退出目标（exitTarget）" in messages


def test_reject_extra_loop_out_edge():
    raw = make_loop_graph()
    raw["nodes"].append(_tool("tool-extra", "多余出口"))
    raw["edges"].append({"id": "e5", "source": "loop-1", "target": "tool-extra"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("tool-extra 的连线未配置" in error for error in exc.value.errors)


def test_reject_illegal_cycle_unrelated_to_loop():
    raw = {
        "version": 1,
        "variables": [],
        "nodes": [_trigger(), _tool("tool-x", "X"), _tool("tool-y", "Y")],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "tool-x"},
            {"id": "e2", "source": "tool-x", "target": "tool-y"},
            {"id": "e3", "source": "tool-y", "target": "tool-x"},
        ],
    }
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("非法循环依赖" in error for error in exc.value.errors)


def _parallel_node(node_id: str = "parallel-1", **config_overrides) -> dict:
    config = {
        "joinStrategy": "all_success",
        "branches": [{"label": "分支A", "target": "tool-a"}, {"label": "分支B", "target": "tool-b"}],
        "joinTarget": "tool-join",
    }
    config.update(config_overrides)
    return {
        "id": node_id, "type": "parallel", "name": "并行", "position": {"x": 1, "y": 0},
        "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_parallel_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            _trigger(),
            _parallel_node(),
            _tool("tool-a", "分支A操作"),
            _tool("tool-b", "分支B操作"),
            _tool("tool-join", "汇聚后操作"),
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "parallel-1"},
            {"id": "e2", "source": "parallel-1", "target": "tool-a"},
            {"id": "e3", "source": "parallel-1", "target": "tool-b"},
            {"id": "e4", "source": "tool-a", "target": "tool-join"},
            {"id": "e5", "source": "tool-b", "target": "tool-join"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_parallel_graph():
    graph = parse_graph(make_parallel_graph())
    parallel = next(node for node in graph.nodes if node.type == "parallel")
    assert parallel.config["joinTarget"] == "tool-join"
    assert [branch["target"] for branch in parallel.config["branches"]] == ["tool-a", "tool-b"]


def test_parse_valid_parallel_region_with_condition():
    raw = make_parallel_graph()
    raw["nodes"][3] = {
        "id": "cond-b", "type": "condition", "name": "B 内条件", "position": {"x": 2, "y": 1},
        "config": {
            "branches": [{"label": "大额", "expression": "{{global.amount}} > 100", "target": "tool-b1"}],
            "defaultTarget": "tool-b2",
        },
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }
    raw["nodes"].extend([_tool("tool-b1", "B1"), _tool("tool-b2", "B2")])
    raw["edges"] = [
        edge for edge in raw["edges"] if edge["id"] != "e5"
    ] + [
        {"id": "e6", "source": "parallel-1", "target": "cond-b"},
        {"id": "e7", "source": "cond-b", "target": "tool-b1"},
        {"id": "e8", "source": "cond-b", "target": "tool-b2"},
        {"id": "e9", "source": "tool-b1", "target": "tool-join"},
        {"id": "e10", "source": "tool-b2", "target": "tool-join"},
    ]
    # e3 原指向 tool-b，改指 cond-b
    raw["edges"] = [
        {"id": "e3", "source": "parallel-1", "target": "cond-b"} if edge["id"] == "e3" else edge
        for edge in raw["edges"]
    ]
    raw["nodes"][1]["config"]["branches"][1]["target"] = "cond-b"
    parse_graph(raw)  # 不抛异常即可


def test_parse_valid_parallel_region_with_self_contained_loop():
    raw = make_parallel_graph()
    raw["nodes"] = [node for node in raw["nodes"] if node["id"] != "tool-b"]
    raw["nodes"].append({
        "id": "loop-1", "type": "loop", "name": "分支内重试", "position": {"x": 3, "y": 0},
        "config": {"mode": "while", "continueExpression": "{{loop-1.index}} < 2",
                   "maxIterations": 3, "bodyTarget": "tool-body", "exitTarget": "tool-join"},
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    })
    raw["nodes"].append(_tool("tool-body", "重试体"))
    raw["edges"] = [
        edge for edge in raw["edges"] if edge["id"] != "e5"
    ] + [
        {"id": "e6", "source": "parallel-1", "target": "loop-1"},
        {"id": "e7", "source": "loop-1", "target": "tool-body"},
        {"id": "e8", "source": "tool-body", "target": "loop-1"},
        {"id": "e9", "source": "loop-1", "target": "tool-join"},
    ]
    raw["edges"] = [
        {"id": "e3", "source": "parallel-1", "target": "loop-1"} if edge["id"] == "e3" else edge
        for edge in raw["edges"]
    ]
    raw["nodes"][1]["config"]["branches"][1]["target"] = "loop-1"
    parse_graph(raw)  # 区域内自包含 loop 合法，回边白名单不触发非法环


def test_reject_parallel_bad_strategy_count_labels_targets():
    raw = make_parallel_graph()
    raw["nodes"][1] = _parallel_node(
        joinStrategy="any_success",
        branches=[{"label": "  ", "target": "tool-a"}],
        joinTarget="tool-a",
    )
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "all_success" in messages
    assert "2-10" in messages
    assert "名称（label）不能为空" in messages
    assert "不能与汇聚目标相同" in messages


def test_reject_parallel_missing_join_self_join_and_edge_mismatch():
    raw = make_parallel_graph()
    raw["nodes"][1]["config"]["joinTarget"] = "parallel-1"
    raw["edges"].append({"id": "e6", "source": "parallel-1", "target": "tool-join"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "汇聚目标不能指向自身" in messages
    assert "tool-join 的连线未配置分支" in messages


def test_reject_parallel_join_incoming_from_outside_region():
    # joinTarget 额外接收一条不经过并行区域的旁路：trigger→sibling→join
    raw = make_parallel_graph()
    raw["nodes"].append(_tool("tool-sibling", "区域外旁路"))
    raw["edges"].extend([
        {"id": "e6", "source": "trigger-1", "target": "tool-sibling"},
        {"id": "e7", "source": "tool-sibling", "target": "tool-join"},
    ])
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("只能接收分支区域内的连线" in error and "tool-sibling" in error
               for error in exc.value.errors)


def test_reject_parallel_region_edge_back_to_parallel_node():
    # 区域内节点回边到 parallel 节点本身：成环且不属于区域合法出口
    raw = make_parallel_graph()
    raw["edges"].append({"id": "e6", "source": "tool-a", "target": "parallel-1"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("交叉或外泄" in error for error in exc.value.errors)


def test_reject_nested_parallel_and_trigger_in_region():
    nested = make_parallel_graph()
    nested["nodes"][1]["config"]["branches"][0]["target"] = "parallel-2"
    nested["nodes"][2]["id"] = "parallel-2"
    nested["nodes"][2] = _parallel_node("parallel-2", joinTarget="tool-join")
    nested["edges"] = [
        {"id": "e2", "source": "parallel-1", "target": "parallel-2"} if e["id"] == "e2" else e
        for e in nested["edges"]
    ] + [{"id": "e6", "source": "parallel-2", "target": "tool-a"}]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(nested)
    assert any("不支持嵌套并行" in error for error in exc.value.errors)

    triggered = make_parallel_graph()
    triggered["nodes"][1]["config"]["branches"][0]["target"] = "trigger-2"
    triggered["nodes"].append({
        "id": "trigger-2", "type": "trigger", "name": "区内触发", "position": {"x": 2, "y": 0},
        "config": {"triggerType": "manual", "cron": "", "webhookUrl": ""},
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    })
    triggered["edges"] = [
        {"id": "e2", "source": "parallel-1", "target": "trigger-2"} if e["id"] == "e2" else e
        for e in triggered["edges"]
    ] + [{"id": "e6", "source": "trigger-2", "target": "tool-join"}]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(triggered)
    assert any("分支区域内不能包含触发器" in error for error in exc.value.errors)


def test_reject_parallel_branch_unable_to_reach_join():
    raw = make_parallel_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e4"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "tool-a 不存在到达汇聚目标" in messages


def test_reject_parallel_duplicate_label_and_target():
    raw = make_parallel_graph()
    raw["nodes"][1]["config"]["branches"][1]["label"] = "分支A"
    raw["nodes"][1]["config"]["branches"][1]["target"] = "tool-a"
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e5"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    messages = " ".join(exc.value.errors)
    assert "分支名称重复：分支A" in messages
    assert "分支目标重复：tool-a" in messages

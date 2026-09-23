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
    assert "连回循环节点的回边" in messages and "tool-body 没有回到循环节点或 break 出口的路径" in messages


def test_reject_loop_body_leaking_to_exit_target():
    raw = make_loop_graph()
    raw["edges"].append({"id": "e5", "source": "tool-body", "target": "tool-exit"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    # D17/A2：非 condition 的体内节点直连退出目标仍属非法逃逸（break 须经 condition 分支）。
    assert any("不能直接连到退出目标" in error for error in exc.value.errors)


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


def make_foreach_graph(**config_overrides):
    config = {
        "mode": "foreach",
        "itemsExpression": "{{global.order_ids}}",
        "itemName": "order",
        "collectTarget": "tool-body",
        "bodyTarget": "tool-body",
        "exitTarget": "tool-exit",
    }
    config.update(config_overrides)
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _trigger(),
            {"id": "loop-1", "type": "loop", "name": "遍历循环", "position": {"x": 1, "y": 0},
             "config": config,
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


def test_parse_valid_foreach_graph():
    graph = parse_graph(make_foreach_graph())
    loop = next(node for node in graph.nodes if node.type == "loop")
    assert loop.config["mode"] == "foreach"
    assert loop.config["itemsExpression"] == "{{global.order_ids}}"
    assert loop.config["collectTarget"] == "tool-body"


def test_reject_foreach_without_items_expression():
    raw = make_foreach_graph()
    del raw["nodes"][1]["config"]["itemsExpression"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("itemsExpression" in error for error in exc.value.errors)


def test_reject_foreach_with_syntax_error_items_expression():
    raw = make_foreach_graph(itemsExpression="{{global.order_ids}")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("遍历数组表达式" in error for error in exc.value.errors)


def test_reject_foreach_with_invalid_item_name():
    raw = make_foreach_graph(itemName="123-bad")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("itemName" in error for error in exc.value.errors)


def test_reject_foreach_collect_target_outside_body():
    raw = make_foreach_graph(collectTarget="tool-exit")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("聚合节点" in error and "不在循环体内" in error for error in exc.value.errors)


def test_reject_foreach_collect_target_self():
    raw = make_foreach_graph(collectTarget="loop-1")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("聚合节点" in error for error in exc.value.errors)


def test_reject_unsupported_loop_mode():
    raw = make_foreach_graph(mode="until")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("mode" in error for error in exc.value.errors)


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
        "mode": "until",
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
        joinStrategy="wrong_strategy",
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


def _wait_node(node_id: str = "wait-1", **config_overrides):
    config = {"waitType": "duration", "durationSeconds": 2}
    config.update(config_overrides)
    if config.get("durationSeconds") is None:
        config.pop("durationSeconds", None)
    return {
        "id": node_id, "type": "wait", "name": "等待",
        "position": {"x": 2, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_wait_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            _wait_node(),
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_wait_graph():
    graph = parse_graph(make_wait_graph())
    wait = next(node for node in graph.nodes if node.type == "wait")
    assert wait.config["waitType"] == "duration"
    assert wait.config["durationSeconds"] == 2


def test_parse_valid_wait_inside_loop_body():
    raw = make_wait_graph()
    raw["nodes"] = [
        {"id": "trigger-1", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "loop-1", "type": "loop", "name": "重试循环",
         "config": {"mode": "while", "continueExpression": "{{loop-1.index}} < 2",
                    "maxIterations": 3, "bodyTarget": "wait-1", "exitTarget": "tool-exit"}},
        _wait_node(),
        {"id": "tool-body", "type": "tool_call", "name": "循环体",
         "config": {"tool": "op-body"}},
        {"id": "tool-exit", "type": "tool_call", "name": "退出",
         "config": {"tool": "op-exit"}},
    ]
    raw["edges"] = [
        {"id": "e1", "source": "trigger-1", "target": "loop-1"},
        {"id": "e2", "source": "loop-1", "target": "wait-1"},
        {"id": "e3", "source": "wait-1", "target": "tool-body"},
        {"id": "e4", "source": "tool-body", "target": "loop-1"},
        {"id": "e5", "source": "loop-1", "target": "tool-exit"},
    ]
    parse_graph(raw)  # wait 在 loop 体内合法，回边白名单不触发非法环


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"waitType": "until", "durationSeconds": 2}, "等待类型（waitType）必须是 duration 或 event"),
        ({"waitType": "duration", "durationSeconds": "2"}, "必须是整数秒"),
        ({"waitType": "duration", "durationSeconds": True}, "必须是整数秒"),
        ({"waitType": "duration", "durationSeconds": None}, "必须是整数秒"),
        ({"waitType": "duration", "durationSeconds": 0}, "需在 1-600 秒之间"),
        ({"waitType": "duration", "durationSeconds": -1}, "需在 1-600 秒之间"),
        ({"waitType": "duration", "durationSeconds": 601}, "需在 1-600 秒之间"),
        ({"waitType": "event", "eventKey": None, "timeoutSeconds": 300}, "事件标识（eventKey）为必填"),
        ({"waitType": "event", "eventKey": "bad key", "timeoutSeconds": 300}, "只允许字母、数字及 :_-"),
        ({"waitType": "event", "eventKey": "x" * 129, "timeoutSeconds": 300}, "长度不能超过 128"),
        ({"waitType": "event", "eventKey": "order_paid", "timeoutSeconds": 0}, "需在 1-3600 秒之间"),
        ({"waitType": "event", "eventKey": "order_paid", "timeoutSeconds": 3601}, "需在 1-3600 秒之间"),
        ({"waitType": "event", "eventKey": "order_paid", "timeoutSeconds": "300"}, "必须是整数秒"),
        ({"waitType": "event", "eventKey": "order_paid", "timeoutSeconds": 300,
          "onTimeout": "abort"}, "超时策略（onTimeout）必须是 continue 或 fail"),
    ],
)
def test_reject_wait_bad_type_and_duration(config, expected):
    raw = make_wait_graph()
    raw["nodes"][1] = _wait_node(**config)
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any(expected in error for error in exc.value.errors)


@pytest.mark.parametrize(
    "config",
    [
        {"waitType": "event", "eventKey": "order_paid", "timeoutSeconds": 1},
        {"waitType": "event", "eventKey": "order_paid_{{trigger-1.context.payload.id}}",
         "timeoutSeconds": 3600, "onTimeout": "fail"},
        {"waitType": "event", "eventKey": "evt:paid-x_1", "timeoutSeconds": 300,
         "onTimeout": "continue"},
    ],
)
def test_parse_valid_event_wait(config):
    raw = make_wait_graph()
    raw["nodes"][1] = _wait_node(**config)
    parse_graph(raw)


@pytest.mark.parametrize(
    "config",
    [
        {"waitType": "duration", "durationMode": "static", "durationSeconds": 2},
        {"waitType": "duration", "durationMode": "dynamic",
         "durationExpression": "{{global.waitSecs}}"},
        {"waitType": "duration", "durationMode": "dynamic",
         "durationExpression": "{{global.slaHours}} * 3600", "durationSeconds": 5},
    ],
)
def test_parse_valid_dynamic_wait(config):
    raw = make_wait_graph()
    raw["nodes"][1] = _wait_node(**config)
    parse_graph(raw)


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"waitType": "duration", "durationMode": "soon", "durationSeconds": 2},
         "时长模式（durationMode）必须是 static 或 dynamic"),
        ({"waitType": "duration", "durationMode": "dynamic", "durationExpression": ""},
         "动态时长表达式（durationExpression）为必填"),
        ({"waitType": "duration", "durationMode": "dynamic"},
         "动态时长表达式（durationExpression）为必填"),
        ({"waitType": "duration", "durationMode": "dynamic",
          "durationExpression": "x" * 201},
         "动态时长表达式长度不能超过 200 字符"),
    ],
)
def test_reject_dynamic_wait_bad_config(config, expected):
    raw = make_wait_graph()
    raw["nodes"][1] = _wait_node(**config)
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any(expected in error for error in exc.value.errors)



def test_reject_wait_without_outgoing_edge():
    raw = make_wait_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e2"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 1 条出边" in error for error in exc.value.errors)


def test_reject_wait_with_two_outgoing_edges():
    raw = make_wait_graph()
    raw["nodes"].append({"id": "tool-other", "type": "tool_call", "name": "另一后继",
                         "config": {"tool": "op-other"}})
    raw["edges"].append({"id": "e3", "source": "wait-1", "target": "tool-other"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 1 条出边（当前 2 条）" in error for error in exc.value.errors)


def _human_node(node_id: str = "human-1", **config_overrides):
    config = {
        "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
        "approver": "客服主管",
        "timeoutSeconds": 300,
        "onTimeout": "reject",
        "approvedTarget": "tool-approve",
        "rejectedTarget": "tool-reject",
    }
    config.update(config_overrides)
    return {
        "id": node_id, "type": "human_approval", "name": "人工审批",
        "position": {"x": 2, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_human_approval_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            _human_node(),
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
    graph.update(overrides)
    return graph


def test_parse_valid_human_approval_graph():
    graph = parse_graph(make_human_approval_graph())
    human = next(node for node in graph.nodes if node.type == "human_approval")
    assert human.config["approvedTarget"] == "tool-approve"
    assert human.config["rejectedTarget"] == "tool-reject"
    assert human.config["onTimeout"] == "reject"


def test_human_approval_default_on_timeout_when_omitted():
    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(onTimeout=None)
    raw["nodes"][1]["config"].pop("onTimeout")
    parse_graph(raw)  # 默认 reject 由前端/loader 兜底，DSL 缺省视为合法默认


def test_parse_valid_human_approval_inside_loop_body():
    raw = make_human_approval_graph()
    raw["nodes"] = [
        {"id": "trigger-1", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "loop-1", "type": "loop", "name": "重试循环",
         "config": {"mode": "while", "continueExpression": "{{loop-1.index}} < 2",
                    "maxIterations": 3, "bodyTarget": "human-1", "exitTarget": "tool-exit"}},
        _human_node(),
        {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
         "config": {"tool": "op-approve"}},
        {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
         "config": {"tool": "op-reject"}},
        {"id": "tool-exit", "type": "tool_call", "name": "退出",
         "config": {"tool": "op-exit"}},
    ]
    raw["edges"] = [
        {"id": "e1", "source": "trigger-1", "target": "loop-1"},
        {"id": "e2", "source": "loop-1", "target": "human-1"},
        {"id": "e3", "source": "human-1", "target": "tool-approve"},
        {"id": "e4", "source": "human-1", "target": "tool-reject"},
        {"id": "e5", "source": "tool-approve", "target": "loop-1"},
        {"id": "e6", "source": "tool-reject", "target": "loop-1"},
        {"id": "e7", "source": "loop-1", "target": "tool-exit"},
    ]
    parse_graph(raw)  # 双出口在 loop 体内合法，两支回边均白名单


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"summary": "  "}, "审批说明（summary）"),
        ({"summary": None}, "审批说明（summary）"),
        ({"timeoutSeconds": "300"}, "必须是整数秒"),
        ({"timeoutSeconds": True}, "必须是整数秒"),
        ({"timeoutSeconds": 9}, "需在 10-3600 秒之间"),
        ({"timeoutSeconds": 3601}, "需在 10-3600 秒之间"),
        ({"onTimeout": "retry"}, "超时策略（onTimeout）必须是 approve 或 reject"),
    ],
)
def test_reject_human_approval_bad_summary_timeout_ontimeout(config, expected):
    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(**config)
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any(expected in error for error in exc.value.errors)


def test_reject_human_approval_missing_and_same_targets():
    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(approvedTarget=None)
    raw["nodes"][1]["config"].pop("approvedTarget")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("通过目标（approvedTarget）" in error for error in exc.value.errors)

    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(rejectedTarget="tool-approve")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("通过目标与拒绝目标不能相同" in error for error in exc.value.errors)


def test_reject_human_approval_nonexistent_and_self_target():
    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(approvedTarget="ghost")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("通过目标节点不存在：ghost" in error for error in exc.value.errors)

    raw = make_human_approval_graph()
    raw["nodes"][1] = _human_node(rejectedTarget="human-1")
    raw["edges"] = [
        edge for edge in raw["edges"] if edge["id"] != "e3"
    ] + [{"id": "e-self", "source": "human-1", "target": "human-1"}]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("拒绝目标不能指向自身" in error for error in exc.value.errors)


def test_reject_human_approval_wrong_edge_count_and_mismatch():
    # 0 条出边：直连 END
    raw = make_human_approval_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] == "e1"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 2 条出边（当前 0 条）" in error for error in exc.value.errors)

    # 1 条出边
    raw = make_human_approval_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e3"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 2 条出边（当前 1 条）" in error for error in exc.value.errors)

    # 3 条出边
    raw = make_human_approval_graph()
    raw["nodes"].append({"id": "tool-other", "type": "tool_call", "name": "第三后继",
                         "config": {"tool": "op-other"}})
    raw["edges"].append({"id": "e4", "source": "human-1", "target": "tool-other"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 2 条出边（当前 3 条）" in error for error in exc.value.errors)

    # 2 条出边但与配置目标不一致
    raw = make_human_approval_graph()
    raw["nodes"].append({"id": "tool-other", "type": "tool_call", "name": "第三后继",
                         "config": {"tool": "op-other"}})
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] not in {"e2", "e3"}] + [
        {"id": "e4", "source": "human-1", "target": "tool-approve"},
        {"id": "e5", "source": "human-1", "target": "tool-other"},
    ]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("到节点 tool-other 的连线未配置" in error for error in exc.value.errors)
    assert any("缺少到目标节点 tool-reject 的连线" in error for error in exc.value.errors)


def _subgraph_node(node_id: str = "subgraph-1", **config_overrides):
    config = {
        "graphId": "graph-7",
        "inputs": {"order_id": "{{trigger-1.context.payload.order_id}}"},
    }
    config.update(config_overrides)
    return {
        "id": node_id, "type": "subgraph", "name": "子流程",
        "position": {"x": 2, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def make_subgraph_graph(**overrides):
    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            _subgraph_node(),
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
            {"id": "e2", "source": "subgraph-1", "target": "tool-after"},
        ],
    }
    graph.update(overrides)
    return graph


def test_parse_valid_subgraph_graph():
    graph = parse_graph(make_subgraph_graph())
    node = next(node for node in graph.nodes if node.type == "subgraph")
    assert node.config["graphId"] == "graph-7"
    assert node.config["inputs"]["order_id"] == "{{trigger-1.context.payload.order_id}}"

    # inputs 可省略
    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node()
    raw["nodes"][1]["config"] = {"graphId": "graph-8"}
    parse_graph(raw)


def test_parse_valid_subgraph_inside_loop_body():
    raw = make_subgraph_graph()
    raw["nodes"] = [
        {"id": "trigger-1", "type": "trigger", "name": "t",
         "config": {"triggerType": "manual"}},
        {"id": "loop-1", "type": "loop", "name": "重试循环",
         "config": {"mode": "while", "continueExpression": "{{loop-1.index}} < 2",
                    "maxIterations": 3, "bodyTarget": "subgraph-1", "exitTarget": "tool-exit"}},
        _subgraph_node(),
        {"id": "tool-body", "type": "tool_call", "name": "循环体",
         "config": {"tool": "op-body"}},
        {"id": "tool-exit", "type": "tool_call", "name": "退出",
         "config": {"tool": "op-exit"}},
    ]
    raw["edges"] = [
        {"id": "e1", "source": "trigger-1", "target": "loop-1"},
        {"id": "e2", "source": "loop-1", "target": "subgraph-1"},
        {"id": "e3", "source": "subgraph-1", "target": "tool-body"},
        {"id": "e4", "source": "tool-body", "target": "loop-1"},
        {"id": "e5", "source": "loop-1", "target": "tool-exit"},
    ]
    parse_graph(raw)  # subgraph 在 loop 体内合法，回边白名单不触发非法环


def test_reject_subgraph_missing_or_empty_graph_id():
    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(graphId=None)
    raw["nodes"][1]["config"].pop("graphId")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须选择引用的已保存子图（graphId）" in error for error in exc.value.errors)

    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(graphId="   ")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须选择引用的已保存子图（graphId）" in error for error in exc.value.errors)


def test_reject_subgraph_bad_inputs():
    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(inputs=[{"k": "v"}])
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("入参映射（inputs）必须是对象" in error for error in exc.value.errors)

    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(inputs={"": "{{trigger-1.x}}"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("入参键名不能为空" in error for error in exc.value.errors)

    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(inputs={"order_id": 123})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("映射值必须是非空文本" in error for error in exc.value.errors)

    raw = make_subgraph_graph()
    raw["nodes"][1] = _subgraph_node(inputs={"order_id": "  "})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("映射值必须是非空文本" in error for error in exc.value.errors)


def test_reject_subgraph_edge_cases():
    # 0 条出边：直连 END
    raw = make_subgraph_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e2"]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 1 条出边（当前 0 条）" in error for error in exc.value.errors)

    # 2 条出边
    raw = make_subgraph_graph()
    raw["nodes"].append({"id": "tool-other", "type": "tool_call", "name": "另一后继",
                         "config": {"tool": "op-other"}})
    raw["edges"].append({"id": "e3", "source": "subgraph-1", "target": "tool-other"})
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("必须恰好配置 1 条出边（当前 2 条）" in error for error in exc.value.errors)

    # 自环
    raw = make_subgraph_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e2"] + [
        {"id": "e-self", "source": "subgraph-1", "target": "subgraph-1"}
    ]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("出边不能指向自身" in error for error in exc.value.errors)

    # 指向不存在节点：通用边校验 + 出边计数双重报错
    raw = make_subgraph_graph()
    raw["edges"] = [edge for edge in raw["edges"] if edge["id"] != "e2"] + [
        {"id": "e-ghost", "source": "subgraph-1", "target": "ghost"}
    ]
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    assert any("target 节点不存在：ghost" in error for error in exc.value.errors)
    assert any("必须恰好配置 1 条出边（当前 0 条）" in error for error in exc.value.errors)

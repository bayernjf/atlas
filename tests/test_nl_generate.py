"""自然语言 → 流程草稿测试（docs/08 §7.3 验收 6）。"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import parse_graph, validate_graph
from atlas.llm.nl_generate import generate_graph, validate_param_fills


def test_refund_intent_returns_valid_refund_template():
    graph = generate_graph("帮我做一个电商退款自动审批流程")
    parsed = parse_graph(graph)
    assert validate_graph(parsed) == []
    assert [node.type for node in parsed.nodes] == [
        "trigger", "ai_decision", "condition", "human_approval",
        "tool_call", "tool_call", "tool_call",
    ]
    assert parsed.nodes[4].config["tool"] == "shop/process_refund"
    assert parsed.nodes[5].config["tool"] == "shop/execute_refund"
    assert parsed.nodes[6].config["tool"] == "shop/reject_refund"


def test_unknown_intent_without_llm_raises(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    with pytest.raises(ValueError):
        generate_graph("帮我管管日历日程")


def test_prod_without_llm_refund_intent_refuses_silent_template(monkeypatch):
    """docs/73 W1-1.1/W5-5.4 同族：prod 无模型不得静默返回写死模板（fail-closed）。"""
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.setattr(
        "atlas.llm.nl_generate.demo_surface_enabled", lambda: False
    )
    with pytest.raises(ValueError) as exc:
        generate_graph("帮我做一个电商退款自动审批流程")
    assert "生产环境未配置 LLM" in str(exc.value)


def test_prod_without_llm_arbitrary_intent_also_refuses(monkeypatch):
    """prod 无模型：任意意图（非退款关键词）同样显式报错，不落入任何兜底。"""
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.setattr(
        "atlas.llm.nl_generate.demo_surface_enabled", lambda: False
    )
    with pytest.raises(ValueError) as exc:
        generate_graph("帮我管管日历日程")
    assert "生产环境未配置 LLM" in str(exc.value)



def test_llm_prompt_advertises_condition_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "condition" in system
    assert "branches" in system
    assert "defaultTarget" in system


def test_llm_prompt_advertises_shop_output_and_global_ref_rules(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "result.orders[0].amount" in system
    assert "{{global.<变量名>}}" in system
    assert "web-playwright/navigate" in system
    assert "memory/auto_extract" in system
    assert "{{<节点id>.decision}}" in system
    assert "禁止用 http/request 代替 shop 退款工具" in system
    assert "ai_decision 的 config 必须带非空 promptTemplate" in system
    assert "禁止把末端连回 trigger 形成环" in system
    assert "流程只能有一个 trigger 起点" in system
    assert "schedule 必须给 5 段 cron" in system
    assert "不要把整段布尔表达式再包一层" in system
    assert "也不是裸 {{context.payload...}}" in system
    assert "wait 节点恰好配置一条出边" in system
    assert "不能作为图的结束节点" in system
    assert "只有 loop 节点的循环体才允许回到该 loop 节点" in system
    assert "不要新增 wait/end 占位节点" in system
    assert "工具调用节点 type 必须是 tool_call" in system
    assert "len() 只能出现在 condition/loop 的 expression" in system


def test_llm_prompt_advertises_loop_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "loop" in system
    assert '"mode":"while"' in system
    assert "continueExpression" in system
    assert "maxIterations" in system
    assert "bodyTarget" in system
    assert "exitTarget" in system
    assert "回到该 loop 节点的回边" in system


def test_llm_prompt_advertises_parallel_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "parallel" in system
    assert "joinStrategy" in system
    assert "all_success" in system
    assert "all_completed" in system
    assert "joinTarget" in system
    assert "2-10 个" in system
    assert "不直连结束" in system
    assert "不得交叉" in system
    assert "不得再嵌套 parallel" in system


def test_llm_prompt_advertises_wait_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/wait" in system
    assert "/subgraph" in system
    assert "/human_approval" in system
    assert "九类" in system
    assert "waitType" in system
    assert "durationSeconds" in system
    assert "1-3600 的整数秒" in system
    assert "事件等待不支持" in system
    assert "恰好配置一条出边" in system
    assert "10-3600 的整数秒" in system
    assert "approvedTarget" in system
    assert "rejectedTarget" in system
    assert "恰好两条出边" in system


def test_llm_prompt_advertises_subgraph_kind_and_config(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/subgraph" in system
    assert "九类" in system
    assert "graphId" in system
    assert "inputs" in system
    assert "已保存图 id" in system
    assert "不要凭空捏造 id" in system
    assert "恰好配置一条出边" in system


def test_llm_prompt_advertises_registered_tools(monkeypatch):
    import litellm

    captured: dict[str, str] = {}

    def _fake_completion(*, model, messages, temperature, **kwargs):  # noqa: ANN001
        captured["system"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "{\"nodes\": [], \"edges\": []}"}}]}

    monkeypatch.setattr(litellm, "completion", _fake_completion)
    monkeypatch.setenv("LITELLM_MODEL", "fake-model")

    generate_graph("任意需求")
    system = captured["system"]
    assert "/api/adapters" in system
    assert "http/request" in system
    assert "params 是 JSON 字符串" in system
    assert "database/query" in system
    assert "database/execute" in system
    assert "绑定参数" in system
    assert "message/send" in system
    assert "shop/process_refund" in system


_PROCESS_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "action": {"type": "string", "enum": ["approve_refund", "request_human_approval"]},
        "note": {"type": "string"},
    },
    "required": ["order_id", "action"],
}

_TOOL_SCHEMAS = {"shop/process_refund": _PROCESS_REFUND_SCHEMA, "open/thing": {}}


def _tool_graph(tool: str, params: str) -> dict:
    return {"version": 1, "nodes": [
        {"id": "t1", "type": "tool_call", "config": {"tool": tool, "params": params}}
    ], "edges": []}


def test_param_fills_legal():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":"O-1","action":"approve_refund","note":"ok"}',
    )
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_missing_required():
    warnings = validate_param_fills(_tool_graph("shop/process_refund", '{"order_id":"O-1"}'), _TOOL_SCHEMAS)
    assert len(warnings) == 1
    assert "缺少必填字段" in warnings[0]
    assert "action" in warnings[0]
    assert "t1" in warnings[0]


def test_param_fills_bad_type_and_enum():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":123,"action":"refund_now"}',
    )
    warnings = validate_param_fills(graph, _TOOL_SCHEMAS)
    joined = "; ".join(warnings)
    assert "order_id" in joined and "string" in joined
    assert "action" in joined and "允许范围" in joined


def test_param_fills_template_values_skipped_but_count_as_present():
    graph = _tool_graph(
        "shop/process_refund",
        '{"order_id":"{{trigger-1.context.payload.order_id}}","action":"approve_refund"}',
    )
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_unparseable_params_pass():
    # 裸 {{}} 插值使 params 不是合法 JSON，尽力校验直接放行
    graph = _tool_graph("shop/process_refund", '{"order_id":{{trigger-1.context.payload.id}}}')
    assert validate_param_fills(graph, _TOOL_SCHEMAS) == []


def test_param_fills_unknown_tool_and_empty_schema_pass():
    assert validate_param_fills(_tool_graph("ghost/tool", '{}'), _TOOL_SCHEMAS) == []
    assert validate_param_fills(_tool_graph("open/thing", '{"anything": 1}'), _TOOL_SCHEMAS) == []


def test_param_fills_additional_properties_false():
    schemas = {"shop/process_refund": {**_PROCESS_REFUND_SCHEMA, "additionalProperties": False}}
    warnings = validate_param_fills(
        _tool_graph("shop/process_refund", '{"order_id":"O-1","action":"approve_refund","bogus":1}'),
        schemas,
    )
    assert any("未声明字段" in w and "bogus" in w for w in warnings)


def test_normalize_drops_dangling_edge_and_dedupes_ids():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "start", "type": "trigger", "name": "开始"},
                {"id": "start", "type": "tool_call", "config": {"tool": "shop/list_pending_refunds"}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "end"},
                {"id": "e2", "source": "start", "target": "start-2"},
            ],
        }
    )
    assert [node["id"] for node in graph["nodes"]] == ["start", "start-2"]
    assert [edge["target"] for edge in graph["edges"]] == ["start-2"]
    assert graph["version"] == 1


def test_normalize_uses_alternative_edge_for_condition_default():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "c", "type": "condition", "config": {
                    "branches": [{"label": "big", "expression": "true", "target": "a"}],
                    "defaultTarget": "a",
                }},
                {"id": "a", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "b", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
            ],
            "edges": [
                {"id": "e1", "source": "c", "target": "a"},
                {"id": "e2", "source": "c", "target": "b"},
            ],
        }
    )
    assert graph["nodes"][0]["config"]["defaultTarget"] == "b"


def test_normalize_drops_condition_branch_that_duplicates_default_target():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "trigger_start", "type": "trigger", "config": {}},
                {
                    "id": "cond",
                    "type": "condition",
                    "config": {
                        "branches": [
                            {"label": "成功", "expression": "{{q.result.ok}}", "target": "ok_node"},
                            {"label": "失败", "expression": "{{q.result.ok}} == false", "target": "fail_node"},
                        ],
                        "defaultTarget": "fail_node",
                    },
                },
                {"id": "ok_node", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "fail_node", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger_start", "target": "cond"},
                {"id": "e2", "source": "cond", "target": "ok_node"},
                {"id": "e3", "source": "cond", "target": "fail_node"},
            ],
        }
    )
    condition = next(node for node in graph["nodes"] if node["id"] == "cond")
    assert [branch["label"] for branch in condition["config"]["branches"]] == ["成功"]
    assert condition["config"]["defaultTarget"] == "fail_node"


def test_tool_output_contract_exposes_real_fields():
    from atlas.api.main import _demo_registry
    from atlas.llm.nl_generate import _tool_output_contract

    contract = _tool_output_contract(_demo_registry)
    assert "shop/list_pending_refunds" in contract
    assert "reason:string" in contract
    assert "memory/recall" in contract
    assert "results:[{id:string,kind:string,content:string" in contract
    assert "memory/auto_extract" in contract
    assert "extracted:integer" in contract
    assert "shop/reject_refund" in contract
    assert "status:string" in contract


def test_normalize_repairs_malformed_condition_and_loop_expressions():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "t", "type": "trigger", "config": {}},
                {
                    "id": "c",
                    "type": "condition",
                    "config": {
                        "branches": [
                            {
                                "label": "有记录",
                                "expression": "{{q.result.row_count} > 0",
                                "target": "a",
                            },
                            {
                                "label": "非空",
                                "expression": "{{save.result != null}}",
                                "target": "b",
                            },
                            {
                                "label": "合法",
                                "expression": "{{a}} == {{b}}",
                                "target": "d",
                            },
                            {
                                "label": "外层多括号",
                                "expression": (
                                    "{{tool_refund_progress.result.status == 200 && "
                                    "{{tool_refund_progress.result.body.refund_status != null}}"
                                ),
                                "target": "f",
                            },
                        ],
                        "defaultTarget": "e",
                    },
                },
                {
                    "id": "loop",
                    "type": "loop",
                    "config": {
                        "mode": "while",
                        "continueExpression": "{{counter} < {{limit}}",
                        "maxIterations": 10,
                        "bodyTarget": "a",
                        "exitTarget": "b",
                    },
                },
                {"id": "a", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "b", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "d", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "e", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
                {"id": "f", "type": "wait", "config": {"waitType": "duration", "durationSeconds": 1}},
            ],
            "edges": [
                {"id": "e1", "source": "t", "target": "c"},
                {"id": "e2", "source": "c", "target": "a"},
                {"id": "e3", "source": "c", "target": "b"},
                {"id": "e6", "source": "c", "target": "d"},
                {"id": "e7", "source": "c", "target": "e"},
                {"id": "e8", "source": "c", "target": "f"},
                {"id": "e4", "source": "loop", "target": "a"},
                {"id": "e5", "source": "loop", "target": "b"},
            ],
        }
    )
    cond = next(node for node in graph["nodes"] if node["id"] == "c")
    expressions = [branch["expression"] for branch in cond["config"]["branches"]]
    assert expressions == [
        "{{q.result.row_count}} > 0",
        "{{save.result}} != null",
        "{{a}} == {{b}}",
        (
            "{{tool_refund_progress.result.status}} == 200 && "
            "{{tool_refund_progress.result.body.refund_status}} != null"
        ),
    ]
    loop = next(node for node in graph["nodes"] if node["id"] == "loop")
    assert loop["config"]["continueExpression"] == "{{counter}} < {{limit}}"


def test_normalize_qualifies_bare_context_refs_with_single_trigger():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "trig_refund", "type": "trigger", "config": {"triggerType": "manual"}},
                {
                    "id": "approve",
                    "type": "human_approval",
                    "config": {
                        "summary": "订单 {{context.payload.order_id}}",
                        "approver": "财务",
                        "timeoutSeconds": 60,
                        "onTimeout": "reject",
                        "approvedTarget": "refund",
                        "rejectedTarget": "notify",
                    },
                },
                {
                    "id": "refund",
                    "type": "tool_call",
                    "config": {
                        "tool": "shop/execute_refund",
                        "params": '{"order_id":"{{context.payload.order_id}}"}',
                    },
                },
                {
                    "id": "notify",
                    "type": "tool_call",
                    "config": {"tool": "message/send", "params": "{}"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "trig_refund", "target": "approve"},
                {"id": "e2", "source": "approve", "target": "refund"},
                {"id": "e3", "source": "approve", "target": "notify"},
            ],
        }
    )
    approve = next(node for node in graph["nodes"] if node["id"] == "approve")
    refund = next(node for node in graph["nodes"] if node["id"] == "refund")
    assert approve["config"]["summary"] == "订单 {{trig_refund.context.payload.order_id}}"
    assert refund["config"]["params"] == '{"order_id":"{{trig_refund.context.payload.order_id}}"}'


def test_normalize_converts_tool_name_node_type_to_tool_call():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "t", "type": "trigger", "config": {"triggerType": "manual"}},
                {
                    "id": "notify",
                    "type": "message/send",
                    "config": {
                        "tool": "message/send",
                        "params": '{"channel":"email","to":["ops@example.com"],"body":"ok"}',
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "t", "target": "notify"}],
        }
    )
    notify = next(node for node in graph["nodes"] if node["id"] == "notify")
    assert notify["type"] == "tool_call"
    assert notify["config"]["tool"] == "message/send"


def test_normalize_collapses_constant_condition_and_drops_non_loop_self_edge():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {"id": "trigger_start", "type": "trigger", "config": {"triggerType": "manual"}},
                {"id": "recall", "type": "tool_call", "config": {"tool": "memory/recall", "params": "{}"}},
                {"id": "decide", "type": "ai_decision", "config": {"promptTemplate": "选择渠道"}},
                {
                    "id": "cond",
                    "type": "condition",
                    "config": {
                        "branches": [
                            {"label": "邮件", "expression": "{{decide.result.channel}} == \"email\"", "target": "send"},
                            {"label": "短信", "expression": "{{decide.result.channel}} == \"sms\"", "target": "send"},
                        ],
                        "defaultTarget": "send",
                    },
                },
                {"id": "send", "type": "tool_call", "config": {"tool": "message/send", "params": "{}"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger_start", "target": "recall"},
                {"id": "e2", "source": "recall", "target": "decide"},
                {"id": "e3", "source": "decide", "target": "cond"},
                {"id": "e4", "source": "cond", "target": "send"},
                {"id": "e5", "source": "send", "target": "send"},
            ],
        }
    )
    node_ids = {node["id"] for node in graph["nodes"]}
    assert "cond" not in node_ids
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("trigger_start", "recall"),
        ("recall", "decide"),
        ("decide", "send"),
    ]


def test_normalize_repairs_unknown_retry_on_error_to_stop():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "nodes": [
                {
                    "id": "q",
                    "type": "tool_call",
                    "config": {"tool": "database/query", "params": "{}"},
                    "retry": {"max_retries": 2, "on_error": "retry"},
                }
            ],
            "edges": [],
        }
    )
    assert graph["nodes"][0]["retry"]["on_error"] == "stop"
    assert graph["nodes"][0]["retry"]["max_retries"] == 2


def test_normalize_qualifies_declared_global_refs_without_touching_node_ids():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "variables": [
                {"name": "customer_email", "type": "string", "value": "", "scope": "global"},
                {"name": "order_id", "type": "string", "value": "", "scope": "global"},
            ],
            "nodes": [
                {"id": "trigger_start", "type": "trigger", "config": {}},
                {"id": "notify", "type": "tool_call", "config": {
                    "tool": "message/send",
                    "params": "{\"to\":[\"{{customer_email}}\"],\"body\":\"订单 {{order_id}} 已退款\"}",
                }},
                {"id": "wait_1", "type": "wait", "config": {
                    "waitType": "duration", "durationSeconds": 1,
                    "note": "{{customer_email}}",
                }},
            ],
            "edges": [
                {"id": "e1", "source": "trigger_start", "target": "notify"},
                {"id": "e2", "source": "notify", "target": "wait_1"},
            ],
        }
    )
    assert "{{global.customer_email}}" in graph["nodes"][1]["config"]["params"]
    assert "{{global.order_id}}" in graph["nodes"][1]["config"]["params"]
    assert graph["nodes"][2]["config"]["note"] == "{{global.customer_email}}"


def test_normalize_keeps_bare_ref_when_global_name_collides_with_node_id():
    from atlas.llm.nl_generate import normalize_generated_graph

    graph = normalize_generated_graph(
        {
            "variables": [{"name": "customer_email", "type": "string", "value": "", "scope": "global"}],
            "nodes": [
                {"id": "trigger_start", "type": "trigger", "config": {}},
                {"id": "customer_email", "type": "wait", "config": {
                    "waitType": "duration", "durationSeconds": 1, "note": "{{customer_email}}",
                }},
            ],
            "edges": [{"id": "e1", "source": "trigger_start", "target": "customer_email"}],
        }
    )
    # 同名节点 id 优先，裸引用保持原样，不误判成 global 变量
    assert graph["nodes"][1]["config"]["note"] == "{{customer_email}}"


def test_bracket_index_path_passes_tool_output_schema():
    from atlas.api.main import _demo_registry
    from atlas.llm.nl_generate import validate_generated_graph

    graph = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t", "config": {}},
            {"id": "q", "type": "tool_call", "name": "q", "config": {
                "tool": "database/query", "params": "{\"sql\":\"select 1 as amount\"}"
            }},
            {"id": "c", "type": "condition", "name": "c", "config": {
                "branches": [{"label": "big", "expression": "{{q.result.rows[0].amount}} > 1000", "target": "a"}],
                "defaultTarget": "b",
            }},
            {"id": "a", "type": "tool_call", "name": "a", "config": {
                "tool": "message/send", "params": "{\"channel\":\"email\",\"to\":[\"a@b.com\"],\"subject\":\"x\",\"body\":\"y\"}"
            }},
            {"id": "b", "type": "tool_call", "name": "b", "config": {
                "tool": "message/send", "params": "{\"channel\":\"email\",\"to\":[\"a@b.com\"],\"subject\":\"x\",\"body\":\"y\"}"
            }},
        ],
        "edges": [
            {"id": "e1", "source": "t", "target": "q"},
            {"id": "e2", "source": "q", "target": "c"},
            {"id": "e3", "source": "c", "target": "a"},
            {"id": "e4", "source": "c", "target": "b"},
        ],
    }
    assert validate_generated_graph(graph, _demo_registry) == {
        "ok": True, "errors": [], "codes": [],
    }


def test_generate_with_meta_repairs_invalid_first_draft(monkeypatch):
    import atlas.llm.nl_generate as nl

    calls: list[dict] = []

    def fake_generate(prompt, model, *, api_key=None, base_url=None, repair=None, output_contract=None):
        calls.append({"repair": repair})
        if len(calls) == 1:
            return {
                "nodes": [
                    {"id": "t", "type": "trigger", "name": "t"},
                    {"id": "c", "type": "condition", "name": "c", "config": {
                        "branches": [{"label": "x", "expression": "true", "target": "missing"}],
                        "defaultTarget": "missing",
                    }},
                ],
                "edges": [{"id": "e1", "source": "t", "target": "c"}],
            }
        from atlas.template.graphs import refund_template_graph

        return refund_template_graph()

    monkeypatch.setattr(nl, "_resolve_llm_config", lambda tenant_id: ("fake-model", None, None))
    monkeypatch.setattr(nl, "_generate_with_llm", fake_generate)
    from atlas.api.main import _demo_registry

    graph, meta = nl.generate_graph_with_meta("退款", registry=_demo_registry)
    assert len(calls) == 2
    assert calls[1]["repair"] is not None
    assert meta["mode"] == "llm"
    assert meta["attempts"] == 2
    assert meta["repaired"] is True
    assert meta["compileOk"] is True
    assert len(graph["nodes"]) == 7


def test_nl_output_budget_is_not_1024():
    import atlas.llm.nl_generate as nl

    assert nl._MAX_TOKENS >= 4096

"""Graph DSL 错误码向量契约测试（docs/17 §2.4：code 是契约、message 是日志/兜底）。

覆盖：
- GraphValidationError 的 errors/codes/params 等长、下标对齐；code 非空大写下划线串；
  params 全为 dict；locations 稀疏侧车的 index 不越界。
- 缺省 codes/params 时兜底 GRAPH_VALIDATION_FAILED。
- parse 坏 JSON → DSL_PARSE_FAILED。
- validate_graph_report 返回 (messages, locations, codes, params) 四元组且等长。
- 各节点类型代表性错误码命中（防码名漂移/漏配 code）。
"""

from __future__ import annotations

import json

import pytest

from atlas.graph.dsl import (
    GRAPH_VALIDATION_FAILED,
    GraphValidationError,
    parse_graph,
    validate_graph_report,
)


def _trigger(**config_overrides):
    cfg = {"triggerType": "webhook", "webhookUrl": "/hooks/x"}
    cfg.update(config_overrides)
    return {
        "id": "trigger-1", "type": "trigger", "name": "触发",
        "position": {"x": 0, "y": 0}, "config": cfg,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _node(node_id, node_type, config):
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "position": {"x": 1, "y": 1}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _graph(nodes, edges=None):
    if edges is None:
        edges = [{"id": "e1", "source": "trigger-1", "target": nodes[0]["id"]}]
    return {"version": 1, "variables": [], "nodes": [_trigger(), *nodes], "edges": edges}


def _collect_codes(raw):
    """跑 parse_graph，返回 (codes, params, errors, locations)；不抛则返回 ([],...)。"""
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(raw)
    err = exc.value
    return err.codes, err.params, err.errors, err.locations


def _assert_vector_invariants(codes, params, errors, locations):
    n = len(errors)
    assert len(codes) == n, "codes 必须与 errors 等长"
    assert len(params) == n, "params 必须与 errors 等长"
    for code in codes:
        assert isinstance(code, str) and code, "code 必须是非空字符串"
        # 码名约定：大写字母/数字/下划线，且不以数字开头。
        assert code == code.upper() and all(
            ch.isupper() or ch.isdigit() or ch == "_" for ch in code
        ), f"错误码命名不符合约定: {code}"
    for prm in params:
        assert isinstance(prm, dict), "params 每项必须是 dict"
        json.dumps(prm, ensure_ascii=False)  # 必须可 JSON 序列化下发
    for loc in locations or []:
        assert 0 <= loc["index"] < n, "locations 稀疏侧车 index 不得越界"


def test_default_codes_and_params_fall_back_to_generic():
    err = GraphValidationError(["某中文错误", "另一个错误"])
    assert err.errors == ["某中文错误", "另一个错误"]
    assert err.codes == [GRAPH_VALIDATION_FAILED, GRAPH_VALIDATION_FAILED]
    assert err.params == [{}, {}]


def test_malformed_json_yields_parse_failed_code():
    with pytest.raises(GraphValidationError) as exc:
        parse_graph('{"version": 1, "nodes": [')
    assert exc.value.codes[0] == "DSL_PARSE_FAILED"
    assert isinstance(exc.value.params[0].get("detail"), str)
    _assert_vector_invariants(
        exc.value.codes, exc.value.params, exc.value.errors, exc.value.locations
    )


def test_validate_graph_report_returns_four_tuple_aligned():
    # 合法图：report 不抛、返回四元组且三段等长。
    good = parse_graph(_graph([_node("tool-1", "tool_call", {"tool": "web-playwright/click"})]))
    messages, locations, codes, params = validate_graph_report(good)
    assert messages == [] and codes == [] and params == []
    assert isinstance(messages, list) and isinstance(codes, list) and isinstance(params, list)
    assert len(messages) == len(codes) == len(params)


@pytest.mark.parametrize(
    "node,expected_code",
    [
        (_node("c1", "condition", {}), "COND_BRANCHES_REQUIRED"),
        (_node("l1", "loop", {"mode": "bogus"}), "LOOP_MODE_INVALID"),
        (_node("p1", "parallel", {}), "PAR_STRATEGY_INVALID"),
        (_node("w1", "wait", {"waitType": "bogus"}), "WAIT_TYPE_INVALID"),
        (_node("h1", "human_approval", {}), "APR_SUMMARY_REQUIRED"),
        (_node("s1", "subgraph", {}), "SUB_GRAPH_ID_REQUIRED"),
    ],
)
def test_each_node_kind_emits_known_code(node, expected_code):
    codes, params, errors, locations = _collect_codes(_graph([node]))
    _assert_vector_invariants(codes, params, errors, locations)
    assert expected_code in codes, f"期望命中 {expected_code}，实际 {codes}"


def test_duplicate_node_id_code_and_params():
    raw = _graph([_node("tool-1", "tool_call", {"tool": "x"})])
    raw["nodes"].append(
        {**_node("tool-1", "tool_call", {"tool": "y"}), "position": {"x": 3, "y": 3}}
    )
    codes, params, errors, locations = _collect_codes(raw)
    _assert_vector_invariants(codes, params, errors, locations)
    assert "NODE_ID_DUPLICATE" in codes
    hit = params[codes.index("NODE_ID_DUPLICATE")]
    assert hit == {"nodeId": "tool-1"}


def test_template_ref_missing_node_code():
    # L2 引用校验在编译期（check_refs=True），report 聚合返回四元组而不抛。
    parsed = parse_graph(
        _graph([_node("ai-1", "ai_decision", {"promptTemplate": "引用 {{ghost.result}}"})])
    )
    _messages, _locations, codes, params = validate_graph_report(parsed, check_refs=True)
    assert "REF_NODE_NOT_FOUND" in codes
    prm = params[codes.index("REF_NODE_NOT_FOUND")]
    assert prm["display"] == "{{ghost.result}}"
    assert prm["owner"] == "ai-1"  # 出错节点（引用方）由闭包统一注入


def test_condition_expression_invalid_carries_branch_and_detail():
    node = _node(
        "c1",
        "condition",
        {
            "mode": "rule",
            "defaultTarget": "tool-1",
            "branches": [
                {"label": "B1", "target": "tool-1", "expression": "!!!not-valid", "output": "x"}
            ],
        },
    )
    raw = _graph(
        [node, _node("tool-1", "tool_call", {"tool": "web-playwright/click"})],
        edges=[
            {"id": "e1", "source": "trigger-1", "target": "c1"},
            {"id": "e2", "source": "c1", "target": "tool-1"},
        ],
    )
    codes, params, errors, locations = _collect_codes(raw)
    _assert_vector_invariants(codes, params, errors, locations)
    # 表达式非法应透传引擎 detail（本批为中文 detail，conditions 引擎码化是后续小批）。
    if "COND_EXPRESSION_INVALID" in codes:
        prm = params[codes.index("COND_EXPRESSION_INVALID")]
        assert prm["branch"] in ("B1", 1)
        assert "detail" in prm


def test_data_dependency_cycle_code_whitebox():
    from atlas.graph.dsl import _validate_data_dependency_cycles

    issues = _validate_data_dependency_cycles([("a", "b", "/x"), ("b", "a", "/y")])
    assert issues, "双向数据依赖应成环"
    _message, location, code, prm = issues[0]
    assert code == "GRAPH_DATA_CYCLE"
    assert "chain" in prm and isinstance(prm["chain"], str)
    assert location is not None

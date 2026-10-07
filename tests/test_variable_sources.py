"""打包 A3（docs/99）：图变量受限来源 env/secret 与字段级可见（U1196–U1199）。"""

from __future__ import annotations

import json
import os

import pytest

from atlas.graph.dsl import GraphDSL, GraphVariable, parse_graph, validate_graph_report
from atlas.graph.loader import (
    EnvVariableUnavailable,
    _redact_outputs,
    initial_state,
    run_graph,
)
from atlas.graph.redact import redact_sensitive
from atlas.security.secrets import PlaintextSecretProvider, SecretUnavailable


def _sourced_graph(
    *, source: str, ref: str, var_name: str = "secret_val",
    expected: str = "expected-match",
):
    """trigger → rule condition：表达式引用受限来源变量，分支走 tool-a。

    expected 为表达式里的手写字面量（操作者期望值）；注入展开值可与之相同（命中）
    或不同（未中）。表达式原文会出现在 condition 输出的 expressionResults.expr——
    那是操作语义（期望值），不是展开值泄漏；泄漏断言用与 expected 不同的独特展开值。
    """
    return {
        "version": 1,
        "variables": [
            {"name": var_name, "type": "string", "value": ref,
             "scope": "global", "source": source}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "cond-1", "type": "condition", "name": "分流", "position": {"x": 2, "y": 0},
             "config": {
                 "conditionMode": "rule",
                 "branches": [
                     {"label": "命中", "expression": f"{{{{global.{var_name}}}}} == '{expected}'",
                      "target": "tool-a"},
                     {"label": "未中", "expression": f"{{{{global.{var_name}}}}} != '{expected}'",
                      "target": "tool-b"},
                 ],
                 "defaultTarget": "tool-default",
             }},
            {"id": "tool-a", "type": "tool_call", "name": "A", "config": {"tool": "op-a"}},
            {"id": "tool-b", "type": "tool_call", "name": "B", "config": {"tool": "op-b"}},
            {"id": "tool-default", "type": "tool_call", "name": "默认", "config": {"tool": "op-default"}},
        ],
        "edges": [
            {"id": "e0", "source": "trigger-1", "target": "cond-1"},
            {"id": "e1", "source": "cond-1", "target": "tool-a"},
            {"id": "e2", "source": "cond-1", "target": "tool-b"},
            {"id": "e3", "source": "cond-1", "target": "tool-default"},
        ],
    }


# ---------- U1196：DSL 校验 ----------

def test_u1196_source_invalid_reports_code():
    """非法 source 值：pydantic Literal 先拦截（422 literal_error），防御码 VAR_SOURCE_INVALID 对程序化构造可达。"""
    bad = GraphVariable.model_construct(name="a", value="x", source="bogus")
    _msgs, _locs, codes, params = validate_graph_report(
        GraphDSL.model_construct(nodes=[], edges=[], variables=[bad])
    )
    assert "VAR_SOURCE_INVALID" in codes
    assert any(p.get("source") == "bogus" for p in params)


def test_u1196_source_ref_empty_reports_code():
    bad = GraphVariable.model_construct(name="a", value="", source="env")
    _msgs, _locs, codes, _params = validate_graph_report(
        GraphDSL.model_construct(nodes=[], edges=[], variables=[bad])
    )
    assert "VAR_SOURCE_REF_EMPTY" in codes


def test_u1196_env_ref_invalid_reports_code():
    bad = GraphVariable.model_construct(name="a", value="1BAD-NAME", source="env")
    _msgs, _locs, codes, _params = validate_graph_report(
        GraphDSL.model_construct(nodes=[], edges=[], variables=[bad])
    )
    assert "VAR_SOURCE_REF_INVALID" in codes


def test_u1196_source_scope_mismatch_defensive():
    """scope 非 global 与 source 互斥（模型层 scope 恒 global，防御码保留）。"""
    bad = GraphVariable.model_construct(
        name="a", value="K", source="env", scope="local"
    )
    _msgs, _locs, codes, _params = validate_graph_report(
        GraphDSL.model_construct(nodes=[], edges=[], variables=[bad])
    )
    assert "VAR_SOURCE_SCOPE_MISMATCH" in codes


def test_u1196_legal_source_compiles():
    graph = parse_graph(_sourced_graph(source="env", ref="ATLAS_A3_TEST"))
    assert graph.variables[0].source == "env"
    assert graph.variables[0].value == "ATLAS_A3_TEST"


# ---------- U1197：env 解析 ----------

def test_u1197_env_hit_expands_and_routes(monkeypatch):
    monkeypatch.setenv("ATLAS_A3_TEST", "expected-match")
    result = run_graph(parse_graph(_sourced_graph(source="env", ref="ATLAS_A3_TEST")))
    assert result["status"] == "completed"
    assert result["outputs"]["cond-1"]["branch"] == "命中"
    assert result["outputs"]["cond-1"]["target"] == "tool-a"


def test_u1197_env_miss_fails_closed(monkeypatch):
    monkeypatch.delenv("ATLAS_A3_MISSING_VAR", raising=False)
    with pytest.raises(EnvVariableUnavailable) as exc:
        initial_state(
            parse_graph(_sourced_graph(source="env", ref="ATLAS_A3_MISSING_VAR"))
        )
    assert exc.value.code == "ENV_VARIABLE_UNAVAILABLE"
    assert exc.value.name == "ATLAS_A3_MISSING_VAR"


# ---------- U1198：secret 解析 ----------

def test_u1198_secret_hit_expands_and_routes():
    provider = PlaintextSecretProvider({"api_key": "expected-match"})
    result = run_graph(
        parse_graph(_sourced_graph(source="secret", ref="api_key")),
        secret_provider=provider,
    )
    assert result["status"] == "completed"
    assert result["outputs"]["cond-1"]["branch"] == "命中"


def test_u1198_secret_uri_prefix_normalized():
    provider = PlaintextSecretProvider({"api_key": "plain-secret"})
    graph = parse_graph(_sourced_graph(source="secret", ref="secret://api_key"))
    state = initial_state(graph, secret_provider=provider)
    assert state["variables"]["global"]["secret_val"] == "plain-secret"


def test_u1198_secret_miss_fails_closed():
    provider = PlaintextSecretProvider({"api_key": "plain-secret"})
    with pytest.raises(SecretUnavailable):
        initial_state(
            parse_graph(_sourced_graph(source="secret", ref="no_such_key")),
            secret_provider=provider,
        )


def test_u1198_secret_without_provider_fails_closed():
    with pytest.raises(SecretUnavailable):
        initial_state(parse_graph(_sourced_graph(source="secret", ref="api_key")))


# ---------- U1199：字段级可见（脱敏） ----------

def test_u1199_run_outputs_never_leak_plaintext(monkeypatch):
    # 独特展开值（≠ 表达式手写字面量 expected-match）：求值引用后仍不出现在任何通道
    monkeypatch.setenv("ATLAS_A3_TEST", "sk-live-9f3a-unique")
    graph = parse_graph(_sourced_graph(source="env", ref="ATLAS_A3_TEST"))
    result = run_graph(graph)
    assert result["status"] == "completed"
    blob = json.dumps(result, ensure_ascii=False)
    assert "sk-live-9f3a-unique" not in blob
    # 敏感表只进 state、不进 result（图投影保持引用形态：value=env 引用名）
    assert "ATLAS_A3_TEST" in json.dumps(graph.model_dump())


def test_u1199_graph_projection_keeps_reference_shape():
    graph = parse_graph(_sourced_graph(source="secret", ref="api_key"))
    var = graph.variables[0]
    assert var.value == "api_key"  # 引用形态，无明文
    assert var.source == "secret"


def test_u1199_redact_sensitive_recursive():
    mapping = {"sk-live-9f3a-unique": "<redacted:secret:api_key>"}
    assert redact_sensitive("sk-live-9f3a-unique", mapping) == "<redacted:secret:api_key>"
    assert redact_sensitive("other", mapping) == "other"
    nested = {"a": [{"b": "sk-live-9f3a-unique"}, "sk-live-9f3a-unique"], "c": 3}
    redacted = redact_sensitive(nested, mapping)
    assert redacted == {"a": [{"b": "<redacted:secret:api_key>"}, "<redacted:secret:api_key>"], "c": 3}


def test_u1199_redact_outputs_uses_sensitive_table(monkeypatch):
    monkeypatch.setenv("ATLAS_A3_TEST", "sk-live-9f3a-unique")
    graph = parse_graph(_sourced_graph(source="env", ref="ATLAS_A3_TEST"))
    state = initial_state(graph)
    state["outputs"] = {"node_1": {"result": {"msg": "sk-live-9f3a-unique", "ok": True}}}
    out = _redact_outputs(state)
    assert out["node_1"]["result"]["msg"] == "<redacted:env:ATLAS_A3_TEST>"
    assert out["node_1"]["result"]["ok"] is True

# -*- coding: utf-8 -*-
"""打包 V：LLM 语义分支录制回放脚本化（docs/83；U964–U969）。

全程无真实 LLM/网络：录制用固定假分类器跑一次标准 run_graph 取真实步骤，
再经录制回放端点验证 mock 回放的确定性与各边界。
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate
from atlas.llm.condition_classifier import (
    ConditionClassifyError,
    ScriptedConditionClassifier,
)
from atlas.recording.replay import build_condition_script, collect_steps

client = TestClient(app)


def _token(username: str, password: str) -> str:
    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


OPERATOR_A = {"Authorization": f"Bearer {_token('operator-a', 'operator123')}"}


class _FixedClassifier:
    def __init__(self, label: str):
        self.label = label

    def classify(self, *, branches, context_text, instruction, node_id=None):
        return self.label


def _graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "cond-1", "type": "condition", "name": "分流",
             "position": {"x": 2, "y": 0},
             "config": {
                 "conditionMode": "llm",
                 "branches": [
                     {"label": "愤怒投诉", "description": "客户强烈不满",
                      "target": "tool-a"},
                     {"label": "普通咨询", "description": "客户平和询问",
                      "target": "tool-b"},
                 ],
                 "defaultTarget": "tool-default",
             }},
            {"id": "tool-a", "type": "tool_call", "name": "A",
             "config": {"tool": "op-a"}},
            {"id": "tool-b", "type": "tool_call", "name": "B",
             "config": {"tool": "op-b"}},
            {"id": "tool-default", "type": "tool_call", "name": "默认",
             "config": {"tool": "op-default"}},
        ],
        "edges": [
            {"id": "e0", "source": "trigger-1", "target": "cond-1"},
            {"id": "e1", "source": "cond-1", "target": "tool-a"},
            {"id": "e2", "source": "cond-1", "target": "tool-b"},
            {"id": "e3", "source": "cond-1", "target": "tool-default"},
        ],
    }


@pytest.fixture()
def recording_store():
    store = tenant_registry.get("t1").recording_store
    for case in store.list():
        assert store.delete(case.id)
    yield store
    for case in store.list():
        store.delete(case.id)


def _record_case(store, *, label: str, executed_tool: str) -> str:
    graph = _graph()
    emit, take_steps = collect_steps()
    result = run_graph(
        parse_graph(graph),
        condition_classifier=_FixedClassifier(label),
        tool_mocks={executed_tool: {"result": f"mocked-{executed_tool}"}},
        emit=emit,
    )
    case = store.add(
        name=f"case-{label}",
        graph=graph,
        inputs=None,
        steps=take_steps(),
        status=result["status"],
    )
    return case.id


# ---- U964：ScriptedConditionClassifier ----


def test_scripted_returns_recorded_label():
    scripted = ScriptedConditionClassifier({"cond-1": "普通咨询"})
    assert (
        scripted.classify(
            branches=[], context_text="", instruction="", node_id="cond-1"
        )
        == "普通咨询"
    )


def test_scripted_returns_default_label():
    scripted = ScriptedConditionClassifier({"cond-1": "__default__"})
    assert (
        scripted.classify(
            branches=[], context_text="", instruction="", node_id="cond-1"
        )
        == "__default__"
    )


@pytest.mark.parametrize("node_id", [None, "cond-unknown"])
def test_scripted_unknown_node_raises(node_id):
    scripted = ScriptedConditionClassifier({"cond-1": "普通咨询"})
    with pytest.raises(ConditionClassifyError):
        scripted.classify(
            branches=[], context_text="", instruction="", node_id=node_id
        )


# ---- U965：build_condition_script ----


def test_build_condition_script_filters_llm_conditions(recording_store):
    case_id = _record_case(
        recording_store, label="普通咨询", executed_tool="tool-b"
    )
    classifier, nodes = build_condition_script(recording_store.get(case_id))
    assert classifier is not None
    assert nodes == ["cond-1"]
    assert (
        classifier.classify(
            branches=[], context_text="", instruction="", node_id="cond-1"
        )
        == "普通咨询"
    )


def test_build_condition_script_skips_rule_conditions(recording_store):
    graph = _graph()
    config = graph["nodes"][1]["config"]
    config.pop("conditionMode")
    config["branches"] = [
        {"label": "大额", "expression": "{{global.amount}} > 100",
         "target": "tool-a"},
        {"label": "小额", "expression": "{{global.amount}} <= 100",
         "target": "tool-b"},
    ]
    graph["variables"] = [
        {"name": "amount", "type": "number", "value": "0", "scope": "global"}
    ]
    emit, take_steps = collect_steps()
    run_graph(
        parse_graph(graph),
        condition_classifier=_FixedClassifier("unused"),
        tool_mocks={"tool-b": {"result": "mocked"}},
        inputs={"amount": 10},
        emit=emit,
    )
    case = recording_store.add(
        name="rule-case",
        graph=graph,
        inputs={"amount": 10},
        steps=take_steps(),
        status="completed",
    )
    assert build_condition_script(case) == (None, [])


def test_build_condition_script_empty_without_llm_steps():
    from atlas.recording.cases import RecordingCase

    case = RecordingCase(
        id="rec-x",
        name="x",
        graph={},
        inputs=None,
        steps=[],
        status="completed",
        created_at="2026-09-29T00:00:00+00:00",
    )
    assert build_condition_script(case) == (None, [])


# ---- U966：mock 回放确定性重放 ----


def test_mock_replay_replays_recorded_branch_without_llm(
    recording_store, monkeypatch
):
    case_id = _record_case(
        recording_store, label="普通咨询", executed_tool="tool-b"
    )
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    import atlas.graph.loader as loader

    def _must_not_call():
        raise AssertionError("replay must not build a live classifier")

    monkeypatch.setattr(loader, "get_condition_classifier", _must_not_call)

    resp = client.post(
        f"/api/recordings/{case_id}/replay",
        json={"mock_tools": True},
        headers=OPERATOR_A,
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["matches"] is True
    assert report["replay_status"] == "completed"
    assert report["mocked_conditions"] == ["cond-1"]
    assert "tool-b" in report["mocked_tools"]


# ---- U967：录制即 __default__ ----


def test_mock_replay_default_label_routes_default(recording_store):
    case_id = _record_case(
        recording_store, label="__default__", executed_tool="tool-default"
    )
    resp = client.post(
        f"/api/recordings/{case_id}/replay",
        json={"mock_tools": True},
        headers=OPERATOR_A,
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["matches"] is True
    assert report["mocked_conditions"] == ["cond-1"]
    cond_row = next(
        row for row in report["steps"] if row["node_id"] == "cond-1"
    )
    assert cond_row["match"] is True


# ---- U968：录制后新增 LLM condition 节点 ----


def test_mock_replay_new_condition_node_drifts(recording_store):
    case_id = _record_case(
        recording_store, label="普通咨询", executed_tool="tool-b"
    )
    case = recording_store.get(case_id)
    changed = deepcopy(case.graph)
    changed["nodes"].append(
        {
            "id": "cond-2",
            "type": "condition",
            "name": "新增分流",
            "position": {"x": 1, "y": 0},
            "config": {
                "conditionMode": "llm",
                "branches": [
                    {"label": "新分支", "description": "新语义",
                     "target": "tool-a"}
                ],
                "defaultTarget": "cond-1",
            },
        }
    )
    changed["edges"] = [
        edge for edge in changed["edges"] if edge["id"] != "e0"
    ]
    changed["edges"].extend(
        [
            {"id": "e-new-1", "source": "trigger-1", "target": "cond-2"},
            {"id": "e-new-2", "source": "cond-2", "target": "cond-1"},
            {"id": "e-new-3", "source": "cond-2", "target": "tool-a"},
        ]
    )
    index = recording_store._items.index(case)
    recording_store._items[index] = case.model_copy(update={"graph": changed})

    resp = client.post(
        f"/api/recordings/{case_id}/replay",
        json={"mock_tools": True},
        headers=OPERATOR_A,
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["matches"] is False
    assert report["mocked_conditions"] == ["cond-1"]
    extra = next(
        row for row in report["steps"] if row["node_id"] == "cond-2"
    )
    assert extra["match"] is False
    assert "多出该节点" in extra["note"]


# ---- U969：非 mock 回放不注入脚本 ----


def test_live_replay_does_not_script(recording_store, monkeypatch):
    case_id = _record_case(
        recording_store, label="普通咨询", executed_tool="tool-b"
    )
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    resp = client.post(
        f"/api/recordings/{case_id}/replay", json={}, headers=OPERATOR_A
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["matches"] is False
    assert report["mocked_conditions"] == []
    assert report["mocked_tools"] == []
    cond_row = next(
        row for row in report["steps"] if row["node_id"] == "cond-1"
    )
    assert cond_row["match"] is False

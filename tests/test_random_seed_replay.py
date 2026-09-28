# -*- coding: utf-8 -*-
"""打包 W（docs/84）：随机函数＋回放种子化 U970–U973。"""

from __future__ import annotations

import random
import uuid

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.conditions import ConditionEvalError, evaluate_expression
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.iam.deps import tenant_registry


# ---------- U970：条件函数纯逻辑 ----------

def test_random_and_randint_match_stdlib_sequence():
    rng = random.Random(7)
    expected = [rng.random() for _ in range(4)]
    rng = random.Random(7)
    assert [evaluate_expression("random()", {}, rng=rng) for _ in range(4)] == expected

    rng = random.Random(7)
    expected_ints = [rng.randint(1, 100) for _ in range(4)]
    rng = random.Random(7)
    assert [
        evaluate_expression("randint(1, 100)", {}, rng=rng) for _ in range(4)
    ] == expected_ints


def test_same_seed_evaluates_equal_and_uuid_is_valid_v4():
    left = evaluate_expression("uuid()", {}, rng=random.Random(99))
    right = evaluate_expression("uuid()", {}, rng=random.Random(99))
    assert left == right
    parsed = uuid.UUID(left)
    assert parsed.version == 4


def test_randint_rejects_non_integer_bool_and_reversed_range():
    with pytest.raises(ConditionEvalError) as exc:
        evaluate_expression("randint(1.5, 3)", {}, rng=random.Random(1))
    assert exc.value.code == "COND_TYPE_MISMATCH"
    assert "参数必须是整数" in str(exc.value)

    with pytest.raises(ConditionEvalError) as exc:
        evaluate_expression("randint(1 == 1, 3)", {}, rng=random.Random(1))
    assert exc.value.code == "COND_TYPE_MISMATCH"

    with pytest.raises(ConditionEvalError) as exc:
        evaluate_expression("randint(3, 1)", {}, rng=random.Random(1))
    assert exc.value.code == "COND_INVALID_RANGE"
    assert "下界不能大于上界" in str(exc.value)


def test_random_functions_arity_errors():
    with pytest.raises(ConditionEvalError):
        evaluate_expression("random(1)", {})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("uuid(1)", {})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("randint(1)", {})


def test_random_functions_are_not_constant_folded():
    # 同一无变量表达式在各自独立 RNG 下必须逐次真正抽样：random() 永不折叠。
    rng = random.Random(3)
    values = {evaluate_expression("random()", {}, rng=rng) for _ in range(8)}
    assert len(values) > 1
    rng = random.Random(3)
    uuids = {evaluate_expression("uuid()", {}, rng=rng) for _ in range(8)}
    assert len(uuids) == 8


# ---------- 图夹具 ----------

def _random_branch_graph() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "condition-1", "type": "condition", "name": "随机分支",
             "config": {
                 "conditionMode": "rule",
                 "branches": [
                     {"label": "hit", "expression": "random() < 0.5", "target": "wait-a"},
                     {"label": "miss", "expression": "random() >= 0.5", "target": "wait-b"},
                 ],
                 "defaultTarget": "wait-c",
             }},
            {"id": "wait-a", "type": "wait", "name": "A",
             "config": {"waitType": "duration", "durationSeconds": 1}},
            {"id": "wait-b", "type": "wait", "name": "B",
             "config": {"waitType": "duration", "durationSeconds": 1}},
            {"id": "wait-c", "type": "wait", "name": "C",
             "config": {"waitType": "duration", "durationSeconds": 1}},
            {"id": "end-a", "type": "ai_decision", "name": "终态 A",
             "config": {"promptTemplate": "branch A terminal"}},
            {"id": "end-b", "type": "ai_decision", "name": "终态 B",
             "config": {"promptTemplate": "branch B terminal"}},
            {"id": "end-c", "type": "ai_decision", "name": "终态 C",
             "config": {"promptTemplate": "branch C terminal"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "condition-1"},
            {"id": "e2", "source": "condition-1", "target": "wait-a"},
            {"id": "e3", "source": "condition-1", "target": "wait-b"},
            {"id": "e4", "source": "condition-1", "target": "wait-c"},
            {"id": "e5", "source": "wait-a", "target": "end-a"},
            {"id": "e6", "source": "wait-b", "target": "end-b"},
            {"id": "e7", "source": "wait-c", "target": "end-c"},
        ],
    }


# ---------- U971：run_graph 种子 ----------

def test_same_rng_seed_produces_identical_outputs():
    graph = parse_graph(_random_branch_graph())
    first = run_graph(graph, rng_seed=12345)
    second = run_graph(graph, rng_seed=12345)
    assert first["outputs"] == second["outputs"]
    assert first["rng_seed"] == second["rng_seed"] == 12345


def test_missing_seed_generates_distinct_seeds():
    graph = parse_graph(_random_branch_graph())
    first = run_graph(graph)
    second = run_graph(graph)
    assert first["rng_seed"] and second["rng_seed"]
    assert first["rng_seed"] != second["rng_seed"]


def test_different_seeds_can_take_different_branches():
    graph = _random_branch_graph()
    branches = set()
    for seed in range(1, 40):
        result = run_graph(parse_graph(graph), rng_seed=seed)
        branches.add(result["outputs"]["condition-1"]["branch"])
    assert {"hit", "miss"} <= branches


# ---------- U972：子图共享随机流 ----------

def _child_graph() -> dict:
    graph = _random_branch_graph()
    graph["nodes"] = graph["nodes"][1:]
    graph["edges"] = graph["edges"][1:]
    return graph


def test_subgraph_reuses_expression_rng_stream():
    child_raw = _child_graph()
    child = parse_graph(child_raw)
    parent_raw = {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "subgraph-1", "type": "subgraph", "name": "子图",
             "config": {"graphId": "child-1", "inputs": {}}},
            {"id": "end-node", "type": "ai_decision", "name": "收尾",
             "config": {"promptTemplate": "parent terminal"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "subgraph-1"},
            {"id": "e2", "source": "subgraph-1", "target": "end-node"},
        ],
    }
    parent = parse_graph(parent_raw)

    def resolver(_ref: str):
        return child

    first = run_graph(parent, rng_seed=2026, graph_resolver=resolver)
    second = run_graph(parent, rng_seed=2026, graph_resolver=resolver)
    assert first["outputs"] == second["outputs"]
    child_branch = first["outputs"]["subgraph-1"]["outputs"]["condition-1"]["branch"]
    # 子图内部确有随机分支被求值（不是空跑），且同种子可复现。
    assert child_branch in {"hit", "miss"}


# ---------- U973：录制回放种子锚定（API 全链路） ----------

client = TestClient(app)


@pytest.fixture()
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    tenant_registry.get("t1").recording_store._items.clear()
    yield
    client.headers.pop("authorization", None)


def _steps_from_outputs(graph: dict, outputs: dict) -> list[dict]:
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    return [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in outputs.items()
    ]


def _record_run(graph_id: str, graph: dict, *, with_seed: bool) -> dict:
    run = client.post(f"/api/graphs/{graph_id}/run")
    assert run.status_code == 200, run.text
    result = run.json()
    payload: dict = {
        "name": "随机分支用例",
        "graph_id": graph_id,
        "inputs": {},
        "steps": _steps_from_outputs(graph, result["outputs"]),
        "status": result["status"],
    }
    if with_seed:
        payload["rng_seed"] = result["rng_seed"]
    response = client.post("/api/recordings", json=payload)
    assert response.status_code == 201, response.text
    return {"recording": response.json(), "seed": result["rng_seed"]}


def test_seeded_recording_replay_matches_and_gate_passes(_admin_session):
    graph = _random_branch_graph()
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    bundle = _record_run(graph_id, graph, with_seed=True)
    case_id = bundle["recording"]["id"]
    assert bundle["recording"]["rng_seed"] == bundle["seed"]

    report = client.post(f"/api/recordings/{case_id}/replay").json()
    assert report["matches"] is True
    assert "rng_seed_note" not in report
    assert report["mocked_conditions"] == []

    gate = client.post(f"/api/graphs/{graph_id}/release-gate").json()
    assert gate["blocked"] is False and gate["passed"] == 1
    assert "rng_seed_note" not in gate["cases"][0]


def test_historical_case_without_seed_flags_note(_admin_session):
    graph = _random_branch_graph()
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    bundle = _record_run(graph_id, graph, with_seed=False)
    case_id = bundle["recording"]["id"]
    assert bundle["recording"]["rng_seed"] is None

    report = client.post(f"/api/recordings/{case_id}/replay").json()
    assert report["rng_seed_note"]
    assert "rng_seed" in report["rng_seed_note"]


def test_mock_replay_replays_seeded_branches(_admin_session):
    graph = _random_branch_graph()
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    bundle = _record_run(graph_id, graph, with_seed=True)
    case_id = bundle["recording"]["id"]

    report = client.post(
        f"/api/recordings/{case_id}/replay", json={"mock_tools": True}
    ).json()
    assert report["matches"] is True
    assert report["mocked_conditions"] == []
    assert "rng_seed_note" not in report

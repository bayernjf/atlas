"""L3 对拍夹具的后端侧守（M4 批 2 ⑥ / U41）。

夹具 frontend/src/lib/validation/__tests__/l3-fixtures.json 由
scripts/dev/generate_l3_fixtures.py 以后端 parse_graph 实跑生成；
前端 vitest（l3.test.ts）读同一份 JSON 断言 l3.ts 输出逐条一致。
本测试在后端侧重跑一遍，防止 dsl.py 规则演进后夹具未重新生成（对拍漂移）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "lib"
    / "validation"
    / "__tests__"
    / "l3-fixtures.json"
)

UNREACHABLE_MARK = "不可达（没有任何入边路径能到达它）"
CYCLE_MARK = "检测到非法循环依赖（循环只允许经循环节点的循环体回到自身）"


def _load_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


def _to_raw(case: dict) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {
                "id": node["id"],
                "type": node["type"],
                "name": node["id"],
                "position": {"x": 0, "y": 0},
                "config": node.get("config", {}),
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            }
            for node in case["nodes"]
        ],
        "edges": [
            {
                "id": f"e-{index}",
                "source": edge["source"],
                "target": edge["target"],
            }
            for index, edge in enumerate(case["edges"])
        ],
    }


def _structural_messages(messages: list[str]) -> list[str]:
    return [m for m in messages if UNREACHABLE_MARK in m or CYCLE_MARK in m]


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_l3_fixture_matches_backend(case: dict):
    backend = case["backend"]
    assert backend["structural"] is True, "对拍夹具只收录进入结构域的用例"
    try:
        parse_graph(_to_raw(case))
        structural: list[str] = []
    except GraphValidationError as exc:
        structural = _structural_messages(exc.errors)
    assert structural == backend["messages"], (
        f"夹具 {case['name']} 与后端实跑不一致；重跑 scripts/dev/generate_l3_fixtures.py"
    )

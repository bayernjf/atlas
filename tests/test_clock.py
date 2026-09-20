"""C（docs/27 §2）：可注入回放时钟的端到端测试。

覆盖 run_graph(now_override=) 单次运行固定 UTC、条件分支随冻结时钟确定、
子图重入继承同一时钟、录制回放 clock_anchor（recorded_at 优先 / created_at 回退）
把回放冻结到用例录制时刻。纯表达式层测试见 test_conditions.py。
"""

import datetime as dt

import pytest

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.recording import (
    RecordStep,
    RecordingCase,
    RecordingStore,
    clock_anchor,
)

UTC = dt.timezone.utc
FIXED = dt.datetime(2026, 9, 19, 13, 30, tzinfo=UTC)
# 以 2026-09-19 00:00 UTC 为界的 now() 条件：固定时刻在其后（命中），过去时刻在其前（默认）。
GATE_EXPR = "now() >= datetime(2026,9,19,0,0)"
TODAY_EXPR = "today() == date(2026,9,19)"


def _clock_graph(expression: str):
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "cond", "type": "condition", "name": "时间门",
                 "config": {
                     "branches": [
                         {"label": "命中", "expression": expression, "target": "tool-yes"}
                     ],
                     "defaultTarget": "tool-no",
                 }},
                {"id": "tool-yes", "type": "tool_call", "name": "是",
                 "config": {"tool": "human-review"}},
                {"id": "tool-no", "type": "tool_call", "name": "否",
                 "config": {"tool": "auto-refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "cond"},
                {"id": "e2", "source": "cond", "target": "tool-yes"},
                {"id": "e3", "source": "cond", "target": "tool-no"},
            ],
        }
    )


def test_now_override_freezes_condition_branch():
    past = dt.datetime(2026, 9, 18, 23, 0, tzinfo=UTC)
    hit = run_graph(_clock_graph(GATE_EXPR), now_override=FIXED)
    miss = run_graph(_clock_graph(GATE_EXPR), now_override=past)
    assert "tool-yes" in hit["outputs"] and "tool-no" not in hit["outputs"]
    assert "tool-no" in miss["outputs"] and "tool-yes" not in miss["outputs"]
    assert hit["outputs"]["cond"]["branch"] == "命中"
    assert miss["outputs"]["cond"]["branch"] == "__default__"


def test_today_function_uses_frozen_clock():
    same_day = run_graph(_clock_graph(TODAY_EXPR), now_override=FIXED)
    other_day = run_graph(
        _clock_graph(TODAY_EXPR), now_override=dt.datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
    )
    assert "tool-yes" in same_day["outputs"]
    assert "tool-no" in other_day["outputs"]


def test_naive_now_override_is_treated_as_utc():
    # naive datetime（无时区）按 UTC 处理，结果与等价 aware 时刻一致。
    naive = dt.datetime(2026, 9, 19, 13, 30)  # 视为 13:30 UTC
    result = run_graph(_clock_graph(GATE_EXPR), now_override=naive)
    assert "tool-yes" in result["outputs"]


def test_subgraph_inherits_same_frozen_clock():
    child = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "c-cond", "type": "condition", "name": "子图时间门",
                 "config": {
                     "branches": [
                         {"label": "命中", "expression": GATE_EXPR, "target": "c-yes"}
                     ],
                     "defaultTarget": "c-no",
                 }},
                {"id": "c-yes", "type": "tool_call", "name": "子是",
                 "config": {"tool": "human-review"}},
                {"id": "c-no", "type": "tool_call", "name": "子否",
                 "config": {"tool": "auto-refund"}},
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "c-cond"},
                {"id": "ce2", "source": "c-cond", "target": "c-yes"},
                {"id": "ce3", "source": "c-cond", "target": "c-no"},
            ],
        }
    )
    parent = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": "g-child", "inputs": {}}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after", "params": "done"}},
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )
    # 冻结在未来 → 子图内 now() 条件命中 c-yes
    hit = run_graph(
        parent, graph_id="g-parent",
        graph_resolver={"g-child": child}.get, now_override=FIXED,
    )
    child_out = hit["outputs"]["subgraph-1"]["outputs"]
    assert hit["outputs"]["subgraph-1"]["status"] == "success"
    assert "c-yes" in child_out and "c-no" not in child_out

    # 冻结在过去 → 子图走默认 c-no，证明子图用的是父 run 同一冻结时钟，而非真实时钟
    past = dt.datetime(2026, 9, 18, 0, 0, tzinfo=UTC)
    miss = run_graph(
        parent, graph_id="g-parent",
        graph_resolver={"g-child": child}.get, now_override=past,
    )
    child_out_miss = miss["outputs"]["subgraph-1"]["outputs"]
    assert "c-no" in child_out_miss and "c-yes" not in child_out_miss


def _case(*, recorded_at, created_at="2026-09-19T13:30:00+00:00"):
    return RecordingCase(
        id="rec-1", name="时钟用例", graph={}, inputs=None,
        steps=[RecordStep(node_id="n1", node_type="trigger", output={})],
        status="completed", created_at=created_at, recorded_at=recorded_at,
    )


def test_clock_anchor_prefers_recorded_at():
    case = _case(recorded_at="2026-09-10T08:00:00+00:00")
    anchor, note = clock_anchor(case)
    assert anchor == dt.datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    assert note is None


def test_clock_anchor_falls_back_to_created_at():
    # 历史用例无 recorded_at → 回退 created_at
    case = _case(recorded_at=None, created_at="2026-09-10T08:00:00+00:00")
    anchor, note = clock_anchor(case)
    assert anchor == dt.datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    assert note is None


def test_clock_anchor_naive_utc_and_missing():
    # naive ISO 串按 UTC
    case = _case(recorded_at="2026-09-10T08:00:00")
    anchor, note = clock_anchor(case)
    assert anchor == dt.datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    # 两者都缺 / 非法 → (None, note)，调用方退回真实时钟
    missing = _case(recorded_at=None, created_at="")
    anchor_m, note_m = clock_anchor(missing)
    assert anchor_m is None and note_m
    bad = _case(recorded_at="not-a-time")
    anchor_b, note_b = clock_anchor(bad)
    assert anchor_b is None and note_b


def test_replay_freezes_clock_to_case_recorded_at():
    """模拟单用例回放：clock_anchor 取用例录制时刻并注入 run_graph，
    使含 now() 条件的图在历史锚点下走与录制时一致的分支，而非按真实当前时间。"""
    # 用例录制于 2026-09-10（门槛 2026-09-19 之前），回放应稳定走默认支 tool-no。
    case = _case(recorded_at="2026-09-10T12:00:00+00:00")
    anchor, note = clock_anchor(case)
    assert note is None
    replayed = run_graph(_clock_graph(GATE_EXPR), now_override=anchor)
    assert "tool-no" in replayed["outputs"] and "tool-yes" not in replayed["outputs"]


def test_recording_store_stamps_recorded_at_on_add():
    store = RecordingStore()
    case = store.add(
        name="c", graph={"nodes": []}, inputs=None,
        steps=[RecordStep(node_id="n1", node_type="trigger", output={})],
        status="completed",
    )
    # 新用例入库即生成 recorded_at，且与 created_at 同刻
    assert case.recorded_at == case.created_at
    # 显式传入则保留（baseline 锚点）
    case2 = store.add(
        name="c2", graph={"nodes": []}, inputs=None,
        steps=[RecordStep(node_id="n1", node_type="trigger", output={})],
        status="completed", recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert case2.recorded_at == "2026-01-01T00:00:00+00:00"

# -*- coding: utf-8 -*-
"""docs/61 §5 H4：影子运行 PG 化集成测试（候选 U791–U806）。

仅当 ATLAS_RUN_INTEGRATION=1 且 DATABASE_URL 可用时跑；迁移 glob 自动 apply 到 028。
核心不是「能不能落库」，而是**两档投影逐键一致**：add/list/get/attach_outcome 的返回
都必须是同一个 ShadowRun.model_dump()，这样 REST 四端点与前端才真的零改动。
另覆盖跨 store 实例（模拟重启）可见、ring 100 惰性裁剪、reset 只清本租户。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from atlas.recording.pg_shadow import PgShadowStore
from atlas.recording.shadow import (
    SHADOW_RING_SIZE,
    HumanOutcome,
    ShadowDecision,
    ShadowStore,
    ToolIntent,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run shadow run PG integration",
)


def _intents(*, with_write: bool = True) -> list[ToolIntent]:
    intents = [
        ToolIntent(
            node_id="tool-1",
            # 分类器按工具名归一动作（shadow.py `_refund_class`）：execute_refund → refunded。
            tool="shop/execute_refund",
            permission="financial",
            dry_run=True,
            parameters={"amount": 299, "order": "12345"},
            action_status="SHADOW_DRY_RUN",
        )
    ]
    if not with_write:
        intents = [
            ToolIntent(
                node_id="tool-0",
                tool="shop/list_orders",
                permission="read",
                dry_run=False,
                parameters=None,
                action_status="SUCCESS",
            )
        ]
    return intents


def _decisions() -> list[ShadowDecision]:
    return [ShadowDecision(node_id="cond-1", node_type="condition", target="tool-1")]


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["DATABASE_URL"])
    migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
        statements, current = [], []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            current.append(line)
            if stripped.endswith(";"):
                statements.append("\n".join(current))
                current = []
        with eng.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    yield eng
    eng.dispose()


@pytest.fixture()
def tiers(engine):
    """同一批入参分别喂内存档与 PG 档；PG 用独立租户并在结束后清掉。"""
    tenant = f"pgshadow-{uuid.uuid4().hex[:8]}"
    memory = ShadowStore()
    pg = PgShadowStore(engine, tenant)

    def make():
        return PgShadowStore(engine, tenant)

    yield memory, pg, make
    pg.reset()


def _add(store, **overrides):
    args = {
        "graph_id": "g-1",
        "trace_id": "trace-1",
        "decisions": _decisions(),
        "tool_intents": _intents(),
        "inputs": {"refundId": "12345"},
        "status": "completed",
    }
    args.update(overrides)
    return store.add(**args)


def test_add_projection_matches_memory_tier(tiers):
    memory, pg, _make = tiers
    mem_run = _add(memory)
    pg_run = _add(pg)
    # id/created_at 天然不同，其余逐键一致（含四子模型与 auto_action/comparison）。
    assert {k: v for k, v in pg_run.items() if k not in ("id", "created_at")} == {
        k: v for k, v in mem_run.items() if k not in ("id", "created_at")
    }
    assert pg_run["auto_action"] == mem_run["auto_action"] == "refunded"
    # 创建时未带人工结果 → match=None（尚未补录，无法判定），不是 True/False。
    assert pg_run["comparison"]["match"] is None
    assert pg_run["comparison"] == mem_run["comparison"]
    assert pg_run["tool_intents"][0]["parameters"] == {"amount": 299, "order": "12345"}


def test_null_inputs_stays_null_across_tiers(tiers):
    """inputs 缺省在两档都必须是 None，落成 {} 会让前端读到空对象。"""
    memory, pg, _make = tiers
    assert _add(memory, inputs=None)["inputs"] is None
    assert _add(pg, inputs=None)["inputs"] is None


def test_read_back_submodels_after_restart(tiers):
    _memory, pg, make = tiers
    run = _add(pg)
    fresh = make()  # 模拟重启：新实例、同一库
    stored = fresh.get(run["id"])
    assert stored == run
    assert stored["decisions"][0]["node_type"] == "condition"
    assert stored["human_outcome"] is None


def test_list_desc_graph_filter_and_clamp(tiers):
    _memory, pg, _make = tiers
    first = _add(pg, graph_id="g-a")
    second = _add(pg, graph_id="g-b")
    assert [i["id"] for i in pg.list()] == [second["id"], first["id"]]  # 倒序
    assert [i["id"] for i in pg.list("g-a")] == [first["id"]]  # 按图过滤
    assert [i["id"] for i in pg.list("missing")] == []
    assert len(pg.list(None, 0)) == 1  # clamp 下界
    assert len(pg.list(None, 999)) == 2  # 上界 200，未超即全给


def test_get_unknown_returns_none(tiers):
    _memory, pg, _make = tiers
    assert pg.get("sr-does-not-exist") is None


def test_attach_outcome_recomputes_comparison(tiers):
    memory, pg, make = tiers
    mem_run = _add(memory)
    pg_run = _add(pg)
    outcome = HumanOutcome(action="human_review", note="转人工")

    mem_updated = memory.attach_outcome(mem_run["id"], outcome)
    pg_updated = pg.attach_outcome(pg_run["id"], outcome)

    assert pg_updated["human_outcome"] == outcome.model_dump()
    assert pg_updated["comparison"]["match"] is False
    assert {k: v for k, v in pg_updated.items() if k not in ("id", "created_at")} == {
        k: v for k, v in mem_updated.items() if k not in ("id", "created_at")
    }
    # 覆盖式补录：再记一次不产生第二条历史
    again = make().attach_outcome(pg_run["id"], HumanOutcome(action="refunded"))
    assert again["comparison"]["match"] is True
    assert len(make().list()) == len(pg.list())
    assert pg.attach_outcome("sr-nope", outcome) is None


def test_ring_evicts_oldest_in_database(tiers):
    _memory, pg, make = tiers
    ids = [_add(pg)["id"] for _ in range(SHADOW_RING_SIZE + 5)]
    listed = make().list(None, 200)
    assert len(listed) == SHADOW_RING_SIZE
    assert ids[0] not in {item["id"] for item in listed}  # 最旧五条被挤出
    assert ids[-1] == listed[0]["id"]


def test_reset_only_clears_own_tenant(engine, tiers):
    _memory, pg, make = tiers
    other_tenant = f"{pg._tenant_id}-other"
    other = PgShadowStore(engine, other_tenant)
    _add(pg)
    other.add(
        graph_id="g-x",
        trace_id="t-x",
        decisions=[],
        tool_intents=_intents(with_write=False),
    )
    pg.reset()
    assert make().list() == []
    assert len(other.list()) == 1
    other.reset()


def test_read_only_intent_has_no_auto_action(tiers):
    """纯 READ 透传：两档都算不出系统动作，comparison.match 应为 None（无法判定）。"""
    memory, pg, _make = tiers
    mem_run = _add(memory, tool_intents=_intents(with_write=False), decisions=[])
    pg_run = _add(pg, tool_intents=_intents(with_write=False), decisions=[])
    assert pg_run["comparison"]["match"] == mem_run["comparison"]["match"] is None
    assert pg_run["tool_intents"][0]["parameters"] == mem_run["tool_intents"][0]["parameters"] is None


def test_schema_present_for_new_install(engine):
    from sqlalchemy import inspect

    assert "shadow_runs" in inspect(engine).get_table_names()

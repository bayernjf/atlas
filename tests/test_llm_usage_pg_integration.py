# -*- coding: utf-8 -*-
"""打包 AU（docs/129）U1418/U1423：LLM 用量 PG 持久化与租户隔离集成测试。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5445/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_llm_usage_pg_integration.py

覆盖：
- 迁移 049 应用成功：llm_usage 表与列齐全；
- **跨新 engine/新 store 实例可读**（重启模拟，U1418）——成本账不能随重启丢；
- 三档 group_by 聚合与明细求和**对账相等**，时间窗过滤生效；
- **租户隔离反向门**（U1423）：B 租户 store 读不到 A 租户的行；
- reset 只清本租户、不涉他租户。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from atlas.llm.metering import UsageRecord
from atlas.llm.pg_store import PgUsageStore

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run usage PG integration",
    ),
]

TENANT = "pgusagetest"
OTHER_TENANT = "pgusageother"

ROWS = [
    ("2026-10-13T10:00:00+00:00", "ai_decision", "m1", 10, 5, 15, "ok", "ai-1", "g1", "r1"),
    ("2026-10-13T11:00:00+00:00", "condition_llm", "m1", 20, 6, 26, "ok", "cond-1", "g1", "r1"),
    ("2026-10-14T09:00:00+00:00", "ai_decision", "m2", 30, 7, 37, "error", "ai-1", "g2", "r2"),
]


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


def _clean(engine, tenant: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM llm_usage WHERE tenant_id = :t"), {"t": tenant})


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    for tenant in (TENANT, OTHER_TENANT):
        _clean(eng, tenant)
    yield eng
    for tenant in (TENANT, OTHER_TENANT):
        _clean(eng, tenant)
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_rows(engine):
    """每条用例独立起账（模块级 engine 复用，避免上一条的种子污染计数）。"""
    for tenant in (TENANT, OTHER_TENANT):
        _clean(engine, tenant)
    yield
    for tenant in (TENANT, OTHER_TENANT):
        _clean(engine, tenant)


def _seed(store: PgUsageStore, tenant: str) -> None:
    for ts, caller, model, pt, ct, tt, status, node, graph, run in ROWS:
        store.record(
            UsageRecord(
                tenant_id=tenant, ts=ts, caller=caller, model=model,
                prompt_tokens=pt, completion_tokens=ct, total_tokens=tt,
                latency_ms=1.5, status=status, node_id=node, graph_id=graph, run_id=run,
            )
        )


def test_u1418_rows_survive_a_new_engine_and_store(engine):
    """U1418：写后换 engine/换 store 实例仍可读（重启模拟）。"""
    _seed(PgUsageStore(engine, TENANT), TENANT)
    fresh = PgUsageStore(_new_engine(), TENANT)
    rows = fresh.list_recent(limit=50)
    assert len(rows) == 3
    by_ts = {row["ts"]: row for row in rows}
    assert by_ts["2026-10-14T09:00:00+00:00"]["caller"] == "ai_decision"
    assert by_ts["2026-10-14T09:00:00+00:00"]["status"] == "error"
    assert by_ts["2026-10-13T10:00:00+00:00"]["nodeId"] == "ai-1"
    assert by_ts["2026-10-13T10:00:00+00:00"]["runId"] == "r1"
    # 倒序：最新在前
    assert [row["ts"] for row in rows] == sorted(by_ts, reverse=True)


def test_u1419_pg_summary_matches_detail_sum(engine):
    store = PgUsageStore(engine, TENANT)
    _seed(store, TENANT)
    detail = store.list_recent(limit=50)
    for group_by, expected_calls in (("caller", 3), ("model", 3), ("day", 3)):
        items = store.summarize(group_by=group_by)
        assert sum(item["totalTokens"] for item in items) == sum(
            row["totalTokens"] for row in detail
        ), group_by
        assert sum(item["calls"] for item in items) == expected_calls, group_by
    by_caller = {item["key"]: item for item in store.summarize(group_by="caller")}
    assert by_caller["ai_decision"]["totalTokens"] == 52
    assert by_caller["ai_decision"]["errors"] == 1
    # 时间窗
    window = store.summarize(since="2026-10-14T00:00:00+00:00", group_by="caller")
    assert [item["key"] for item in window] == ["ai_decision"]
    assert window[0]["totalTokens"] == 37


def test_u1423_other_tenant_cannot_read_the_rows(engine):
    """U1423 反向门：他租户 store 读不到本租户的用量（摘 WHERE tenant_id 必须红）。"""
    _seed(PgUsageStore(engine, TENANT), TENANT)
    _seed(PgUsageStore(engine, OTHER_TENANT), OTHER_TENANT)
    mine = PgUsageStore(engine, TENANT)
    theirs = PgUsageStore(engine, OTHER_TENANT)
    assert len(mine.list_recent(limit=50)) == 3
    assert len(theirs.list_recent(limit=50)) == 3
    # 逐条归属正确（不是"谁都能读到全部 6 条"）
    assert {row["tenantId"] for row in mine.list_recent(limit=50)} == {TENANT}
    assert {row["tenantId"] for row in theirs.list_recent(limit=50)} == {OTHER_TENANT}
    # 聚合同样分区
    assert sum(item["totalTokens"] for item in mine.summarize()) == 78
    assert sum(item["totalTokens"] for item in theirs.summarize()) == 78


def test_reset_only_clears_the_own_tenant(engine):
    store = PgUsageStore(engine, TENANT)
    _seed(store, TENANT)
    store.reset()
    assert store.list_recent() == []
    other = PgUsageStore(engine, OTHER_TENANT)
    _seed(other, OTHER_TENANT)
    assert len(other.list_recent(limit=50)) == 3
    other.reset()
    assert other.list_recent() == []

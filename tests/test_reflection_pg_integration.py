# -*- coding: utf-8 -*-
"""打包 ZU（docs/94）U1120：反思报告/候选 PG 持久化集成测试。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5433/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_reflection_pg_integration.py

覆盖（docs/94 §4 U1120）：
- 迁移 041 应用成功：reflection_reports/reflection_candidates 两表与列齐全；
- reports/candidates/decision 落库后**跨新 engine/新 store 实例可读**（重启模拟）；
- PgReflectionStore 与进程内 ReflectionStore **两档投影逐键对拍一致**（含 node_id、
  decision_status）；
- 候选 id 走全局 storage_id_seq，重启后不复用、不回 refl-1；
- ring 惰性裁 100（candidates/reports 各超 100 只留最近 100）；
- reset 只清本租户、不涉他租户。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from atlas.reflection import ReflectionCandidate, ReflectionReport, ReflectionStore
from atlas.reflection.candidate import Change
from atlas.reflection.pg_store import PgReflectionStore

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run reflection PG integration",
    ),
]

TENANT = "pgreflecttest"
OTHER_TENANT = "pgreflectother"
GRAPH = "g-reflect-pg"
STAMP = "2026-10-04T00:00:00+00:00"
STAMP2 = "2026-10-05T00:00:00+00:00"


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM reflection_reports WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT, "b": OTHER_TENANT})
        conn.execute(text("DELETE FROM reflection_candidates WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT, "b": OTHER_TENANT})
    yield eng
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM reflection_reports WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT, "b": OTHER_TENANT})
        conn.execute(text("DELETE FROM reflection_candidates WHERE tenant_id IN (:a, :b)"),
                     {"a": TENANT, "b": OTHER_TENANT})
    eng.dispose()


def _make_candidate(cid: str, *, decision: str | None = None) -> ReflectionCandidate:
    return ReflectionCandidate(
        candidate_id=cid,
        graph_id=GRAPH,
        base_version=3,
        changes=[
            Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.7,
                   reason="失败偏多", node_id="ai-1"),
            Change(param_key="approval_limit", from_value="500", to_value=300,
                   reason="转人工偏多", node_id=None),
        ],
        prompt_suggestions=["在 prompt 里补一句限额规则"],
        evidence_digest=f"{GRAPH}@3",
        generated_at=STAMP,
        decision_status=decision,
        decided_at=STAMP2 if decision else None,
    )


def _make_report(cid: str | None, *, status: str = "ok") -> ReflectionReport:
    return ReflectionReport(
        candidate_id=cid,
        graph_id=GRAPH,
        base_version=3,
        status=status,
        reasons=["产出 2 条参数建议"] if cid else ["无证据"],
        generated_at=STAMP,
    )


def test_u1120_migration_creates_two_tables(engine):
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
        }
        assert {"reflection_reports", "reflection_candidates"} <= tables

        report_cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='reflection_reports'"
                )
            )
        }
        assert {"tenant_id", "seq", "candidate_id", "graph_id", "base_version",
                "status", "reasons", "generated_at"} == report_cols

        cand_cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='reflection_candidates'"
                )
            )
        }
        assert {"tenant_id", "id", "seq", "graph_id", "base_version", "changes",
                "prompt_suggestions", "evidence_digest", "generated_at",
                "decision_status", "decided_at"} == cand_cols


def test_u1120_two_backend_projections_match_key_by_key(engine):
    """同一候选/报告落两档，get_candidate 与 list_reports 投影逐键一致（含 node_id/决策态）。"""
    mem = ReflectionStore()
    pg = PgReflectionStore(engine, TENANT)
    pg.reset()

    cid = "refl-777"
    mem.add_candidate(_make_candidate(cid))
    pg.add_candidate(_make_candidate(cid))
    mem.add_report(_make_report(cid))
    pg.add_report(_make_report(cid))
    mem.add_report(_make_report(None, status="no_evidence"))
    pg.add_report(_make_report(None, status="no_evidence"))

    # 未决策：get_candidate 投影逐键相等。
    assert pg.get_candidate(cid) == mem.get_candidate(cid)

    # 登记决策后两档仍逐键一致（含 decision_status/decided_at）。
    assert mem.record_decision(cid, "adopted", decided_at=STAMP2)
    assert pg.record_decision(cid, "adopted", decided_at=STAMP2)
    assert pg.get_candidate(cid) == mem.get_candidate(cid)

    # list_reports：candidate 行动态带 adopted，no_evidence 行为 None；两档逐键相等。
    mem_rows = mem.list_reports(graph_id=GRAPH)
    pg_rows = pg.list_reports(graph_id=GRAPH)
    assert pg_rows == mem_rows
    ok_row = next(r for r in pg_rows if r["status"] == "ok")
    assert ok_row["decision_status"] == "adopted"
    no_ev = next(r for r in pg_rows if r["status"] == "no_evidence")
    assert no_ev["decision_status"] is None

    # 改判覆盖后仍一致。
    mem.record_decision(cid, "dismissed", decided_at=STAMP)
    pg.record_decision(cid, "dismissed", decided_at=STAMP)
    assert pg.get_candidate(cid) == mem.get_candidate(cid)
    assert pg.list_reports(graph_id=GRAPH) == mem.list_reports(graph_id=GRAPH)
    pg.reset()


def test_u1120_persistence_survives_new_engine_and_store(engine):
    """写库后换新 engine/新 store 实例（重启模拟）仍可读候选、报告与决策态。"""
    pg = PgReflectionStore(engine, TENANT)
    pg.reset()
    cid = pg.next_candidate_id()
    pg.add_candidate(_make_candidate(cid, decision="adopted"))
    pg.add_report(_make_report(cid))
    last_seq_num = int(cid.split("-")[1])

    # 全新 engine + 全新 store 实例。
    fresh_engine = _new_engine()
    try:
        reborn = PgReflectionStore(fresh_engine, TENANT)
        candidate = reborn.get_candidate(cid)
        assert candidate is not None
        assert candidate["decision_status"] == "adopted"
        assert candidate["changes"][0]["node_id"] == "ai-1"
        reports = reborn.list_reports(graph_id=GRAPH)
        assert any(r["candidate_id"] == cid and r["decision_status"] == "adopted" for r in reports)

        # id 走持久化 storage_id_seq：重启后取号严格大于重启前，不回 refl-1。
        next_id = reborn.next_candidate_id()
        assert int(next_id.split("-")[1]) > last_seq_num
    finally:
        fresh_engine.dispose()
    pg.reset()


def test_u1120_ring_is_lazily_trimmed_to_100(engine):
    """candidates/reports 各超 100 条后只留最近 100（与内存 deque maxlen 对齐）。"""
    pg = PgReflectionStore(engine, TENANT)
    pg.reset()
    first_cid = None
    for i in range(105):
        cid = pg.next_candidate_id()
        if first_cid is None:
            first_cid = cid
        cand = ReflectionCandidate(
            candidate_id=cid, graph_id=GRAPH, base_version=1,
            changes=[Change(param_key="approval_limit", from_value="500", to_value=300)],
            generated_at=STAMP,
        )
        pg.add_candidate(cand)
        pg.add_report(ReflectionReport(
            candidate_id=cid, graph_id=GRAPH, base_version=1, status="ok",
            reasons=[], generated_at=STAMP,
        ))

    assert len(pg.list_reports(limit=200)) == 100
    # 最早的候选已被裁掉。
    assert pg.get_candidate(first_cid) is None
    with engine.connect() as conn:
        cand_count = conn.execute(
            text("SELECT count(*) FROM reflection_candidates WHERE tenant_id=:t"),
            {"t": TENANT},
        ).scalar_one()
        report_count = conn.execute(
            text("SELECT count(*) FROM reflection_reports WHERE tenant_id=:t"),
            {"t": TENANT},
        ).scalar_one()
    assert cand_count == 100
    assert report_count == 100
    pg.reset()


def test_u1120_record_decision_unknown_returns_false_and_reset_is_tenant_scoped(engine):
    pg = PgReflectionStore(engine, TENANT)
    other = PgReflectionStore(engine, OTHER_TENANT)
    pg.reset()
    other.reset()

    # 不存在/跨租户返 False。
    assert pg.record_decision("refl-999999", "adopted") is False

    cid_a = pg.next_candidate_id()
    pg.add_candidate(_make_candidate(cid_a))
    cid_b = other.next_candidate_id()
    other.add_candidate(_make_candidate(cid_b))

    # 跨租户取不到对方候选（404 语义）。
    assert pg.get_candidate(cid_b) is None
    assert other.get_candidate(cid_a) is None

    # reset 只清本租户。
    pg.reset()
    assert pg.get_candidate(cid_a) is None
    assert other.get_candidate(cid_b) is not None
    other.reset()

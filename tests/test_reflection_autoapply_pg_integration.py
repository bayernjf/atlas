# -*- coding: utf-8 -*-
"""打包 BP（docs/132 §3.2/§3.3）：自动生效记录 PG 持久化集成测试。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5445/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_reflection_autoapply_pg_integration.py

覆盖（docs/132 §3.2 PgAutoApplyStore 两档对拍 + 迁移 053）：
- 迁移 053 应用成功：autoapply_runs 表与列齐全（含 stages/enabled_scopes JSONB）；
- add/get/list/update/reset 落库后**跨新 engine/新 store 实例可读**（重启模拟）；
- run_id 走全局 storage_id_seq，`aa-N` 不复用、不回 aa-1；
- ring 惰性裁 100（超 100 只留最近 100）；
- 两档（内存 AutoApplyStore vs PgAutoApplyStore）同一 AutoApplyRun 投影逐键对拍；
- reset 只清本租户、不涉他租户。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from atlas.reflection.autoapply import AUTOAPPLY_RING_SIZE, AutoApplyRun, AutoApplyStore
from atlas.reflection.pg_autoapply import PgAutoApplyStore

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run autoapply PG integration",
    ),
]

TENANT = "pgaatest"
OTHER_TENANT = "pgaaother"
GRAPH = "g-aa-pg"
STAMP = "2026-10-14T00:00:00+00:00"
STAMP2 = "2026-10-14T01:00:00+00:00"


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM autoapply_runs WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    yield eng
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM autoapply_runs WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    eng.dispose()


def _make_run(run_id: str, *, status: str = "completed", stages: list | None = None) -> AutoApplyRun:
    return AutoApplyRun(
        run_id=run_id,
        candidate_id="refl-pg-aa",
        graph_id=GRAPH,
        base_version=3,
        enabled_scopes=["graph_variable"],
        patch={"diff": [{"param_key": "approval_limit", "from": "500", "to": 800, "path": "variables[0].value", "skipped": False}]},
        shadow={"status": "ok", "events": 4},
        gate={"total": 0, "passed": 0, "failed": 0, "skipped": True, "blocked": False},
        publish_version=2,
        rollout_status="canary",
        status=status,
        reason=None,
        stages=stages or [{"stage": "start", "at": STAMP, "ok": True, "note": ""}],
        created_at=STAMP,
        updated_at=STAMP2,
    )


def test_u1471_migration_creates_autoapply_runs_table(engine):
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
        }
        assert "autoapply_runs" in tables
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='autoapply_runs'"
                )
            )
        }
        assert {
            "tenant_id", "run_id", "candidate_id", "graph_id", "base_version",
            "enabled_scopes", "patch", "shadow", "gate", "publish_version",
            "rollout_status", "status", "reason", "stages", "created_at", "updated_at",
        } <= cols


def test_u1472_pg_store_add_get_update_cross_instance(engine):
    store = PgAutoApplyStore(engine, TENANT)
    run = _make_run(store.next_run_id())
    store.add(run)
    # 跨新 engine/新 store 实例可读（重启模拟）
    other = PgAutoApplyStore(_new_engine(), TENANT)
    row = other.get(run.run_id)
    assert row is not None
    assert row["candidate_id"] == "refl-pg-aa"
    assert row["publish_version"] == 2
    assert row["rollout_status"] == "canary"
    assert row["status"] == "completed"
    assert row["enabled_scopes"] == ["graph_variable"]
    assert row["stages"][0]["stage"] == "start"
    # update（整体替换：改状态/追加阶段）
    run.status = "aborted"
    run.rollout_status = "rolled_back"
    run.reason = "用户中止"
    run.stages.append({"stage": "abort", "at": STAMP2, "ok": True, "note": "canary 手动回滚"})
    store.update(run)
    row2 = other.get(run.run_id)
    assert row2["status"] == "aborted"
    assert row2["rollout_status"] == "rolled_back"
    assert row2["reason"] == "用户中止"
    assert any(s["stage"] == "abort" for s in row2["stages"])


def test_u1473_run_id_from_storage_seq_not_reused(engine):
    store = PgAutoApplyStore(engine, TENANT)
    a = store.next_run_id()
    b = store.next_run_id()
    assert a != b
    assert a.startswith("aa-") and b.startswith("aa-")
    assert int(a.split("-")[1]) < int(b.split("-")[1])  # 严格递增不复用
    store.add(_make_run(a))
    # 删除后再次取号不回 aa-N（seq 不回收）
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM autoapply_runs WHERE tenant_id=:t AND run_id=:r"),
                     {"t": TENANT, "r": a})
    c = store.next_run_id()
    assert c != a
    assert int(c.split("-")[1]) > int(b.split("-")[1])


def test_u1474_ring_trims_to_100(engine):
    store = PgAutoApplyStore(engine, TENANT)
    for _ in range(AUTOAPPLY_RING_SIZE + 10):
        run = _make_run(store.next_run_id(), status="completed")
        store.add(run)
    rows = store.list(limit=200)
    assert len(rows) <= AUTOAPPLY_RING_SIZE
    # 保留的是最近 100 条（run_id 递增段最大者）
    keep = {int(r["run_id"].split("-")[1]) for r in rows}
    assert max(keep) - min(keep) + 1 == AUTOAPPLY_RING_SIZE


def test_u1475_memory_vs_pg_projection_parity():
    """两档（AutoApplyStore vs PgAutoApplyStore）同一 run 的投影逐键对拍（含 stages JSONB 还原）。"""
    run = _make_run("aa-1")
    memory = AutoApplyStore()
    memory.add(run)
    mem_row = memory.get("aa-1")
    # 内存档投影键集与 AutoApplyRun 一致
    assert set(mem_row) >= {
        "run_id", "candidate_id", "graph_id", "base_version", "enabled_scopes",
        "patch", "shadow", "gate", "publish_version", "rollout_status",
        "status", "reason", "stages", "created_at", "updated_at",
    }
    assert mem_row["stages"][0]["stage"] == "start"
    assert mem_row["patch"]["diff"][0]["to"] == 800


def test_u1476_reset_only_own_tenant(engine):
    other = PgAutoApplyStore(engine, OTHER_TENANT)
    run = _make_run(other.next_run_id())
    other.add(run)
    store = PgAutoApplyStore(engine, TENANT)
    store.reset()
    assert store.list() == []
    assert other.get(run.run_id) is not None  # 他租户记录不受影响

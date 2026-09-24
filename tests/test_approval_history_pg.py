# -*- coding: utf-8 -*-
"""docs/61 §3 H2：已决审批历史 PG 档集成测试（候选 U763–U770）。

仅当 ATLAS_RUN_INTEGRATION=1 且 DATABASE_URL 指向可用 PG 时运行；迁移 glob 自动 apply 到 027。
相对内存档的可观察变化：跨 store 实例（模拟重启/多实例）保留已决历史、ring 200 淘汰
落库、同 token 重记走 upsert 不产生第二行、reset 只清本租户。投影形状与内存档逐键一致。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.history import (
    HISTORY_RING_SIZE,
    ApprovalHistoryEntry,
    InMemoryApprovalHistoryStore,
    PgApprovalHistoryStore,
    epoch_to_iso,
)
from atlas.iam.registry import STORAGE_BACKEND

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run approval history PG integration",
)


def _entry(token: str, **overrides):
    base = {
        "token": token,
        "node_id": "apr-1",
        "graph_id": "g1",
        "summary": "请确认退款",
        "approver": "boss",
        "decision": "approved",
        "resolved_by": "human",
        "comment": "ok",
        "created_at": epoch_to_iso(1_700_000_000.0),
        "resolved_at": epoch_to_iso(1_700_000_010.0),
    }
    return ApprovalHistoryEntry(**{**base, **overrides})


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["DATABASE_URL"])
    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        statements: list[str] = []
        current: list[str] = []
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
def store_factory(engine):
    tenant = f"pghist-{uuid.uuid4().hex[:8]}"

    def make():
        return PgApprovalHistoryStore(engine, tenant)

    primary = make()
    yield primary, make, tenant, engine
    primary.clear()


def test_record_list_desc_and_persists_across_instances(store_factory):
    store, make, _tenant, _engine = store_factory
    store.record(_entry("t-1"))
    store.record(_entry("t-2", decision="rejected", resolved_by="timeout"))

    fresh = make()  # 模拟重启：新 store 实例、同一库
    items = fresh.list(50)
    assert [item["token"] for item in items] == ["t-2", "t-1"]
    assert items[1]["decision"] == "approved"
    assert items[1]["comment"] == "ok"


def test_limit_clamped(store_factory):
    store, _make, _tenant, _engine = store_factory
    for index in range(3):
        store.record(_entry(f"t-{index}"))
    assert len(store.list(0)) == 1
    assert len(store.list(999)) == 3


def test_ring_evicts_oldest_in_database(store_factory):
    store, make, _tenant, _engine = store_factory
    for index in range(HISTORY_RING_SIZE + 5):
        store.record(_entry(f"t-{index}"))
    items = make().list(HISTORY_RING_SIZE)
    assert len(items) == HISTORY_RING_SIZE
    assert items[-1]["token"] == "t-5"


def test_same_token_upserts_instead_of_duplicating(store_factory):
    store, make, _tenant, _engine = store_factory
    store.record(_entry("t-dup", decision="approved", resolved_by="human"))
    store.record(
        _entry("t-dup", decision="rejected", resolved_by="timeout")
    )
    items = make().list(50)
    assert [item["token"] for item in items] == ["t-dup"]
    assert items[0]["decision"] == "rejected"  # 后写的决策胜出并排到最新


def test_clear_only_touches_own_tenant(store_factory):
    store, make, tenant, engine = store_factory
    other = PgApprovalHistoryStore(engine, f"{tenant}-other")
    store.record(_entry("mine"))
    other.record(_entry("theirs"))
    store.clear()
    assert make().list(50) == []
    assert [item["token"] for item in other.list(50)] == ["theirs"]
    other.clear()


def test_projection_matches_in_memory_tier(store_factory):
    store, _make, _tenant, _engine = store_factory
    memory = InMemoryApprovalHistoryStore()
    entry = _entry("t-shape", card_template_id="refund-approval")
    store.record(entry)
    memory.record(entry)
    assert store.list(50) == memory.list(50)
    assert store.list(50)[0]["cardTemplateId"] == "refund-approval"


def test_broker_with_pg_store_records_decision(store_factory):
    _store, make, _tenant, _engine = store_factory
    broker = ApprovalBroker(history_store=make())
    token = broker.request(
        node_id="apr-1",
        graph_id="g1",
        summary="请确认退款",
        approver="boss",
        timeout_seconds=600,
    )
    assert broker.resolve(token, "approved", comment="ok") is True
    items = make().list(50)
    assert len(items) == 1
    assert items[0]["token"] == token
    assert items[0]["resolvedBy"] == "human"


def test_new_install_schema_has_approval_history_table(engine):
    from sqlalchemy import inspect

    assert "approval_history" in inspect(engine).get_table_names()
    assert STORAGE_BACKEND in ("memory", "pg")  # 直连档不切 backend，装配仍走内存实现

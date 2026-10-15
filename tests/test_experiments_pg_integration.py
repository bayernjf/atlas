# -*- coding: utf-8 -*-
"""PgExperimentStore 集成（docs/134 §5 U1513–U1518；打包 BR；迁移 056）。

跑法（与其余 PG 集成用例同）：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \\
    .venv/bin/pytest -m integration tests/test_experiments_pg_integration.py

条款聚焦内存档给不了的事：迁移 056 落表与索引（U1513）、跨实例存活（U1514/U1517）、
全局 storage_id_seq 取号不复用（U1515）、两档投影逐键同构（U1516）、跨租户隔离与
reset 范围（U1518）。语义同构部分由 tests/test_experiments.py 守，这里不重复。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from atlas.routing import CanaryRule, RolloutConfig, Verdict
from atlas.routing.experiment_store import ExperimentStore
from atlas.routing.pg_experiment_store import PgExperimentStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run experiments PG integration",
    ),
]

_G = "refund-flow"


def _prior() -> RolloutConfig:
    return RolloutConfig(rules=[CanaryRule(percent=80)])


def _verdict() -> Verdict:
    return Verdict(
        status="significant",
        winner="A",
        metric="run_error_rate",
        rateA=0.10,
        rateB=0.20,
        z=2.8006,
        pValue=0.0051,
        reason="B 臂主指标率显著更高（z≈2.80，p≈0.005）",
    )


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    from atlas.storage.migrations import apply_pending

    apply_pending(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def tenant(engine):
    suffix = uuid.uuid4().hex[:8]
    tenant_id = f"exp-pg-{suffix}"
    yield tenant_id
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM experiments WHERE tenant_id LIKE :pattern"),
            {"pattern": f"exp-%-{suffix}"},
        )


# ---------- U1513 迁移 056 落表/列/索引 ----------

def test_u1513_migration_056_creates_table_columns_and_index(engine):
    expected_columns = {
        "id", "tenant_id", "graph_id", "version_a", "version_b", "traffic_percent",
        "primary_metric", "min_samples", "status", "prior_config", "verdict",
        "conclusion_action", "conclusion_reason", "created_by", "created_at",
        "concluded_at",
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'experiments'"
            )
        ).all()
        columns = {row[0] for row in rows}
        assert expected_columns <= columns

        index = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'experiments' "
                "AND indexname = 'idx_experiments_tenant_graph_status'"
            )
        ).first()
    assert index is not None
    for col in ("tenant_id", "graph_id", "status"):
        assert col in index[0]


# ---------- U1514 创建跨实例可读 ----------

def test_u1514_created_experiment_survives_new_store_instance(engine, tenant):
    created = PgExperimentStore(engine, tenant).create(
        graph_id=_G,
        version_a=1,
        version_b=2,
        traffic_percent=50,
        primary_metric="run_error_rate",
        min_samples=30,
        prior_config=_prior(),
        created_by="admin-a",
    )
    assert created.id.startswith("exp-")
    assert created.status == "running"

    seen = PgExperimentStore(engine, tenant).get(created.id)
    assert seen is not None
    assert seen.model_dump(mode="json") == created.model_dump(mode="json")
    assert seen.priorConfig is not None
    assert seen.priorConfig.model_dump(mode="json") == _prior().model_dump(mode="json")


# ---------- U1515 id 取全局 storage_id_seq，跨实例不复用 ----------

def test_u1515_ids_come_from_global_sequence_and_never_repeat(engine, tenant):
    first = PgExperimentStore(engine, tenant).create(
        graph_id=_G, version_a=1, version_b=2, traffic_percent=50,
        primary_metric="run_error_rate", min_samples=30,
    )
    # 换一个 store 实例再取号（模拟重启后另一进程）：数字部分必须不复用
    second = PgExperimentStore(engine, tenant).create(
        graph_id=f"{_G}-2", version_a=1, version_b=2, traffic_percent=50,
        primary_metric="run_error_rate", min_samples=30,
    )
    n1 = int(first.id.split("-", 1)[1])
    n2 = int(second.id.split("-", 1)[1])
    assert n1 != n2


# ---------- U1516 两档投影逐键同构 ----------

def test_u1516_pg_and_memory_projections_are_identical(engine, tenant):
    kwargs = dict(
        graph_id=_G,
        version_a=1,
        version_b=2,
        traffic_percent=50,
        primary_metric="run_error_rate",
        min_samples=30,
        prior_config=_prior(),
        created_by="admin-a",
    )
    pg = PgExperimentStore(engine, tenant).create(**kwargs)
    mem = ExperimentStore().create(**kwargs)

    pg_dump = pg.model_dump(mode="json")
    mem_dump = mem.model_dump(mode="json")
    # id 数字部分来源不同（PG 全局 seq / 内存进程内计数）、createdAt 各自取时刻，剔除后逐键同构
    for volatile in ("id", "createdAt"):
        pg_dump.pop(volatile)
        mem_dump.pop(volatile)
    assert pg_dump == mem_dump


# ---------- U1517 conclude 冻结跨实例可读 ----------

def test_u1517_conclusion_freezes_and_survives_new_instance(engine, tenant):
    store = PgExperimentStore(engine, tenant)
    created = store.create(
        graph_id=_G, version_a=1, version_b=2, traffic_percent=50,
        primary_metric="run_error_rate", min_samples=30,
    )
    concluded = store.conclude(
        created.id,
        verdict=_verdict(),
        action="adopt_a",
        reason="A 显著更优，回稳",
    )
    assert concluded is not None
    assert concluded.status == "concluded"

    seen = PgExperimentStore(engine, tenant).get(created.id)
    assert seen is not None
    assert seen.status == "concluded"
    assert seen.conclusionAction == "adopt_a"
    assert seen.conclusionReason == "A 显著更优，回稳"
    assert seen.concludedAt is not None
    assert seen.verdict is not None
    assert seen.verdict.model_dump(mode="json") == _verdict().model_dump(mode="json")


# ---------- U1518 reset 只清本租户 ----------

def test_u1518_reset_scopes_to_own_tenant(engine, tenant):
    other = f"exp-pg-{uuid.uuid4().hex[:8]}"
    try:
        created = PgExperimentStore(engine, tenant).create(
            graph_id=_G, version_a=1, version_b=2, traffic_percent=50,
            primary_metric="run_error_rate", min_samples=30,
        )

        bystander = PgExperimentStore(engine, other)
        assert bystander.get(created.id) is None
        assert bystander.list() == []

        bystander.reset()  # 他租户 reset 不涉本租户
        assert PgExperimentStore(engine, tenant).get(created.id) is not None

        PgExperimentStore(engine, tenant).reset()
        assert PgExperimentStore(engine, tenant).get(created.id) is None
        assert PgExperimentStore(engine, tenant).list() == []
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM experiments WHERE tenant_id = :t"), {"t": other}
            )

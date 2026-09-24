# -*- coding: utf-8 -*-
"""docs/59 F-2：PG 档静默 / 值班 / 告警 assignee 持久化集成测试（U652–U659）。

仅当 ATLAS_RUN_INTEGRATION=1 且 DATABASE_URL 指向可用 PG 时运行；迁移 apply 到 024。
核心相对内存档的可观察变化：跨 store 实例（模拟重启/多实例）保留静默、值班、assignee，
suppressed_count 落库；REST 形状与内存档一致（内存档 OpsStore 行为另见
test_monitoring_silences_escalation_oncall.py，本文件不覆盖）。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.silences import OnCallEmpty

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run monitoring PG integration",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed_node_run(store, graph_id: str = "g1") -> None:
    """触发内置 node_failed（warning）告警的一次失败运行。"""
    store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="completed",
        started_at=_now(),
        duration_ms=5.0,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="failed")],
    )


def _error_run(store, graph_id: str = "g1") -> None:
    """触发内置 run_error（critical）告警的一次错误运行。"""
    store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="error",
        started_at=_now(),
        duration_ms=5.0,
        nodes=[],
        error="boom",
    )


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
    from atlas.storage.pg import PgMonitoringStore

    tenant = f"pgf2-{uuid.uuid4().hex[:8]}"

    def make():
        return PgMonitoringStore(engine, tenant)

    primary = make()
    yield primary, make, tenant, engine
    # 清理本 tenant 全部相关行
    primary.reset()


# U652 ---------------------------------------------------------------------
def test_u652_silence_persists_with_active_filter_and_zero_count(store_factory):
    store, make, _tenant, _engine = store_factory
    sil = store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=30, reason="维护", created_by="admin-a"
    )
    assert sil.id.startswith("sil-")
    assert sil.suppressed_count == 0

    # 跨 store 实例（模拟重启）仍可读回
    fresh = make()
    all_items = fresh.list_silences()
    assert len(all_items) == 1
    assert all_items[0].id == sil.id and all_items[0].reason == "维护"
    assert len(fresh.list_silences(active=True)) == 1
    assert fresh.list_silences(active=False) == []


# U653 ---------------------------------------------------------------------
def test_u653_matched_silence_suppresses_and_persists_count(store_factory):
    store, make, tenant, engine = store_factory
    from sqlalchemy import text

    store.create_silence(
        rule_id="node_failed", graph_id=None, duration_minutes=30, reason="r", created_by="a"
    )
    _failed_node_run(store)
    assert store.list_alerts() == []
    # 计数落库：跨实例读回为 1，并直接核对表内列值
    fresh = make()
    assert fresh.list_silences(active=True)[0].suppressed_count == 1
    with engine.connect() as conn:
        db_count = conn.execute(
            text("SELECT suppressed_count FROM monitoring_silences WHERE tenant_id = :t"),
            {"t": tenant},
        ).scalar_one()
    assert db_count == 1

    # 解除后再跑：建告警
    store.delete_silence(store.list_silences()[0].id)
    _failed_node_run(store)
    alerts = store.list_alerts()
    assert len(alerts) == 1 and alerts[0].rule_id == "node_failed"


# U654 ---------------------------------------------------------------------
def test_u654_delete_silence_true_then_false(store_factory):
    store, _make, _tenant, _engine = store_factory
    sil = store.create_silence(
        rule_id=None, graph_id="g1", duration_minutes=30, reason="r", created_by="a"
    )
    assert store.delete_silence(sil.id) is True
    assert store.delete_silence(sil.id) is False  # 再删不存在 → API 404 语义
    assert store.list_silences() == []


# U655 ---------------------------------------------------------------------
def test_u655_oncall_dedup_rotate_modulo_persists_and_empty_409(store_factory):
    store, make, _tenant, _engine = store_factory
    # 去重保序、重置 index=0
    sched = store.set_oncall(members=["a", "b", "a", " ", "c"], updated_by="admin-a")
    assert sched.members == ["a", "b", "c"] and sched.index == 0
    assert store.get_oncall().members == ["a", "b", "c"]

    assert store.rotate_oncall(updated_by="x").index == 1  # current b
    assert store.rotate_oncall(updated_by="x").index == 2  # c
    assert store.rotate_oncall(updated_by="x").index == 0  # 3 % 3 == 0 → a

    # 跨实例保留轮换下标与成员
    fresh = make()
    got = fresh.get_oncall()
    assert got.members == ["a", "b", "c"] and got.index == 0

    # 空表（新 tenant）轮换 409
    empty_tenant = f"pgf2-empty-{uuid.uuid4().hex[:8]}"
    from atlas.storage.pg import PgMonitoringStore

    empty_store = PgMonitoringStore(_engine, empty_tenant)
    with pytest.raises(OnCallEmpty):
        empty_store.rotate_oncall(updated_by="x")


# U656 ---------------------------------------------------------------------
def test_u656_new_alert_assignee_persists_merge_keeps_and_none_when_empty(store_factory):
    store, make, _tenant, engine = store_factory
    store.set_oncall(members=["a", "b"], updated_by="admin-a")  # current = a
    _failed_node_run(store)
    alerts = store.list_alerts()
    assert len(alerts) == 1 and alerts[0].assignee == "a"

    # 再来一次同 rule+graph 失败：合并 count=2，assignee 仍为 a（不重指派）
    _failed_node_run(store)
    merged = make().list_alerts()
    assert len(merged) == 1 and merged[0].count == 2 and merged[0].assignee == "a"

    # assignee 真实落 monitoring_alerts.assignee 列（跨实例读列，非进程内）
    from sqlalchemy import text

    with engine.connect() as conn:
        db_assignee = conn.execute(
            text("SELECT assignee FROM monitoring_alerts WHERE tenant_id = :t"),
            {"t": _tenant},
        ).scalar_one()
    assert db_assignee == "a"

    # 无值班的新 tenant：新建告警 assignee 为 None
    no_oncall_tenant = f"pgf2-nooncall-{uuid.uuid4().hex[:8]}"
    from atlas.storage.pg import PgMonitoringStore

    no_oncall = PgMonitoringStore(engine, no_oncall_tenant)
    _failed_node_run(no_oncall)
    assert no_oncall.list_alerts()[0].assignee is None
    no_oncall.reset()


# U657 ---------------------------------------------------------------------
def test_u657_reset_clears_silences_oncall_and_alerts(store_factory):
    store, _make, tenant, engine = store_factory
    from sqlalchemy import text

    store.set_oncall(members=["a"], updated_by="x")
    store.create_silence(
        rule_id=None, graph_id=None, duration_minutes=30, reason="r", created_by="a"
    )
    _failed_node_run(store)  # 被静默压下，无告警；再解除造一条告警
    store.delete_silence(store.list_silences()[0].id)
    _failed_node_run(store)
    assert store.list_alerts()

    store.reset()
    with engine.connect() as conn:
        for table in ("monitoring_silences", "monitoring_oncall", "monitoring_alerts"):
            count = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant}
            ).scalar_one()
            assert count == 0, table
    assert store.get_oncall().members == []


# U658 ---------------------------------------------------------------------
def test_u658_silence_cap_100_and_lazy_purge_of_expired(store_factory):
    store, _make, tenant, engine = store_factory
    from sqlalchemy import text

    # 先直接插一条已过期静默（绕过 API 1-10080 分钟校验）
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO monitoring_silences "
                "(id, tenant_id, rule_id, graph_id, reason, created_by, created_at, expires_at, suppressed_count) "
                "VALUES (:id, :t, NULL, NULL, 'expired', 'a', :past, :past, 3)"
            ),
            {"id": f"sil-old-{uuid.uuid4().hex[:6]}", "t": tenant, "past": past},
        )

    for _ in range(105):
        store.create_silence(
            rule_id=None, graph_id=None, duration_minutes=60, reason="cap", created_by="a"
        )

    # 创建时惰性清掉过期行；cap 100 淘汰最旧，只保留最新 100 条活跃
    items = store.list_silences(active=True)
    assert len(items) == 100
    assert all(s.reason == "cap" for s in items)
    with engine.connect() as conn:
        expired_left = conn.execute(
            text(
                "SELECT count(*) FROM monitoring_silences "
                "WHERE tenant_id = :t AND reason = 'expired'"
            ),
            {"t": tenant},
        ).scalar_one()
    assert expired_left == 0


# U659 ---------------------------------------------------------------------
def test_u659_silence_graph_and_rule_scoping(store_factory):
    # 图级静默：只压该图
    store, _make, _tenant, _engine = store_factory
    store.create_silence(
        rule_id=None, graph_id="g1", duration_minutes=30, reason="g1", created_by="a"
    )
    _failed_node_run(store, graph_id="g1")
    _failed_node_run(store, graph_id="g2")
    alerts = store.list_alerts()
    assert len(alerts) == 1 and alerts[0].graph_id == "g2"
    assert store.list_silences()[0].suppressed_count == 1

    # 规则级静默：run_error 静默不压 node_failed（新 tenant 隔离）
    store.reset()
    store.create_silence(
        rule_id="run_error", graph_id=None, duration_minutes=30, reason="re", created_by="a"
    )
    _error_run(store, graph_id="g3")       # run_error 命中 → 压下
    _failed_node_run(store, graph_id="g3")  # node_failed 不命中 → 建告警
    rule_alerts = store.list_alerts()
    assert len(rule_alerts) == 1 and rule_alerts[0].rule_id == "node_failed"
    by_rule = {s.rule_id: s for s in store.list_silences()}
    assert by_rule["run_error"].suppressed_count == 1

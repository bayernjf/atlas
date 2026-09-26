# -*- coding: utf-8 -*-
"""调度两档一致性与 PG 认领唯一性（docs/68 §4 U886，打包 N）。

跑法（与其余 PG 集成用例同）：
    ATLAS_RUN_INTEGRATION=1 DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \\
    .venv/bin/pytest -m integration tests/test_scheduling_pg_integration.py

核心不是"能不能落库"，是两件事：
1. **两档投影逐键一致**——同一时间线喂两个 store，认领结果与 REST 投影必须一字不差，
   否则前端与文档就得写两套语义；
2. **内存档的易失性是明示断言**，不是碰巧没测到：跨"重启"（新实例）PG 仍挡住重复派发，
   内存档则放行。docs/68 §1 D-7 把这条差异写进契约，就得有用例钉着它往哪个方向偏。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from atlas.scheduling.engine import ACTION_FIRED, TickLedger, tick
from atlas.scheduling.models import schedule_projection
from atlas.scheduling.pg_store import PgScheduleStore
from atlas.scheduling.store import InMemoryScheduleStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run scheduling PG integration",
    ),
]

_TABLES = ("schedules", "schedule_fires")
_UTC = timezone.utc


def slot(minute: int) -> datetime:
    return datetime(2026, 9, 26, 10, minute, 0, tzinfo=_UTC)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(os.environ["DATABASE_URL"])
    migrations = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
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
def pair(engine):
    """PG 档（真库、独立租户前缀）＋内存档；结束按前缀清行，不靠 reset_tenant 兜底。"""
    suffix = uuid.uuid4().hex[:8]
    pg_tenant = f"sch-pg-{suffix}"
    memory_tenant = f"sch-mem-{suffix}"
    store = PgScheduleStore(engine)
    yield store, pg_tenant, memory_tenant, InMemoryScheduleStore()
    with engine.begin() as conn:
        for table in _TABLES:
            conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id LIKE :pattern"),
                {"pattern": f"sch-%-{suffix}"},
            )


def _replay(store, tenant: str) -> tuple[bool, bool]:
    """一条固定时间线：登记 → 停用 → 重发布 → 抢同一个槽两次 → 一次实发两次跳过。"""
    store.upsert_published(tenant_id=tenant, graph_id="g-1", version=1, cron="*/5 * * * *")
    store.set_enabled(tenant, "g-1", False)
    store.upsert_published(tenant_id=tenant, graph_id="g-1", version=2, cron="*/5 * * * *")
    first_claim = store.claim(tenant, "g-1", slot(5))
    second_claim = store.claim(tenant, "g-1", slot(5))
    store.note_fired(tenant, "g-1", slot(5))
    store.note_skipped(tenant, "g-1", slot(10))
    store.note_skipped(tenant, "g-1", slot(15))
    return first_claim, second_claim


def test_u886_both_tiers_answer_the_same_timeline_identically(pair):
    pg, pg_tenant, memory_tenant, memory = pair

    assert _replay(memory, memory_tenant) == _replay(pg, pg_tenant) == (True, False)

    now = slot(20)
    memory_view = schedule_projection(memory.list_tenant(memory_tenant)[0], now)
    pg_view = schedule_projection(pg.list_tenant(pg_tenant)[0], now)

    assert set(memory_view) == set(pg_view), "投影键集不一致＝前端要写两套读取"
    # created_at 两档各取"当时"（一个应用钟、一个 DB now()），差在微秒；除它之外必须全等。
    for view in (memory_view, pg_view):
        created = view.pop("createdAt")
        assert created.endswith("+00:00"), f"注册时刻未归一为 UTC ISO：{created}"
    assert memory_view == pg_view, f"两档投影分叉：{memory_view} != {pg_view}"
    assert pg_view["version"] == 2 and pg_view["enabled"] is False
    assert pg_view["skipCount"] == 2
    # nextFireAt 是"严格晚于 now 的下一个槽"：*/5 在 10:20 之后就是 10:25，不是 10:20
    assert pg_view["nextFireAt"] == "2026-09-26T10:25:00+00:00"
    # PG 读回的是 TIMESTAMPTZ，归一后必须与内存档的字符串同形同值
    assert pg_view["lastFiredAt"] == "2026-09-26T10:05:00+00:00"
    assert pg_view["lastSkippedAt"] == "2026-09-26T10:15:00+00:00"


def test_u886b_republish_keeps_the_switch_and_the_counters(pair):
    pg, tenant, _, _ = pair
    pg.upsert_published(tenant_id=tenant, graph_id="g-2", version=1, cron="0 * * * *")
    pg.set_enabled(tenant, "g-2", False)
    pg.note_skipped(tenant, "g-2", slot(0))

    record = pg.upsert_published(
        tenant_id=tenant, graph_id="g-2", version=2, cron="30 4 * * *"
    )
    assert record.version == 2 and record.cron == "30 4 * * *"
    assert record.enabled is False, "重发布翻回了运营者的开关"
    assert record.skip_count == 1
    created_before = record.created_at
    again = pg.upsert_published(tenant_id=tenant, graph_id="g-2", version=3, cron="0 1 1 * *")
    assert again.created_at == created_before, "重发布把注册时刻改成了今天"


def test_u886c_pg_claim_survives_a_restart_and_memory_admits_it_does_not(pair):
    pg, tenant, _, _ = pair
    pg.upsert_published(tenant_id=tenant, graph_id="g-3", version=1, cron="* * * * *")
    assert pg.claim(tenant, "g-3", slot(30)) is True

    restarted = PgScheduleStore(pg._engine)  # 新实例＝新进程，同一个库
    assert restarted.claim(tenant, "g-3", slot(30)) is False, "PG 档重启后重复派发了同一个槽"
    assert restarted.claim(tenant, "g-3", slot(31)) is True

    memory = InMemoryScheduleStore()
    memory.upsert_published(tenant_id="mem", graph_id="g-3", version=1, cron="* * * * *")
    assert memory.claim("mem", "g-3", slot(30)) is True
    # 这条断言**期望 True**：内存档易失是契约（docs/68 §1 D-7），
    # 把它断成 False 才是自欺——生产形态必须 PG 档这件事就靠这行留着提醒。
    assert InMemoryScheduleStore().claim("mem", "g-3", slot(30)) is True


def test_u886d_the_engine_drives_the_pg_store_end_to_end(pair):
    pg, tenant, _, _ = pair
    pg.upsert_published(tenant_id=tenant, graph_id="g-4", version=7, cron="*/5 * * * *")
    records = pg.list_tenant(tenant)
    dispatched: list[str] = []

    def claim(record, moment):
        return pg.claim(record.tenant_id, record.graph_id, moment)

    def dispatch(record, moment):
        dispatched.append(f"{record.graph_id}@{record.version}:{moment.isoformat()}")

    ledger = TickLedger()
    first = tick(slot(5), records, claim, dispatch, ledger=ledger)
    again = tick(slot(5) + timedelta(seconds=20), records, claim, dispatch, ledger=ledger)

    assert [outcome.action for outcome in first] == [ACTION_FIRED]
    assert again == []
    assert dispatched == ["g-4@7:2026-09-26T10:05:00+00:00"]
    # 认领落了库；last_fired_at **不**由引擎写（写它的是 api 层的 tick 装配，
    # 引擎零 IO 是这条分界的代价也是它的可测性），所以这里断言的是库里的认领行。
    with pg._engine.connect() as conn:
        rows = conn.execute(
            text("SELECT slot_utc FROM schedule_fires WHERE tenant_id = :t AND graph_id = :g"),
            {"t": tenant, "g": "g-4"},
        ).all()
    assert [str(row[0]) for row in rows] == ["2026-09-26 10:05:00+00:00"]
    assert pg.get(tenant, "g-4").last_fired_at is None


def test_u886e_reset_tenant_clears_rows_and_fire_history_for_that_tenant_only(pair):
    pg, tenant, _, _ = pair
    other = f"{tenant}-b"
    for name in (tenant, other):
        pg.upsert_published(tenant_id=name, graph_id="g-5", version=1, cron="0 * * * *")
        pg.claim(name, "g-5", slot(40))
    pg.upsert_published(tenant_id=other, graph_id="keep-me", version=1, cron="0 * * * *")

    pg.reset_tenant(tenant)
    assert pg.get(tenant, "g-5") is None
    assert pg.get(other, "g-5") is not None
    assert pg.get(other, "keep-me") is not None
    with pg._engine.connect() as conn:
        left = conn.execute(
            text("SELECT count(*) FROM schedule_fires WHERE tenant_id = :t"), {"t": tenant}
        ).scalar_one()
        kept = conn.execute(
            text("SELECT count(*) FROM schedule_fires WHERE tenant_id = :t"), {"t": other}
        ).scalar_one()
    assert left == 0 and kept == 1, "reset 越界清了别人的认领历史"

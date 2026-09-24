# -*- coding: utf-8 -*-
"""docs/61 §4 H3：审计 actor/时间过滤 + 游标分页（候选 U771–U790）。

覆盖内存档 store 过滤、REST 参数与 nextCursor、422 中文校验，以及 PG 直连两档对拍。
时间过滤的关键前提是「所有 `at` 都由 now_iso() 写成同一 UTC ISO-8601 格式」，
本文件用不变量用例把它锁死。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.observability import audit as audit_mod
from atlas.observability.audit import AuditStore, parse_bound

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pg_integration = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run audit filter PG integration",
)

BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def clock(monkeypatch):
    """把 now_iso 钉成可控时钟：写入时刻由测试决定。"""
    holder = {"now": BASE}
    monkeypatch.setattr(audit_mod, "now_iso", lambda: holder["now"].isoformat())
    return holder


def _seed_with_clock(store, clock, count, *, actor="admin-a", start=BASE):
    """写 count 条、每条间隔 1 分钟；返回 [(action, moment)]。"""
    seeded = []
    for index in range(count):
        action = f"POST /api/seed/{index}"
        moment = start + timedelta(minutes=index)
        clock["now"] = moment
        store.record(
            tenant_id="t1",
            actor=actor,
            action=action,
            status_code=200,
            path="/api/x",
            ip="127.0.0.1",
        )
        seeded.append((action, moment))
    return seeded


# ============================ 内存档 store ============================


def test_seq_is_monotonic_and_projected(clock):
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 3)
    items = store.list()
    assert [item["seq"] for item in items] == [3, 2, 1]  # 倒序，seq 递增写入
    assert [item["id"] for item in items] == ["aud-3", "aud-2", "aud-1"]


def test_actor_filter_is_exact_match(clock):
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 2, actor="admin-a")
    _seed_with_clock(store, clock, 1, actor="operator-a")
    only_admin = store.list(actor="admin-a")
    assert {item["actor"] for item in only_admin} == {"admin-a"}
    assert len(only_admin) == 2
    # 前缀相似不算命中（v1 不做模糊搜索）
    assert store.list(actor="admin") == []


def test_since_until_are_inclusive_bounds(clock):
    store = AuditStore(maxlen=100)
    seeded = _seed_with_clock(store, clock, 5)  # minute 0..4
    lo = seeded[1][1].isoformat()
    hi = seeded[3][1].isoformat()
    window = store.list(since=lo, until=hi)
    assert len(window) == 3  # 闭区间含两端
    assert {item["action"] for item in window} == {seeded[i][0] for i in (1, 2, 3)}


def test_until_accepts_zulu_suffix(clock):
    """前端 Date.toISOString() 带 Z，必须与存储侧 +00:00 同义。"""
    store = AuditStore(maxlen=100)
    seeded = _seed_with_clock(store, clock, 3)
    zulu = seeded[1][1].strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
    hits = store.list(since=zulu)
    assert len(hits) == 2  # minute 1（含）与 2


def test_cursor_pages_backward_without_overlap_or_gaps(clock):
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 7)
    first = store.list(limit=3)
    second = store.list(limit=3, cursor=first[-1]["seq"])
    third = store.list(limit=3, cursor=second[-1]["seq"])
    assert [len(first), len(second), third and len(third) or 0] == [3, 3, 1]
    tokens = [item["seq"] for item in first + second + third]
    assert tokens == [7, 6, 5, 4, 3, 2, 1]  # 连贯、无重叠、无遗漏
    assert store.list(limit=3, cursor=1) == []


def test_filters_combine(clock):
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 2, actor="admin-a")
    # 第二段用不同起点，避免与第一段撞 action 名（否则前缀过滤会跨段命中）。
    _seed_with_clock(store, clock, 2, actor="operator-a", start=BASE + timedelta(hours=1))
    both = store.list(actor="operator-a", action_prefix="POST /api/seed/1")
    assert len(both) == 1
    assert both[0]["actor"] == "operator-a"
    # 时间窗与 actor 同时生效
    late = store.list(actor="admin-a", since=(BASE + timedelta(hours=1)).isoformat())
    assert late == []


def test_export_honours_filters_but_ignores_pagination(clock):
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 12)
    body = store.export_jsonl(actor="admin-a")
    rows = [json.loads(line) for line in body.splitlines()]
    assert len(rows) == 12  # 全量匹配，不受 limit 影响
    assert [row["seq"] for row in rows] == list(range(1, 13))  # 正序旧→新


def test_all_recorded_timestamps_share_one_utc_format(clock):
    """TEXT/时刻比较的前提不变量：at 一律同一 UTC ISO-8601 格式且可解析。"""
    store = AuditStore(maxlen=100)
    _seed_with_clock(store, clock, 4)
    stamps = [item["at"] for item in store.list()]  # list 是倒序
    parsed = [parse_bound(stamp) for stamp in stamps]
    assert all(moment.utcoffset() == timedelta(0) for moment in parsed)
    assert parsed == sorted(parsed, reverse=True)  # 倒序读出 == 时间倒序
    assert all(stamp.endswith("+00:00") for stamp in stamps)


def test_record_survives_with_default_seq_when_constructed_directly():
    """seq 带默认值：既有夹具直接构造 AuditEvent 不必跟着改。"""
    event = audit_mod.AuditEvent(
        id="aud-x", tenantId="t1", actor="a", action="POST /x",
        statusCode=200, path="/x", ip="", at=BASE.isoformat(),
    )
    assert event.seq == 0


# ============================ REST ============================

from fastapi.testclient import TestClient  # noqa: E402

from atlas.api.main import app  # noqa: E402
from atlas.iam.deps import tenant_registry  # noqa: E402

anon = TestClient(app)


def _login(username: str = "admin-a", password: str = "admin123") -> dict[str, str]:
    token = anon.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def headers():
    auth = _login()
    services = tenant_registry.get("t1")
    services.audit_store.clear()
    for index in range(5):
        services.audit_store.record(
            tenant_id="t1",
            actor="admin-a" if index % 2 == 0 else "operator-a",
            action=f"POST /api/seed/{index}",
            status_code=200,
            path="/api/seed",
            ip="127.0.0.1",
        )
    yield auth
    services.audit_store.clear()


def test_rest_returns_next_cursor_and_walks_pages(headers):
    # seq 计数器不随 clear 归零、且登录自身也留一条审计 → 只断言相对结构，不断言绝对 seq。
    all_items = anon.get("/api/audit/events", headers=headers, params={"limit": 500}).json()["items"]
    total = len(all_items)
    assert total >= 5

    walked: list[int] = []
    cursor: int | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        page = anon.get("/api/audit/events", headers=headers, params=params).json()
        seqs = [item["seq"] for item in page["items"]]
        pages += 1
        assert len(seqs) <= 2
        assert seqs == sorted(seqs, reverse=True)
        if cursor is not None:
            assert max(seqs) < cursor  # 严格更早，不与上一页重叠
        walked.extend(seqs)
        cursor = page["nextCursor"]
        # 契约：nextCursor 为 null 即「没有下一页」——取满一页才可能有下一页。
        assert (cursor is not None) == (len(seqs) == 2)
        if cursor is None:
            break
        if pages > 20:
            pytest.fail("游标翻页未收敛")

    assert walked == [item["seq"] for item in all_items]  # 无重叠、无遗漏、顺序一致


def test_rest_actor_filter(headers):
    body = anon.get("/api/audit/events", headers=headers, params={"actor": "operator-a"}).json()
    assert {item["actor"] for item in body["items"]} == {"operator-a"}
    assert len(body["items"]) == 2


def test_rest_export_applies_filters(headers):
    resp = anon.get(
        "/api/audit/export", headers=headers, params={"actor": "admin-a", "action": "POST /api/seed/"}
    )
    assert resp.status_code == 200
    rows = [json.loads(line) for line in resp.text.splitlines()]
    assert len(rows) == 3
    assert {row["actor"] for row in rows} == {"admin-a"}


@pytest.mark.parametrize(
    "params, fragment",
    [
        ({"since": "not-a-date"}, "since"),
        ({"until": "2026-13-45T00:00:00+00:00"}, "until"),
        ({"since": "2026-09-05T00:00:00+00:00", "until": "2026-09-01T00:00:00+00:00"}, "since 不能晚于 until"),
        ({"cursor": 0}, "cursor"),
    ],
)
def test_rest_rejects_bad_bounds_with_chinese_422(headers, params, fragment):
    resp = anon.get("/api/audit/events", headers=headers, params=params)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(fragment in item for item in detail)


def test_rest_limit_still_clamped_not_rejected(headers):
    body = anon.get("/api/audit/events", headers=headers, params={"limit": 9999}).json()
    assert body["limit"] == 500  # 现状纪律：limit 静默 clamp，不改判 422
    assert len(body["items"]) == 5


def test_audit_read_stays_admin_only(headers):
    viewer = _login("viewer-a", "viewer123")
    assert anon.get("/api/audit/events", headers=viewer).status_code == 403
    assert anon.get("/api/audit/events", headers={}).status_code == 401


# ============================ PG 直连对拍 ============================


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    eng = create_engine(DATABASE_URL)
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


@pg_integration
def test_pg_store_matches_memory_store_on_every_filter(engine, clock):
    from sqlalchemy import text as sql_text

    from atlas.storage.pg import PgAuditStore

    tenant = f"pgaudit-{uuid.uuid4().hex[:8]}"
    # 两档灌同一批数据（actor 交替、每分钟一条），过滤结果才可逐键对拍。
    memory = AuditStore(maxlen=100)
    clock["now"] = BASE
    for index in range(6):
        clock["now"] = BASE + timedelta(minutes=index)
        memory.record(
            tenant_id="t1",
            actor="admin-a" if index % 2 == 0 else "operator-a",
            action=f"POST /api/seed/{index}",
            status_code=200,
            path="/api/x",
            ip="127.0.0.1",
        )
    pg = PgAuditStore(engine, tenant)
    seeded = []
    for index in range(6):
        moment = BASE + timedelta(minutes=index)
        clock["now"] = moment
        pg.record(
            tenant_id=tenant,
            actor="admin-a" if index % 2 == 0 else "operator-a",
            action=f"POST /api/seed/{index}",
            status_code=200,
            path="/api/x",
            ip="127.0.0.1",
        )
        seeded.append(moment)
    try:
        # actor 精确等值
        assert [i["actor"] for i in pg.list(actor="operator-a")] == ["operator-a"] * 3
        # 时间闭区间两端都含
        window = pg.list(since=seeded[1].isoformat(), until=seeded[3].isoformat())
        assert len(window) == 3
        # Z 后缀与 +00:00 同义
        assert len(pg.list(since="2026-09-01T12:02:00.000Z")) == 4
        # 游标向更早翻页、与首页不重叠（PG 的 seq 来自全局 storage_id_seq，只断言相对结构）
        first = pg.list(limit=2)
        second = pg.list(limit=2, cursor=first[-1]["seq"])
        assert [i["seq"] for i in first] == sorted([i["seq"] for i in first], reverse=True)
        assert len(second) == 2
        assert max(i["seq"] for i in second) < first[-1]["seq"]
        # 两档投影逐键一致
        assert set(pg.list(limit=1)[0].keys()) == set(memory.list(limit=1)[0].keys())
        # export 受过滤、不受分页
        assert len(pg.export_jsonl(actor="admin-a").splitlines()) == 3
        # 组合过滤与内存档同结果
        assert len(pg.list(actor="admin-a", since=seeded[2].isoformat())) == len(
            memory.list(actor="admin-a", since=seeded[2].isoformat())
        )
    finally:
        with engine.begin() as conn:
            conn.execute(
                sql_text("DELETE FROM audit_events WHERE tenant_id = :t"), {"t": tenant}
            )

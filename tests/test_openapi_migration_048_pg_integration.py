# -*- coding: utf-8 -*-
"""迁移 048（openapi_imports 内容指纹唯一部分索引）PG 集成测试。

docs/125 §3.3 / U1380 / U1381（项目级审查发现 #5）：
- U1380：存量同指纹去重（保留最小 seq、余软删、'' 老行不参与）、唯一部分索引
  （谓词带 content_hash <> ''）、二跑幂等；
- U1381：并发同形导入恰一成一折同形 409（无双写）；并发/现存恢复冲突折
  OPENAPI_RESTORE_CLASH。

需 ATLAS_RUN_INTEGRATION=1 + DATABASE_URL；fixture 只铺到 023，048 由用例自控执行。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.environ.get("DATABASE_URL", "")
RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION", "") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run migration 048 PG integration",
)

MIGRATION_048 = "048_openapi_imports_hash_unique.sql"


def _run_migration(engine, name: str) -> None:
    path = Path(__file__).resolve().parents[1] / "db" / "migrations" / name
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
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _hash_index_is_unique(engine) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT i.indisunique FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'idx_openapi_imports_hash'"
            )
        ).first()
    return bool(row is not None and row[0])


def _ensure_048(engine) -> None:
    """048 幂等：唯一索引不存在（仍是 023 的非唯一索引）则应用迁移。"""
    if not _hash_index_is_unique(engine):
        _run_migration(engine, MIGRATION_048)


@pytest.fixture(scope="module")
def setup():
    from atlas.memory.database import create_database_engine
    from atlas.openapi.pg_store import PgImportStore

    engine = create_database_engine(DATABASE_URL, pool_size=4)
    for name in (
        "002_storage.sql",
        "018_openapi_imports.sql",
        "019_openapi_auth_columns.sql",
        "023_openapi_imports_dedupe_softdelete.sql",
    ):
        _run_migration(engine, name)
    yield engine, PgImportStore
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM openapi_imports WHERE tenant_id LIKE 'm048%'"))
    engine.dispose()


def _insert_raw(engine, *, tenant, seq, fingerprint, deleted_at=None):
    """裸 INSERT 造历史行（绕过 store，指纹/软删列直书）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO openapi_imports (id, tenant_id, seq, title, base_url, created_at, "
                "operations, security_schemes, credential_envelopes, content_hash, deleted_at) "
                "VALUES (:id, :tenant, :seq, :title, :base_url, now(), "
                "'[]'::jsonb, '{}'::jsonb, '{}'::jsonb, :hash, :deleted)"
            ),
            {
                "id": f"{tenant}-r{seq}",
                "tenant": tenant,
                "seq": seq,
                "title": f"Raw {seq}",
                "base_url": f"https://{tenant}.example.com/{seq}",
                "hash": fingerprint,
                "deleted": deleted_at,
            },
        )


def _spec(title: str, base_url: str):
    from atlas.openapi.models import OperationDescriptor, ParsedSpec

    return ParsedSpec(
        title=title,
        version="3.0.3",
        base_url=base_url,
        operations=[
            OperationDescriptor(
                name="list_things",
                method="get",
                path="/things",
                summary="things",
                permission="read",
                idempotent=True,
                input_schema={"type": "object"},
                locations={},
            )
        ],
    )


# --- U1380：存量去重 + 唯一部分索引 ---------------------------------------

def test_048_dedupes_existing_rows_keeps_smallest_seq(setup):
    engine, _ = setup
    # 先复原 023 的非唯一索引，造 048 前的存量重复，保证用例可重入。
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS idx_openapi_imports_hash"))
        conn.execute(
            text(
                "CREATE INDEX idx_openapi_imports_hash ON openapi_imports "
                "(tenant_id, content_hash) WHERE deleted_at IS NULL"
            )
        )

    tenant = "m048-dedupe"
    shared = "a" * 64
    other = "b" * 64
    for seq in (10, 20, 30):
        _insert_raw(engine, tenant=tenant, seq=seq, fingerprint=shared)
    _insert_raw(engine, tenant=tenant, seq=40, fingerprint=other)
    # 023 前老行 content_hash=''，可能多行；不参与去重与唯一约束
    _insert_raw(engine, tenant=tenant, seq=50, fingerprint="")
    _insert_raw(engine, tenant=tenant, seq=60, fingerprint="")

    _run_migration(engine, MIGRATION_048)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT seq, deleted_at FROM openapi_imports "
                "WHERE tenant_id = :t ORDER BY seq"
            ),
            {"t": tenant},
        ).all()
    by_seq = {seq: deleted for seq, deleted in rows}
    assert by_seq[10] is None  # 最小 seq 保留
    assert by_seq[20] is not None and by_seq[30] is not None  # 余者软删
    assert by_seq[40] is None
    assert by_seq[50] is None and by_seq[60] is None  # '' 行不动
    assert sorted(seq for seq, deleted in rows if deleted is None) == [10, 40, 50, 60]


def test_048_second_run_changes_nothing(setup):
    engine, _ = setup
    _ensure_048(engine)
    tenant = "m048-idem"
    fingerprint = "c" * 64
    _insert_raw(engine, tenant=tenant, seq=1, fingerprint=fingerprint)
    _insert_raw(
        engine, tenant=tenant, seq=2, fingerprint=fingerprint,
        deleted_at="2026-01-01T00:00:00.000+00:00",
    )
    _insert_raw(
        engine, tenant=tenant, seq=3, fingerprint=fingerprint,
        deleted_at="2026-01-02T00:00:00.000+00:00",
    )

    _run_migration(engine, MIGRATION_048)  # 二跑：DROP/CREATE 幂等，去重零行

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT seq, deleted_at FROM openapi_imports "
                "WHERE tenant_id = :t ORDER BY seq"
            ),
            {"t": tenant},
        ).all()
    by_seq = {seq: deleted for seq, deleted in rows}
    assert by_seq[1] is None
    assert by_seq[2] == "2026-01-01T00:00:00.000+00:00"  # 软删时间不被刷新
    assert by_seq[3] == "2026-01-02T00:00:00.000+00:00"


def test_048_creates_unique_partial_index_with_nonempty_predicate(setup):
    engine, _ = setup
    _ensure_048(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'idx_openapi_imports_hash'"
            )
        ).first()
    assert row is not None
    assert row[0] is True
    predicate = row[1]
    assert "deleted_at" in predicate and "IS NULL" in predicate
    assert "content_hash" in predicate and "<>" in predicate

    # 未删同 (租户, 非空指纹) 第二行直接被唯一索引拒绝
    tenant = "m048-idx"
    _insert_raw(engine, tenant=tenant, seq=1, fingerprint="d" * 64)
    with pytest.raises(IntegrityError):
        _insert_raw(engine, tenant=tenant, seq=2, fingerprint="d" * 64)
    # 空串不参与唯一约束：多行 '' 合法
    _insert_raw(engine, tenant=tenant, seq=3, fingerprint="")
    _insert_raw(engine, tenant=tenant, seq=4, fingerprint="")


# --- U1381：并发 add 竞态折同形 409 ---------------------------------------

def test_concurrent_same_shape_add_one_wins_one_conflict(setup):
    engine, Store = setup
    _ensure_048(engine)
    from atlas.openapi.store import ImportStoreError

    for round_no in range(5):
        tenant = f"m048-conc-{round_no}"
        spec = _spec("Conc Pit", f"https://conc{round_no}.example.com")
        barrier = threading.Barrier(2)
        outcomes: list[tuple] = []

        def worker() -> None:
            store = Store(engine, tenant)
            barrier.wait()
            try:
                imported = store.add(spec)
                outcomes.append(("ok", imported.spec_id))
            except ImportStoreError as exc:
                outcomes.append(
                    ("err", exc.code, exc.existing_spec_id, exc.status_code)
                )

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(outcomes) == 2
        assert sorted(outcome[0] for outcome in outcomes) == ["err", "ok"]
        winner = next(outcome[1] for outcome in outcomes if outcome[0] == "ok")
        error = next(outcome for outcome in outcomes if outcome[0] == "err")
        # 预检路径与 INSERT 唯一冲突折错形状逐字一致
        assert error[1] == "OPENAPI_SPEC_ALREADY_IMPORTED"
        assert error[2] == winner
        assert error[3] == 409
        # 无双写：恰一活行且即赢家
        assert [s.spec_id for s in Store(engine, tenant).list()] == [winner]


# --- U1381：restore 撞现存/并发恢复折 OPENAPI_RESTORE_CLASH ----------------

def test_restore_clash_against_existing_live_row(setup):
    engine, Store = setup
    _ensure_048(engine)
    tenant = "m048-rclash"
    fingerprint = "e" * 64
    _insert_raw(engine, tenant=tenant, seq=1, fingerprint=fingerprint)  # B 存活
    _insert_raw(
        engine, tenant=tenant, seq=2, fingerprint=fingerprint,
        deleted_at="2026-02-01T00:00:00.000+00:00",
    )  # A 软删

    store = Store(engine, tenant)
    ok, code, existing = store.restore(f"{tenant}-r2")
    assert (ok, code, existing) == (False, "OPENAPI_RESTORE_CLASH", f"{tenant}-r1")
    with engine.connect() as conn:
        deleted = conn.execute(
            text(
                "SELECT deleted_at FROM openapi_imports "
                "WHERE tenant_id = :t AND id = :id"
            ),
            {"t": tenant, "id": f"{tenant}-r2"},
        ).scalar_one()
    assert deleted is not None  # 冲突未恢复，A 仍软删


def test_concurrent_restore_duplicate_hash_one_wins_one_clash(setup):
    engine, Store = setup
    _ensure_048(engine)

    for round_no in range(5):
        tenant = f"m048-rrace-{round_no}"
        fingerprint = f"{round_no:064d}"
        # 两条同指纹软删行（部分唯一索引不约束已删行），并发恢复必有一撞
        _insert_raw(
            engine, tenant=tenant, seq=1, fingerprint=fingerprint,
            deleted_at="2026-03-01T00:00:00.000+00:00",
        )
        _insert_raw(
            engine, tenant=tenant, seq=2, fingerprint=fingerprint,
            deleted_at="2026-03-02T00:00:00.000+00:00",
        )
        barrier = threading.Barrier(2)
        outcomes: list[tuple] = []

        def worker(seq: int) -> None:
            store = Store(engine, tenant)
            barrier.wait()
            outcomes.append(store.restore(f"{tenant}-r{seq}"))

        threads = [
            threading.Thread(target=worker, args=(1,)),
            threading.Thread(target=worker, args=(2,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(outcomes) == 2
        winners = [outcome for outcome in outcomes if outcome == (True, None, None)]
        losers = [
            outcome
            for outcome in outcomes
            if outcome[0] is False and outcome[1] == "OPENAPI_RESTORE_CLASH"
        ]
        assert len(winners) == 1 and len(losers) == 1
        winner_id = losers[0][2]
        assert winner_id in {f"{tenant}-r1", f"{tenant}-r2"}
        active = Store(engine, tenant).list()
        assert [s.spec_id for s in active] == [winner_id]

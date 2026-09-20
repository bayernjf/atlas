# -*- coding: utf-8 -*-
"""M11 批 3 PG/pgvector 集成测试（docs/26 §4/§9.2，U93–U96）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_memory_pg_integration.py

覆盖：006 建表/vector 列/索引；PgMemoryStore remember/recall 与进程内**两档对拍一致**
（pgvector vector 为单精度，score 容差 1e-2）；PG scope(@>)/kind/delete/clear；
ATLAS_STORAGE_BACKEND=pg 装配切换。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run memory PG integration",
    ),
]

TENANT = "pgmemtest"


def _run_migration(engine) -> None:
    """按序号执行 db/migrations/*.sql（跳过注释行；语句以 ; 结尾）。"""
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
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


@pytest.fixture(scope="module")
def pg_store():
    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE tenant_id = :t"), {"t": TENANT})
    store = PgBackend(engine).memory_store(TENANT)
    yield store
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE tenant_id = :t"), {"t": TENANT})
    engine.dispose()


def _seed(store) -> None:
    store.remember(kind="fact", content="订单 o-1 已退款 88 元", scope={"order_id": "o-1"})
    store.remember(kind="fact", content="订单 o-2 已发货", scope={"order_id": "o-2"})
    store.remember(kind="preference", content="用户偏好顺丰快递配送", scope={"user_id": "u-1"})
    store.remember(kind="preference", content="用户偏好周末不配送", scope={"user_id": "u-1"})


def test_u93_migration_creates_table_vector_column_and_index(pg_store):
    engine = pg_store._engine
    with engine.connect() as conn:
        columns = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'memory_items'"
                )
            )
        }
        assert {"id", "tenant_id", "kind", "content", "scope", "embedding",
                "confidence", "source", "meta", "created_at"} <= set(columns)
        # vector 类型在 information_schema 显示为 USER-DEFINED
        assert columns["embedding"] == "USER-DEFINED"
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'memory_items'")
            )
        }
        assert "idx_memory_items_tenant_kind" in indexes
        assert "idx_memory_items_embedding" in indexes
        # CHECK 约束存在
        checks = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'memory_items'::regclass")
        ).scalars().all()
        assert any("fact" in c and "preference" in c for c in checks)


def test_u94_pg_matches_in_process_store(pg_store):
    """两档对拍：同批数据下 recall 命中顺序一致、score 在单精度容差内。"""
    from atlas.memory.items import MemoryStore

    _seed(pg_store)
    local = MemoryStore()
    _seed(local)

    for query, kwargs in [
        ("订单退款", {"top_k": 3}),
        ("快递配送偏好", {"kind": "preference", "top_k": 3}),
        ("发货", {"top_k": 2}),
    ]:
        pg_results = pg_store.recall(query, **kwargs)
        local_results = local.recall(query, **kwargs)
        assert [r["content"] for r in pg_results] == [r["content"] for r in local_results]
        assert [r["kind"] for r in pg_results] == [r["kind"] for r in local_results]
        for pg_item, local_item in zip(pg_results, local_results):
            assert pg_item["score"] == pytest.approx(local_item["score"], abs=1e-2)
            assert "embedding" not in pg_item and "embedding" not in local_item


def test_u95_pg_scope_kind_delete_clear(pg_store):
    # scope 子集匹配（@>）
    scoped = pg_store.recall("订单", scope={"order_id": "o-1"})
    assert all(r["scope"].get("order_id") == "o-1" for r in scoped)
    assert scoped and "退款" in scoped[0]["content"]
    # 不匹配的 scope 返空
    assert pg_store.recall("订单退款", scope={"order_id": "nope"}) == []
    # kind 过滤
    facts = pg_store.list(kind="fact")
    assert facts and all(i["kind"] == "fact" for i in facts)
    prefs = pg_store.list(kind="preference")
    assert prefs and all(i["kind"] == "preference" for i in prefs)
    # list 倒序、limit
    all_items = pg_store.list(limit=100)
    assert len(all_items) >= 4
    assert len(pg_store.list(limit=2)) == 2
    # id 前缀 mem-
    assert all(i["id"].startswith("mem-") for i in all_items)
    # delete 存在/不存在、跨租户不可见
    target = all_items[0]["id"]
    assert pg_store.delete(target) is True
    assert pg_store.delete(target) is False
    # clear
    pg_store.clear()
    assert pg_store.list(limit=100) == []
    _seed(pg_store)  # 补回供 fixture 后其他断言/清理


def test_u96_backend_switch_wires_pg_memory_store(monkeypatch):
    """ATLAS_STORAGE_BACKEND=pg 时 TenantServices.memory_store 为 PgMemoryStore。"""
    from atlas.storage.pg import PgMemoryStore

    import atlas.iam.registry as registry_mod

    monkeypatch.setattr(registry_mod, "STORAGE_BACKEND", "pg")
    services = registry_mod.TenantRegistry._create_services(TENANT)
    assert isinstance(services.memory_store, PgMemoryStore)
    services.memory_store.clear()


def test_u201_pg_update_merges_reindexes_and_404(pg_store):
    """docs/28 §5.1 ⑩：PgMemoryStore.update 往返、source=manual、content 变重算 embedding。"""
    from atlas.memory.models import MemoryValidationError

    pg_store.clear()
    created = pg_store.remember(
        kind="fact", content="PG 更新前的事实内容", confidence=0.5, source="tool",
        metadata={"k": "v"},
    )
    # content 不变、仅改 confidence/scope → 200 形态、source manual、created_at 保留
    partial = pg_store.update(created["id"], confidence=0.25, scope={"user_id": "u-1"})
    assert partial is not None
    assert partial["id"] == created["id"]
    assert partial["created_at"] == created["created_at"]
    assert partial["confidence"] == 0.25
    assert partial["scope"] == {"user_id": "u-1"}
    assert partial["source"] == "manual"
    # content/kind/metadata 全改 → 重算 embedding，新词可检索
    full = pg_store.update(
        created["id"], kind="preference", content="PG 更新后偏好顺丰周末配送",
        metadata={"via": "ui"},
    )
    assert full["kind"] == "preference"
    assert full["metadata"] == {"via": "ui"}
    hits = pg_store.recall("顺丰周末配送", top_k=1)
    assert hits and hits[0]["id"] == created["id"]
    # 落库后重新读回，字段确实持久化（非仅返回值）
    again = pg_store.list(limit=100)
    row = next(i for i in again if i["id"] == created["id"])
    assert row["source"] == "manual" and row["kind"] == "preference"
    # 不存在 / 他租户 → None；非法入参报错
    assert pg_store.update("mem-404", content="x") is None
    with pytest.raises(MemoryValidationError):
        pg_store.update(created["id"], confidence=9)
    pg_store.clear()
    _seed(pg_store)

# -*- coding: utf-8 -*-
"""打包 A1（docs/97）U1183：模板元数据 PG 持久化集成测试。

docker run -d --name atlas-a1-test-pg -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas -e POSTGRES_DB=atlas -p 5433:5432 pgvector/pgvector:pg16
DATABASE_URL="postgresql+psycopg://atlas:atlas@localhost:5433/atlas" ATLAS_RUN_INTEGRATION=1 \
  .venv/bin/pytest -m integration tests/test_user_templates_pg_integration.py

覆盖（docs/97 §4 U1183 的 PG 半边）：
- 迁移 043 应用成功：user_templates 增 version/updated_at/usage_count/params 四列；
- add 落库后跨新 engine/新 store 实例可读（重启模拟），元字段齐全；
- update CAS：if_match_version 匹配 version+1；不匹配抛 TemplateVersionConflict（409 语义）且行不变；缺省无防护；
- touch 原子累加 usage_count，跨重启可读；
- params/tags/graph JSONB 往返逐键一致；两档（内存 vs PG）投影对拍；
- 租户隔离：A 租户写不出现于 B 租户。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from atlas.template.pg_store import PgUserTemplateStore
from atlas.template.user_store import TemplateVersionConflict, UserTemplateStore

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run template PG integration",
    ),
]

TENANT = "pga1test"
OTHER_TENANT = "pga1other"

PARAMS = {
    "min_amount": {"type": "number", "label": "最小金额", "required": True},
    "channel": {"type": "select", "label": "渠道", "options": ["email", "webhook", "im"]},
}


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
            text("DELETE FROM user_templates WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    yield eng
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM user_templates WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    eng.dispose()


def _sample_graph():
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }


def test_migration_043_adds_meta_columns(engine):
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_templates'"
                )
            ).all()
        }
    assert {"version", "updated_at", "usage_count", "params"} <= cols


def test_pg_store_crud_and_restart_read(engine):
    store = PgUserTemplateStore(engine, TENANT)
    created = store.add(
        name="PG 模板",
        description="d",
        tags=["t1", "t2"],
        category="退款流程",
        graph=_sample_graph(),
        params=PARAMS,
    )
    assert created.version == 1
    assert created.usage_count == 0
    assert created.updated_at == created.created_at
    assert created.params == PARAMS

    # 重启模拟：新 engine + 新 store 实例
    eng2 = _new_engine()
    try:
        store2 = PgUserTemplateStore(eng2, TENANT)
        got = store2.get(created.id)
        assert got is not None
        assert got.name == "PG 模板"
        assert got.version == 1
        assert got.params == PARAMS
        assert got.tags == ["t1", "t2"]
        assert got.category == "退款流程"
        # graph JSONB 往返逐键一致
        assert got.graph == _sample_graph()
    finally:
        eng2.dispose()


def test_pg_store_cas_conflict_and_passthrough(engine):
    store = PgUserTemplateStore(engine, TENANT)
    created = store.add(name="CAS PG", description="", tags=[], graph=_sample_graph(), params={})
    tid = created.id

    ok = store.update(tid, name="CAS PG v2", description="", tags=[], graph=_sample_graph(),
                      if_match_version=1)
    assert ok is not None and ok.version == 2

    with pytest.raises(TemplateVersionConflict):
        store.update(tid, name="stale", description="", tags=[], graph=_sample_graph(),
                     if_match_version=1)
    # CAS 失败不落写
    after = store.get(tid)
    assert after.name == "CAS PG v2"
    assert after.version == 2

    plain = store.update(tid, name="no guard", description="", tags=[], graph=_sample_graph())
    assert plain.version == 3

    # 不存在→None（与冲突区分）
    assert store.update("utpl-999", name="x", description="", tags=[], graph=_sample_graph()) is None


def test_pg_store_touch_accumulates_across_restart(engine):
    store = PgUserTemplateStore(engine, TENANT)
    created = store.add(name="touch PG", description="", tags=[], graph=_sample_graph(), params={})
    assert store.touch(created.id) is True
    assert store.touch(created.id) is True
    assert store.touch(created.id) is True

    eng2 = _new_engine()
    try:
        store2 = PgUserTemplateStore(eng2, TENANT)
        got = store2.get(created.id)
        assert got.usage_count == 3
        listed = store2.list()
        assert next(item for item in listed if item.id == created.id).usage_count == 3
    finally:
        eng2.dispose()

    assert store.touch("utpl-999") is False


def test_pg_and_memory_projection_match(engine):
    mem = UserTemplateStore()
    pg = PgUserTemplateStore(engine, TENANT)
    mem_t = mem.add(name="对拍", description="d", tags=["t"], category="c",
                    graph=_sample_graph(), params=PARAMS)
    pg_t = pg.add(name="对拍", description="d", tags=["t"], category="c",
                  graph=_sample_graph(), params=PARAMS)
    for field in ("name", "description", "tags", "category", "graph", "params",
                  "version", "usage_count"):
        assert getattr(pg_t, field) == getattr(mem_t, field), field
    # updated_at/created_at 都是非空 ISO
    assert mem_t.updated_at and pg_t.updated_at


def test_pg_store_tenant_isolation(engine):
    store_a = PgUserTemplateStore(engine, TENANT)
    store_b = PgUserTemplateStore(engine, OTHER_TENANT)
    created_a = store_a.add(name="A 租户", description="", tags=[], graph=_sample_graph(), params={})
    assert store_b.get(created_a.id) is None
    assert all(item.name != "A 租户" for item in store_b.list())

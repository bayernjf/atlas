# -*- coding: utf-8 -*-
"""打包 BQ（docs/133 D76+D71）U1491–U1496：网页嵌入配置/适配器草稿 PG 持久化集成测试。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5445/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_embeds_and_drafts_pg_integration.py

覆盖（docs/133 §5.2 补充）：
- U1491 迁移 054/055 应用成功：embed_configs/adapter_drafts 两表与列齐全；
- U1492 embed 配置与 token 落库后跨新 engine/新 store 实例可读（重启模拟）；
- U1493 adapter_drafts add/decision 落库后跨新实例可读（含 definition JSONB）；
- U1494 草稿 id 走全局 storage_id_seq，跨实例不复用 ad-1；
- U1495 PG 与进程内两档 store 投影逐键对拍一致；
- U1496 reset 只清本租户、不涉他租户。
"""

from __future__ import annotations

import os
from dataclasses import asdict

import pytest
from sqlalchemy import text

from atlas.embeds.store import EmbedConfig, EmbedStore
from atlas.embeds.pg_store import PgEmbedStore
from atlas.harness.definition import AdapterDefinition
from atlas.harness.drafts import AdapterDraftStore
from atlas.harness.pg_drafts import PgAdapterDraftStore

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run BQ PG integration",
    ),
]

TENANT = "bqpgtenant"
OTHER_TENANT = "bqpgother"
GRAPH = "g-bq-pg"


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    yield eng
    eng.dispose()


def _definition() -> dict:
    return asdict(AdapterDefinition(
        name="bq-pg-api",
        description="集成测试定义",
        base_url="https://api.bq-pg.test",
        auth_type="none",
        permission="read",
        capabilities=[
            {
                "name": "ping",
                "description": "探活",
                "method": "GET",
                "path": "/ping",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                "permission": "read",
                "is_idempotent": True,
            }
        ],
    ))


def test_u1491_migrations_054_055_apply(engine):
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    assert "embed_configs" in tables
    assert "adapter_drafts" in tables
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'embed_configs'"
                )
            )
        }
    assert {"tenant_id", "graph_id", "enabled", "token_ttl_seconds"} <= cols
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'adapter_drafts'"
                )
            )
        }
    assert {"tenant_id", "draft_id", "definition", "decision_status"} <= cols


def test_u1492_embed_pg_roundtrip_across_instances(engine):
    s1 = PgEmbedStore(engine, TENANT)
    s1.upsert(GRAPH, enabled=True, token_ttl_seconds=7200)
    # 新实例（重启模拟）
    s2 = PgEmbedStore(_new_engine(), TENANT)
    cfg = s2.get(GRAPH)
    assert cfg is not None
    assert cfg["enabled"] is True
    assert cfg["token_ttl_seconds"] == 7200


def test_u1493_drafts_pg_roundtrip_across_instances(engine):
    s1 = PgAdapterDraftStore(engine, TENANT)
    draft = s1.add(_definition())
    s1.set_decision(draft["draft_id"], "adopted")
    s2 = PgAdapterDraftStore(_new_engine(), TENANT)
    rows = s2.list(adopted=True)
    assert any(r["draft_id"] == draft["draft_id"] for r in rows)
    got = s2.get(draft["draft_id"])
    assert got is not None and got["definition"]["name"] == "bq-pg-api"
    assert got["decision_status"] == "adopted"


def test_u1494_draft_id_not_reused_across_instances(engine):
    s1 = PgAdapterDraftStore(engine, TENANT)
    first = s1.add(_definition())
    s2 = PgAdapterDraftStore(_new_engine(), TENANT)
    second = s2.add(_definition())
    assert first["draft_id"] != second["draft_id"]
    assert first["draft_id"].startswith("ad-")
    assert second["draft_id"].startswith("ad-")


def test_u1495_pg_and_memory_projection_match(engine):
    pg = PgEmbedStore(engine, TENANT)
    mem = EmbedStore()
    pg.upsert(GRAPH, enabled=True, token_ttl_seconds=3600)
    mem.upsert(GRAPH, enabled=True, token_ttl_seconds=3600)
    a = pg.get(GRAPH)
    b = mem.get(GRAPH)
    assert a is not None and b is not None
    assert (a["graph_id"], a["enabled"], a["token_ttl_seconds"]) == (
        b["graph_id"],
        b["enabled"],
        b["token_ttl_seconds"],
    )
    # 草稿档对拍：同一定义在两档 store 的投影逐键一致
    pgs = PgAdapterDraftStore(engine, OTHER_TENANT)
    mems = AdapterDraftStore()
    pd = pgs.add(_definition())
    md = mems.add(_definition())
    pgs.set_decision(pd["draft_id"], "dismissed")
    mems.set_decision(md["draft_id"], "dismissed")
    got_p = pgs.get(pd["draft_id"])
    got_m = mems.get(md["draft_id"])
    assert got_p is not None and got_m is not None
    assert (got_p["definition"]["name"], got_p["decision_status"]) == (
        got_m["definition"]["name"],
        got_m["decision_status"],
    )


def test_u1496_reset_scoped_to_tenant(engine):
    s1 = PgEmbedStore(engine, TENANT)
    s1.upsert(GRAPH, enabled=True, token_ttl_seconds=3600)
    s1.reset()
    assert s1.get(GRAPH) is None
    # 他租户不受影响
    other = PgEmbedStore(_new_engine(), OTHER_TENANT)
    other.upsert(GRAPH, enabled=True, token_ttl_seconds=3600)
    s1.reset()
    assert other.get(GRAPH) is not None

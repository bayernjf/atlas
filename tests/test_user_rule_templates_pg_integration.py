# -*- coding: utf-8 -*-
"""打包 ZS（docs/102）U1230：用户告警规则模板 PG 持久化集成测试。

docker run -d --name atlas-zs-test-pg -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas -e POSTGRES_DB=atlas -p 5445:5432 pgvector/pgvector:pg16
DATABASE_URL="postgresql+psycopg://atlas:atlas@localhost:5445/atlas" ATLAS_RUN_INTEGRATION=1 \
  .venv/bin/pytest -m integration tests/test_user_rule_templates_pg_integration.py

覆盖（docs/102 §4 U1230 的 PG 半边）：
- 迁移 045 应用成功：user_rule_templates 表与列齐全（config/tags JSONB NOT NULL）；
- add 落库后跨新 engine/新 store 实例可读（重启模拟），id=urt-N、元字段齐全；
- update 整体替换 name/description/tags/config，id/created_at 不变；
- delete 后不可读；重名 add/update 抛 RuleTemplateNameConflict（409 语义）；
- 租户隔离：A 租户写不出现于 B 租户。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from atlas.monitoring.pg_rule_user_store import PgUserRuleTemplateStore
from atlas.monitoring.rule_user_store import RuleTemplateNameConflict

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run rule template PG integration",
    ),
]

TENANT = "pazstest"
OTHER_TENANT = "pazsother"


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
            text("DELETE FROM user_rule_templates WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    yield eng
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM user_rule_templates WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT, "b": OTHER_TENANT},
        )
    eng.dispose()


def _sample_config() -> dict:
    return {
        "run_error": {"enabled": True},
        "node_failed": {"enabled": True},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
        "custom": [],
        "escalation_ack_minutes": None,
        "recovery_healthy_streak": 1,
        "recovery_cooldown_minutes": None,
    }


def test_migration_045_creates_table_and_columns(engine):
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'user_rule_templates'"
                )
            ).all()
        }
    assert {
        "tenant_id",
        "id",
        "seq",
        "name",
        "description",
        "tags",
        "config",
        "created_at",
    } <= cols


def test_pg_store_crud_and_restart_read(engine):
    store = PgUserRuleTemplateStore(engine, TENANT)
    created = store.add(
        name="PG 规则模板",
        description="d",
        tags=["t1", "t2"],
        config=_sample_config(),
    )
    assert created.id == "urt-1"
    assert created.tags == ["t1", "t2"]

    # 跨新 engine/新 store 实例读回（重启模拟）
    fresh = PgUserRuleTemplateStore(_new_engine(), TENANT)
    got = fresh.get(created.id)
    assert got is not None
    assert got.name == "PG 规则模板"
    assert got.config == _sample_config()
    assert got.created_at == created.created_at

    # update 整体替换：id/created_at 不变，config/tags 替换
    new_config = {**_sample_config(), "consecutive_failures": {"enabled": True, "threshold": 5}}
    updated = fresh.update(
        created.id,
        name="PG 规则模板-2",
        description="e",
        tags=["sre"],
        config=new_config,
    )
    assert updated is not None
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert updated.name == "PG 规则模板-2"
    assert updated.tags == ["sre"]
    assert updated.config["consecutive_failures"]["threshold"] == 5

    # delete 后不可读
    assert fresh.delete(created.id) is True
    assert fresh.get(created.id) is None


def test_pg_store_name_conflict_and_tenant_isolation(engine):
    store_a = PgUserRuleTemplateStore(engine, TENANT)
    store_b = PgUserRuleTemplateStore(engine, OTHER_TENANT)
    assert store_a.add(name="同名校验", description="", tags=[], config=_sample_config()).id == "urt-1"
    # 同租户重名 → RuleTemplateNameConflict（409 语义）
    with pytest.raises(RuleTemplateNameConflict):
        store_a.add(name="同名校验", description="", tags=[], config=_sample_config())
    # update 撞名同样 409
    other = store_a.add(name="其它", description="", tags=[], config=_sample_config())
    with pytest.raises(RuleTemplateNameConflict):
        store_a.update(other.id, name="同名校验", description="", tags=[], config=_sample_config())
    # 租户隔离：B 租户读不到 A 租户的任何模板
    assert store_b.list() == []
    assert store_b.get("urt-1") is None

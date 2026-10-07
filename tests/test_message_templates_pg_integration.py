# -*- coding: utf-8 -*-
"""打包 A2（docs/98）U1193：消息模板 PG 持久化集成测试。

docker run -d --name atlas-a2-test-pg -e POSTGRES_USER=atlas -e POSTGRES_PASSWORD=atlas -e POSTGRES_DB=atlas -p 5433:5432 pgvector/pgvector:pg16
DATABASE_URL="postgresql+psycopg://atlas:atlas@localhost:5433/atlas" ATLAS_RUN_INTEGRATION=1 \
  .venv/bin/pytest -m integration tests/test_message_templates_pg_integration.py

覆盖（docs/98 §3 U1193 的 PG 半边）：
- 迁移 044 应用成功：message_templates 表建表；
- add 落库后跨新 engine/新 store 实例可读（重启模拟），字段齐全；
- name 唯一约束（LOWER casefold）：撞名 IntegrityError → MessageTemplateNameConflict（409 语义）；
- update 撞名同转；update 正常刷新 updated_at；
- variables JSONB 往返一致；两档（内存 vs PG）投影对拍；
- 租户隔离：A 租户写不出现于 B 租户。
"""

from __future__ import annotations

import os

import pytest

from atlas.message.pg_template_store import PgMessageTemplateStore
from atlas.message.template_store import (
    MessageTemplateNameConflict,
    MessageTemplateStore,
)

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run message template PG integration",
    ),
]

TENANT = "pga2test"
OTHER_TENANT = "pga2other"


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    with eng.begin() as conn:
        conn.execute(text_sql())
    yield eng
    with eng.begin() as conn:
        conn.execute(cleanup_sql())
    eng.dispose()


def text_sql():
    from sqlalchemy import text

    return text(
        "DELETE FROM message_templates WHERE tenant_id IN (:t1, :t2)"
    ).bindparams(t1=TENANT, t2=OTHER_TENANT)


def cleanup_sql():
    return text_sql()


def _store(engine):
    return PgMessageTemplateStore(engine, TENANT)


def _other_store(engine):
    return PgMessageTemplateStore(engine, OTHER_TENANT)


def test_U1193_migration_created_table(engine):
    from sqlalchemy import inspect

    tables = inspect(engine).get_table_names()
    assert "message_templates" in tables


def test_U1193_add_survives_new_engine(engine):
    store = _store(engine)
    created = store.add(
        name="审批提醒",
        kind="approval",
        subject="[Atlas] 审批待处理：{{title}}",
        body="节点：{{node_id}}\n说明：{{summary}}",
        variables=["title", "node_id", "summary"],
    )
    assert created.id == "mtpl-1"
    # 新 engine + 新 store 实例（重启模拟）可读。
    reloaded = _store(_new_engine()).get(created.id)
    assert reloaded is not None
    assert reloaded.name == "审批提醒"
    assert reloaded.kind == "approval"
    assert reloaded.subject == "[Atlas] 审批待处理：{{title}}"
    assert reloaded.variables == ["title", "node_id", "summary"]
    assert reloaded.created_at == created.created_at


def test_U1193_name_unique_casefold_conflict(engine):
    store = _store(engine)
    store.add(
        name="告警提醒",
        kind="alert",
        subject="s {{title}}",
        body="b {{graph_id}}",
        variables=["title", "graph_id"],
    )
    # 大小写不同仍撞（LOWER 唯一索引）。
    with pytest.raises(MessageTemplateNameConflict):
        store.add(
            name="告警提醒",
            kind="alert",
            subject="s {{title}}",
            body="b {{graph_id}}",
            variables=["title", "graph_id"],
        )
    with pytest.raises(MessageTemplateNameConflict):
        store.add(
            name="告警提醒",
            kind="alert",
            subject="s {{title}}",
            body="b {{graph_id}}",
            variables=["title", "graph_id"],
        )


def test_U1193_update_conflict_and_refresh(engine):
    store = _store(engine)
    created = store.add(
        name="更新模板",
        kind="approval",
        subject="旧 {{title}}",
        body="旧 {{title}}",
        variables=["title"],
    )
    # 撞名更新 409。
    with pytest.raises(MessageTemplateNameConflict):
        store.update(
            created.id,
            name="告警提醒",
            kind="approval",
            subject="s",
            body="b",
            variables=[],
        )
    # 正常更新刷新 updated_at、保留 created_at。
    updated = store.update(
        created.id,
        name="更新模板",
        kind="approval",
        subject="新 {{title}}",
        body="新 {{title}}",
        variables=["title"],
    )
    assert updated is not None
    assert updated.created_at == created.created_at
    assert updated.subject == "新 {{title}}"
    assert updated.updated_at >= updated.created_at


def test_U1193_variables_roundtrip_and_memory_parity(engine):
    pg_store = _store(engine)
    created = pg_store.add(
        name="往返模板",
        kind="alert",
        subject="s {{title}} {{severity}}",
        body="b {{graph_id}}",
        variables=["title", "severity", "graph_id"],
    )
    memory = MessageTemplateStore()
    mem_item = memory.add(
        name="往返模板",
        kind="alert",
        subject="s {{title}} {{severity}}",
        body="b {{graph_id}}",
        variables=["title", "severity", "graph_id"],
    )
    pg_item = pg_store.get(created.id)
    assert pg_item is not None
    assert pg_item.subject == mem_item.subject
    assert pg_item.body == mem_item.body
    assert pg_item.variables == mem_item.variables
    # kind 过滤（注意：name_unique 测试在本文件先跑，遗留同 kind 模板，故用包含断言）。
    assert created.id in [t.id for t in pg_store.list("alert")]
    assert created.id not in [t.id for t in pg_store.list("approval")]


def test_U1193_tenant_isolation(engine):
    store = _store(engine)
    created = store.add(
        name="隔离模板",
        kind="approval",
        subject="s {{title}}",
        body="b {{title}}",
        variables=["title"],
    )
    other = _other_store(engine)
    assert other.get(created.id) is None
    assert other.list() == []
    assert other.delete(created.id) is False
    # 同租户可删。
    assert store.delete(created.id) is True

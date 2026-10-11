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
    from atlas.storage.migrations import apply_pending
    apply_pending(engine)


@pytest.fixture(scope="module")
def pg_engine():
    from atlas.memory.database import create_database_engine

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def pg_store(pg_engine):
    from atlas.storage.pg import PgBackend

    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE tenant_id = :t"), {"t": TENANT})
    store = PgBackend(pg_engine).memory_store(TENANT)
    yield store
    with pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE tenant_id = :t"), {"t": TENANT})


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
        # 006 建的 idx_memory_items_embedding（IVFFlat）已由迁移 039 移除（U201 冷启动空集缺陷实证＋
        # ANN 并列距离顺序不确定破坏两档对拍 U94；006 注释本就声明"降级为暂不建 ANN，精确余弦已够沙盘"）。
        # 真实数据规模另立批建 HNSW 时同步反转本断言。
        assert "idx_memory_items_embedding" not in indexes
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


# ---- docs/108 打包 AA：知识库 / RAG（U1259 PG 腿 + U1265 两档对拍） ----


def test_aa_u1259_pg_migration_knowledge_kind_writable_and_others_untouched(pg_store, pg_engine):
    """迁移 046 后 kind=knowledge 可写可读；fact/preference 不受影响（PG 直连）。"""
    # pg_store 为 module scope 共享，先清空以免前序测试种子数据污染计数
    pg_store.clear()
    with pg_engine.begin() as conn:
        cols = [row[0] for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'memory_items'"
        )).all()]
    assert "kind" in cols and "meta" in cols and "embedding" in cols
    item = pg_store.remember(
        kind="knowledge",
        content="PG 知识：SOP 演示环境重置流程",
        metadata={"category": "sop"},
    )
    assert item["kind"] == "knowledge"
    assert item["metadata"]["category"] == "sop"
    fact = pg_store.remember(kind="fact", content="PG 既有事实")
    assert fact["kind"] == "fact"
    pref = pg_store.remember(kind="preference", content="PG 既有偏好")
    assert pref["kind"] == "preference"
    assert len(pg_store.list(kind="knowledge")) == 1
    assert len(pg_store.list(kind="fact")) == 1
    assert len(pg_store.list(kind="preference")) == 1
    # 恢复标准种子，保持后序测试前提
    pg_store.clear()
    _seed(pg_store)


def test_aa_u1265_pg_and_inmemory_parity_for_knowledge(pg_store):
    """两档对拍（docs/108 U1265）：同一文本 import 后，PG 与进程内 search 结果一致。"""
    from atlas.memory.items import MemoryStore

    inmem = MemoryStore()
    text = "FAQ：登录失败如何排查。\n\nSOP：发货流程三步。"
    # PG 档 import（模拟端点分段逻辑：段落优先，两段各一条）
    for paragraph in (p.strip() for p in text.split("\n\n") if p.strip()):
        pg_store.remember(kind="knowledge", content=paragraph, metadata={"category": "faq"})
        inmem.remember(kind="knowledge", content=paragraph, metadata={"category": "faq"})
    pg_hits = pg_store.recall("登录失败排查", kind="knowledge", category="faq", top_k=3)
    mem_hits = inmem.recall("登录失败排查", kind="knowledge", category="faq", top_k=3)
    assert pg_hits and mem_hits
    assert [h["content"] for h in pg_hits] == [h["content"] for h in mem_hits]
    assert all(h["kind"] == "knowledge" for h in pg_hits)
    assert all(h["metadata"].get("category") == "faq" for h in pg_hits)
    # category 过滤在 PG 侧同样收敛（rule 只回 rule）
    pg_store.remember(kind="knowledge", content="退款规则：保留包装", metadata={"category": "rule"})
    rules = pg_store.recall("退款", kind="knowledge", category="rule", top_k=3)
    assert all(h["metadata"].get("category") == "rule" for h in rules)
    pg_store.clear()


# --------------------------------------------------------------------------- #
# 打包 AK（docs/122 D-4/D-5）：update_policy 与 decay 的两档对拍
# 判据与进程内档（tests/test_memory_update_policy_decay.py）逐条同名，防"两档语义漂移"。
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ak_store(pg_store):
    """每条用例从空库开始，并固定 created_at 序列（衰减判据要可确定复现）。"""
    pg_store.clear()
    stamps = iter(
        [
            "2026-10-10T12:00:00+00:00",
            "2026-10-11T12:00:00+00:00",
            "2026-10-12T12:00:00+00:00",
            "2026-10-13T12:00:00+00:00",
        ]
    )
    import atlas.storage.pg as pg_module

    original = pg_module._now_iso
    pg_module._now_iso = lambda: next(stamps)
    try:
        yield pg_store
    finally:
        pg_module._now_iso = original
        pg_store.clear()


def _ak_remember(store, content, scope, **kwargs):
    return store.remember(kind="fact", content=content, scope=scope, **kwargs)


def test_u1337_pg_overwrite_matches_the_in_process_semantics(ak_store):
    first = _ak_remember(ak_store, "对象 A 当前状态：待审核", {"object_id": "A"})
    second = _ak_remember(
        ak_store, "对象 A 当前状态：已退款", {"object_id": "A"},
        update_policy="overwrite", dedup_keys=["object_id"],
    )
    assert second["id"] == first["id"] and second["created_at"] == first["created_at"]
    assert second["content"] == "对象 A 当前状态：已退款"
    assert len(ak_store.list()) == 1
    # embedding 必须跟着内容重算，否则"更新了行、检索还是旧的"＝静默失效
    hits = ak_store.recall("已退款", top_k=3)
    assert [hit["content"] for hit in hits] == ["对象 A 当前状态：已退款"]


def test_u1337b_pg_dedup_requires_the_same_scope(ak_store):
    """正控：没给去重键时，同内容不同 scope 不能被合并。"""
    _ak_remember(ak_store, "已退过款", {"object_id": "A"})
    _ak_remember(ak_store, "已退过款", {"object_id": "B"})
    assert len(ak_store.list()) == 2


def test_u1338_pg_version_increments_per_group(ak_store):
    _ak_remember(ak_store, "规则 v1", {"object_id": "R"}, update_policy="version",
                 dedup_keys=["object_id"])
    second = _ak_remember(ak_store, "规则 v2", {"object_id": "R"}, update_policy="version",
                          dedup_keys=["object_id"])
    third = _ak_remember(ak_store, "规则 v3", {"object_id": "R"}, update_policy="version",
                         dedup_keys=["object_id"])
    assert (second["metadata"]["version"], third["metadata"]["version"]) == ("2", "3")
    assert len(ak_store.list()) == 3


def test_u1339_pg_ask_reports_conflict_without_writing(ak_store):
    existing = _ak_remember(ak_store, "旧结论", {"object_id": "A"})
    answer = _ak_remember(ak_store, "新结论", {"object_id": "A"},
                          update_policy="ask", dedup_keys=["object_id"])
    assert answer["conflict"] is True and answer["existing_id"] == existing["id"]
    assert len(ak_store.list()) == 1


def test_u1340_pg_decay_reorders_the_same_way_as_the_in_process_tier(ak_store):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    _ak_remember(ak_store, "客户偏好极速退款", {"object_id": "old"})
    _ak_remember(ak_store, "客户偏好极速退款", {"object_id": "new"})
    plain = ak_store.recall("客户偏好极速退款", top_k=2)
    assert [item["scope"]["object_id"] for item in plain] == ["old", "new"]
    decayed = ak_store.recall("客户偏好极速退款", top_k=2,
                              decay={"enabled": True, "half_life_days": 30},
                              now_override=now + timedelta(days=3))
    assert [item["scope"]["object_id"] for item in decayed] == ["new", "old"]


def test_u1341_pg_decay_off_is_the_pre_batch_query_path(ak_store):
    _ak_remember(ak_store, "客户偏好极速退款", {"object_id": "old"})
    _ak_remember(ak_store, "客户偏好极速退款", {"object_id": "new"})
    baseline = ak_store.recall("客户偏好极速退款", top_k=2)
    assert ak_store.recall("客户偏好极速退款", top_k=2, decay=None) == baseline
    assert ak_store.recall("客户偏好极速退款", top_k=2,
                           decay={"enabled": False, "half_life_days": 7}) == baseline


# ---- docs/14 D65 安全切片：dedup_similarity 语义近似去重的两档对拍（缺省关） ----


def test_u1342_pg_similarity_dedup_matches_in_process_tier(ak_store):
    """同 scope 近义改写按阈值 overwrite 合并（id 不变、不翻倍）；不同 scope 绝不互相吃掉。"""
    from atlas.memory.items import MemoryStore

    pg = ak_store
    mem = MemoryStore()
    for store in (pg, mem):
        first = store.remember(kind="fact", content="订单 o-1 已退款", scope={"order_id": "o-1"})
        second = store.remember(
            kind="fact", content="订单 o-1 已退款 88 元",
            scope={"order_id": "o-1"}, dedup_similarity=0.85,
        )
        assert second["id"] == first["id"], "近似命中走 overwrite，同一条被更新而非新增"
        assert len(store.list()) == 1
        # 缺省关：不传阈值时，哪怕文本高度近似也不做语义合并（接入前行为）
        store.clear()
        store.remember(kind="fact", content="订单 o-1 已退款", scope={"order_id": "o-1"})
        store.remember(kind="fact", content="订单 o-1 已退款 88 元", scope={"order_id": "o-1"})
        assert len(store.list()) == 2
        store.clear()
        # scope 隔离：换了 order_id，即便措辞高度近似也不得合并
        store.remember(kind="fact", content="订单 o-1 已退款", scope={"order_id": "o-1"})
        store.remember(kind="fact", content="订单 o-2 已退款", scope={"order_id": "o-2"},
                       dedup_similarity=0.85)
        assert len(store.list()) == 2
        store.clear()

# -*- coding: utf-8 -*-
"""docs/60 §6 G5：消息投递日志 PG 化集成测试（候选 U727–U742）。

仅当 ATLAS_RUN_INTEGRATION=1 且 DATABASE_URL 指向可用 PG 时运行；迁移 apply 到 026。
核心相对内存档的可观察变化：跨 store 实例（模拟重启/多实例）保留投递日志、ring 200
淘汰落库、群发同一 message id 多条不冲突、reset 清本租户且不影响他租户；REST 形状与
内存档一致（内存档 ring 行为另见 test_message_im.py）。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas.message.deliveries import DELIVERY_RING_SIZE, PgDeliveryStore
from atlas.message.service import DeliveryRecord, MessageService

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run message delivery PG integration",
)


def _rec(
    message_id: str,
    *,
    channel: str = "webhook",
    to: list[str] | None = None,
    status: str = "delivered:webhook",
    attempts: int = 1,
    error_code: str | None = None,
    error_message: str | None = None,
) -> DeliveryRecord:
    return DeliveryRecord(
        id=message_id,
        channel=channel,
        to=to or ["https://example.com/hook"],
        subject="s",
        sentAt=datetime.now(timezone.utc).isoformat(),
        status=status,
        attempts=attempts,
        elapsedMs=3,
        errorCode=error_code,
        errorMessage=error_message,
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
    tenant = f"pgmsg-{uuid.uuid4().hex[:8]}"

    def make():
        return PgDeliveryStore(engine, tenant)

    primary = make()
    yield primary, make, tenant, engine
    primary.clear()


def test_record_list_desc_and_persists_across_instances(store_factory):
    store, make, _tenant, _engine = store_factory
    store.record(_rec("m-1"))
    store.record(_rec("m-2", status="failed", attempts=3, error_code="WEBHOOK_SEND_FAILED",
                      error_message="boom"))

    fresh = make()  # 模拟重启：新 store 实例、同一库
    items = fresh.list(100)
    assert [item["id"] for item in items] == ["m-2", "m-1"]  # 倒序
    failed = items[0]
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3
    assert failed["errorCode"] == "WEBHOOK_SEND_FAILED"
    assert failed["errorMessage"] == "boom"
    assert failed["to"] == ["https://example.com/hook"]


def test_fanout_same_message_id_multiple_rows_no_conflict(store_factory):
    store, make, _t, _e = store_factory
    # 群发：同一 message id 逐目标 3 条
    for url in ("u1", "u2", "u3"):
        store.record(_rec("same-id", to=[f"https://example.com/{url}"]))
    items = make().list(100)
    assert len(items) == 3
    assert {item["to"][0] for item in items} == {
        "https://example.com/u1",
        "https://example.com/u2",
        "https://example.com/u3",
    }


def test_ring_trims_to_200(store_factory):
    store, make, _t, _e = store_factory
    for index in range(DELIVERY_RING_SIZE + 5):
        store.record(_rec(f"m-{index:04d}"))
    items = make().list(DELIVERY_RING_SIZE + 100)
    assert len(items) == DELIVERY_RING_SIZE
    # 最旧 5 条被裁，保留 m-0005..m-0204，倒序首个为最新
    ids = {item["id"] for item in items}
    assert "m-0000" not in ids and "m-0004" not in ids
    assert "m-0005" in ids and items[0]["id"] == f"m-{DELIVERY_RING_SIZE + 4:04d}"


def test_list_limit_clamp(store_factory):
    store, _make, _t, _e = store_factory
    for index in range(5):
        store.record(_rec(f"c-{index}"))
    assert len(store.list(2)) == 2          # 小 limit 生效
    assert len(store.list(99999)) == 5      # 超过 ring 上限被 clamp 到 200，不报错
    assert len(store.list(0)) == 1          # 0 被 clamp 到 1，只回最新一条
    assert store.list(1)[0]["id"] == "c-4"


def test_clear_only_own_tenant(store_factory, engine):
    store, make, tenant, _engine = store_factory
    other = PgDeliveryStore(engine, f"{tenant}-other")
    store.record(_rec("mine"))
    other.record(_rec("theirs"))

    store.clear()
    assert make().list(100) == []
    # 他租户不受影响
    assert [item["id"] for item in other.list(100)] == ["theirs"]
    other.clear()


def test_message_service_persists_via_pg_store_across_restart(store_factory):
    _store, make, _t, _e = store_factory
    svc1 = MessageService(delivery_store=make())
    record = svc1.send("webhook", "https://example.com/demo", "hi", "body")
    assert record["delivered"] == "in_process"  # 无真实 sender：仅记录

    # 新 service + 新 store 实例（模拟重启）仍可见投递日志
    svc2 = MessageService(delivery_store=make())
    items = svc2.list_deliveries()
    assert len(items) == 1
    assert items[0]["channel"] == "webhook"
    assert items[0]["status"] == "in_process"

    # reset 经 MessageService 清表
    svc2.reset()
    assert make().list(100) == []

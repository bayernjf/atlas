# -*- coding: utf-8 -*-
"""打包 U：出站 DLQ 的 PG 集成（docs/82；U961）。

仅当 ATLAS_RUN_INTEGRATION=1 且 DATABASE_URL 指向可用 PG（pgvector 镜像）时运行；
engine 顺序应用全部迁移（含 032 body 列）。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas.message.deliveries import PgDeliveryStore
from atlas.message.service import DeliveryRecord, MessageSendError, MessageService

pytestmark = pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1" or not os.environ.get("DATABASE_URL"),
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run DLQ PG integration",
)


class _FailingWebhook:
    def send(self, url, payload, secret=None):
        raise RuntimeError("connection refused")


class _WorkingWebhook:
    def send(self, url, payload, secret=None):
        pass


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
def tenant(engine):
    tenant_id = f"dlqpg-{uuid.uuid4().hex[:8]}"
    yield tenant_id, engine
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(
            text("DELETE FROM message_deliveries WHERE tenant_id LIKE :prefix"),
            {"prefix": f"{tenant_id}%"},
        )


def _failed_record(message_id: str, *, body: str | None) -> DeliveryRecord:
    return DeliveryRecord(
        id=message_id,
        channel="webhook",
        to=["https://example.com/hook"],
        subject="s",
        sentAt=datetime.now(timezone.utc).isoformat(),
        status="failed",
        attempts=3,
        elapsedMs=5,
        errorCode="WEBHOOK_SEND_FAILED",
        errorMessage="boom",
        body=body if body is not None else "",
    )


def test_body_persisted_and_read_across_connections(tenant):
    tenant_id, engine = tenant
    store = PgDeliveryStore(engine, tenant_id)
    store.record(_failed_record("m-1", body="原始正文"))

    fresh = PgDeliveryStore(engine, tenant_id)
    row = fresh.list(100, status="failed")[0]
    assert row["body"] == "原始正文"
    assert fresh.get(row["seq"])["body"] == "原始正文"
    delivered = fresh.list(100, status="delivered")
    assert delivered == []


def test_replay_writes_new_row_and_keeps_original(tenant):
    tenant_id, engine = tenant
    failing = MessageService(
        webhook_sender=_FailingWebhook(),
        retry_delays=(),
        sleep_func=lambda _seconds: None,
        delivery_store=PgDeliveryStore(engine, tenant_id),
    )
    with pytest.raises(MessageSendError):
        failing.send("webhook", "https://example.com/hook", "subject", "body")
    failed_seq = PgDeliveryStore(engine, tenant_id).list(100, status="failed")[0]["seq"]

    replay = MessageService(
        webhook_sender=_WorkingWebhook(),
        delivery_store=PgDeliveryStore(engine, tenant_id),
    )
    result = replay.replay_failed(failed_seq)
    assert result["replayOf"] == failed_seq
    assert result["deliveries"][0]["status"] == "delivered:webhook"

    store = PgDeliveryStore(engine, tenant_id)
    assert store.get(failed_seq)["status"] == "failed"
    rows = store.list(100)
    assert {row["status"] for row in rows} == {"failed", "delivered:webhook"}


def test_legacy_null_body_blocks_replay(tenant):
    from sqlalchemy import text

    tenant_id, engine = tenant
    store = PgDeliveryStore(engine, tenant_id)
    store.record(_failed_record("legacy", body=""))
    failed_seq = store.list(100, status="failed")[0]["seq"]
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE message_deliveries SET body = NULL "
                "WHERE tenant_id = :t AND seq = :seq"
            ),
            {"t": tenant_id, "seq": failed_seq},
        )

    replay = MessageService(delivery_store=PgDeliveryStore(engine, tenant_id))
    with pytest.raises(MessageSendError) as exc:
        replay.replay_failed(failed_seq)
    assert exc.value.code == "DLQ_BODY_UNAVAILABLE"

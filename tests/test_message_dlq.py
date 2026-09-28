# -*- coding: utf-8 -*-
"""打包 U：出站消息 DLQ——失败投递可过滤、可重放（docs/82；U958–U960、U963）。

失败行用假 webhook sender 制造，零真实渠道；PG 档落列与跨实例见
test_message_dlq_pg.py；端点鉴权与 HTTP 映射见 test_api_message_dlq.py。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas.message.deliveries import InMemoryDeliveryStore
from atlas.message.service import (
    DeliveryRecord,
    MessageSendError,
    MessageService,
)


def _record(message_id: str, *, status: str, body: str = "hello-body") -> DeliveryRecord:
    return DeliveryRecord(
        id=message_id,
        channel="webhook",
        to=["https://example.com/hook"],
        subject="s",
        sentAt=datetime.now(timezone.utc).isoformat(),
        status=status,
        attempts=1,
        elapsedMs=2,
        body=body,
    )


# ---- U958：body 读回与 status 过滤 ----


def test_body_persisted_and_read_back():
    store = InMemoryDeliveryStore()
    store.record(_record("m-1", status="failed", body="原始正文"))
    row = store.list(100)[0]
    assert row["body"] == "原始正文"
    assert row["seq"] == 1
    assert store.get(1)["body"] == "原始正文"


def test_list_status_filter():
    store = InMemoryDeliveryStore()
    store.record(_record("m-1", status="failed"))
    store.record(_record("m-2", status="delivered:webhook"))
    store.record(_record("m-3", status="in_process"))

    failed = store.list(100, status="failed")
    assert [item["id"] for item in failed] == ["m-1"]
    delivered = store.list(100, status="delivered")
    assert [item["id"] for item in delivered] == ["m-2"]
    all_rows = store.list(100)
    assert [item["id"] for item in all_rows] == ["m-3", "m-2", "m-1"]


def test_list_unknown_status_raises():
    with pytest.raises(ValueError):
        InMemoryDeliveryStore().list(100, status="bogus")


# ---- 假 sender ----


class _FailingWebhook:
    def send(self, url, payload, secret=None):
        raise RuntimeError("connection refused")


class _WorkingWebhook:
    def __init__(self) -> None:
        self.received: list[str] = []

    def send(self, url, payload, secret=None):
        self.received.append(url)


def _service(store: InMemoryDeliveryStore | None = None, sender: object | None = None) -> MessageService:
    service = MessageService(
        webhook_sender=sender,
        retry_delays=(),
        sleep_func=lambda _seconds: None,
        delivery_store=store,
    )
    return service


# ---- U959：重放成功路径 ----


def test_replay_failed_success_keeps_original_row():
    store = InMemoryDeliveryStore()
    failing = _service(store, _FailingWebhook())
    with pytest.raises(MessageSendError) as exc:
        failing.send("webhook", "https://example.com/hook", "subject-原", "body-原")
    assert exc.value.code == "WEBHOOK_SEND_FAILED"

    failed_row = store.list(100, status="failed")[0]
    assert failed_row["body"] == "body-原"

    replay = _service(store, _WorkingWebhook())
    result = replay.replay_failed(failed_row["seq"])
    assert result is not None
    assert result["replayOf"] == failed_row["seq"]
    new_row = result["deliveries"][0]
    assert new_row["status"] == "delivered:webhook"
    assert new_row["to"] == ["https://example.com/hook"]
    assert new_row["subject"] == "subject-原"
    assert new_row["body"] == "body-原"
    assert new_row["seq"] != failed_row["seq"]

    original = store.get(failed_row["seq"])
    assert original["status"] == "failed"


# ---- U960：service 层错误态 ----


def test_replay_missing_returns_none():
    assert _service().replay_failed(999) is None


def test_replay_non_failed_raises_conflict():
    store = InMemoryDeliveryStore()
    store.record(_record("m-1", status="delivered:webhook"))
    with pytest.raises(MessageSendError) as exc:
        _service(store).replay_failed(1)
    assert exc.value.code == "DLQ_NOT_FAILED"


def test_replay_empty_body_raises_unavailable():
    store = InMemoryDeliveryStore()
    store.record(_record("m-1", status="failed", body=""))
    with pytest.raises(MessageSendError) as exc:
        _service(store).replay_failed(1)
    assert exc.value.code == "DLQ_BODY_UNAVAILABLE"


# ---- U963：群发逐目标失败，重放只发该目标 ----


def test_fanout_replay_scopes_to_single_target():
    store = InMemoryDeliveryStore()
    failing = _service(store, _FailingWebhook())
    targets = ["https://a.example.com/hook", "https://b.example.com/hook"]
    with pytest.raises(MessageSendError):
        failing.send("webhook", targets, "s", "body")

    failed_rows = store.list(100, status="failed")
    assert len(failed_rows) == 2
    by_target = {row["to"][0]: row for row in failed_rows}

    working = _WorkingWebhook()
    replay = _service(store, working)
    result_a = replay.replay_failed(by_target["https://a.example.com/hook"]["seq"])
    assert result_a["deliveries"][0]["to"] == ["https://a.example.com/hook"]
    result_b = replay.replay_failed(by_target["https://b.example.com/hook"]["seq"])
    assert result_b["deliveries"][0]["to"] == ["https://b.example.com/hook"]

    assert working.received == [
        "https://a.example.com/hook",
        "https://b.example.com/hook",
    ]
    for row in failed_rows:
        assert store.get(row["seq"])["status"] == "failed"

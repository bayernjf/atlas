# -*- coding: utf-8 -*-
"""docs/61 §3 H2：已决审批历史存储与 broker 接线（候选 U755–U770）。

覆盖两档共同形状（内存档在此、PG 档见 test_approval_history_pg.py）、epoch→ISO 转换、
以及最关键的两条纪律：**四类决策来源都落一次历史**（人工/邮件链接/预置 inputs 经
resolve，超时经 complete_timeout），**历史写入失败绝不阻断审批放行**。
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timezone

import pytest

from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.history import (
    HISTORY_RING_SIZE,
    ApprovalHistoryEntry,
    InMemoryApprovalHistoryStore,
    epoch_to_iso,
)


def _entry(token: str, **overrides) -> ApprovalHistoryEntry:
    base = {
        "token": token,
        "node_id": "apr-1",
        "graph_id": "g1",
        "summary": "请确认退款",
        "approver": "boss",
        "decision": "approved",
        "resolved_by": "human",
        "comment": "",
        "created_at": epoch_to_iso(1_700_000_000.0),
        "resolved_at": epoch_to_iso(1_700_000_010.0),
    }
    return ApprovalHistoryEntry(**{**base, **overrides})


def _request(broker: ApprovalBroker, *, node_id: str = "apr-1", card: str | None = None) -> str:
    return broker.request(
        node_id=node_id,
        graph_id="g1",
        summary="请确认退款",
        approver="boss",
        timeout_seconds=600,
        card_template_id=card,
        card_context={"order": "12345"} if card else None,
        notify_recipients=["ops@example.com"],
    )


# --- InMemoryApprovalHistoryStore ------------------------------------------


def test_list_is_newest_first():
    store = InMemoryApprovalHistoryStore()
    store.record(_entry("t-1"))
    store.record(_entry("t-2"))
    assert [item["token"] for item in store.list()] == ["t-2", "t-1"]


def test_limit_clamped_like_broker():
    store = InMemoryApprovalHistoryStore()
    for index in range(3):
        store.record(_entry(f"t-{index}"))
    assert len(store.list(0)) == 1  # 下界夹到 1
    assert len(store.list(999)) == 3  # 上界 200，未超即全给


def test_ring_evicts_oldest_beyond_capacity():
    store = InMemoryApprovalHistoryStore()
    for index in range(HISTORY_RING_SIZE + 5):
        store.record(_entry(f"t-{index}"))
    items = store.list(HISTORY_RING_SIZE)
    assert len(items) == HISTORY_RING_SIZE
    assert items[-1]["token"] == "t-5"  # 最前 5 条已被挤出
    assert items[0]["token"] == f"t-{HISTORY_RING_SIZE + 4}"


def test_clear_empties_store():
    store = InMemoryApprovalHistoryStore()
    store.record(_entry("t-1"))
    store.clear()
    assert store.list() == []


def test_projection_shape_and_card_condition():
    plain = InMemoryApprovalHistoryStore()
    plain.record(_entry("t-1"))
    item = plain.list()[0]
    assert "cardTemplateId" not in item
    assert item["createdAt"] == epoch_to_iso(1_700_000_000.0)
    assert item["resolvedAt"] == epoch_to_iso(1_700_000_010.0)

    with_card = InMemoryApprovalHistoryStore()
    with_card.record(_entry("t-2", card_template_id="refund-approval"))
    assert with_card.list()[0]["cardTemplateId"] == "refund-approval"


# --- epoch_to_iso -----------------------------------------------------------


def test_epoch_to_iso_is_utc_aware():
    parsed = datetime.fromisoformat(epoch_to_iso(1_700_000_000.0))
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc) == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [0, -1, None, "nonsense", math.nan, math.inf])
def test_epoch_to_iso_falls_back_instead_of_raising(bad):
    assert epoch_to_iso(bad) == epoch_to_iso(0)


# --- broker 接线：四类决策来源都落一次历史 ------------------------------------


def test_human_resolve_records_history():
    broker = ApprovalBroker()
    token = _request(broker)
    assert broker.list_decided() == []  # 仍未决不进气历史
    broker.resolve(token, "approved", comment="ok", resolved_by="human")
    items = broker.list_decided()
    assert [(i["token"], i["decision"], i["resolvedBy"]) for i in items] == [
        (token, "approved", "human")
    ]


def test_email_link_and_preset_sources_both_record():
    broker = ApprovalBroker()
    email_token = _request(broker, node_id="n-email")
    preset_token = _request(broker, node_id="n-preset")
    broker.resolve(email_token, "rejected", resolved_by="email-link")
    broker.resolve(preset_token, "approved", resolved_by="input")
    by_token = {item["token"]: item for item in broker.list_decided()}
    assert by_token[email_token]["resolvedBy"] == "email-link"
    assert by_token[preset_token]["resolvedBy"] == "input"


def test_timeout_records_history_once():
    broker = ApprovalBroker()
    token = _request(broker)
    broker.complete_timeout(token, "rejected")
    items = broker.list_decided()
    assert len(items) == 1
    assert items[0]["decision"] == "rejected"
    assert items[0]["resolvedBy"] == "timeout"


def test_second_decision_does_not_append_history():
    broker = ApprovalBroker()
    token = _request(broker)
    assert broker.resolve(token, "approved") is True
    assert broker.resolve(token, "rejected") is False  # 首决生效
    assert len(broker.list_decided()) == 1
    assert broker.complete_timeout(token, "rejected") == ("approved", "human")
    assert len(broker.list_decided()) == 1


def test_history_entry_carries_no_private_context():
    broker = ApprovalBroker()
    token = _request(broker, card="refund-approval")
    broker.resolve(token, "approved")
    item = broker.list_decided()[0]
    assert item["cardTemplateId"] == "refund-approval"
    for leak in ("card_context", "cardContext", "notify_recipients", "action_id", "actionId"):
        assert leak not in item


def test_reset_clears_history():
    broker = ApprovalBroker()
    broker.resolve(_request(broker), "approved")
    assert broker.list_decided()
    broker.reset()
    assert broker.list_decided() == []


def test_history_store_failure_never_blocks_decision():
    class ExplodingStore:
        def __init__(self) -> None:
            self.attempts = 0

        def record(self, entry):
            self.attempts += 1
            raise RuntimeError("db down")

        def list(self, limit=50):
            raise RuntimeError("db down")

        def clear(self) -> None:
            raise RuntimeError("db down")

    store = ExplodingStore()
    broker = ApprovalBroker(history_store=store)
    token = _request(broker)
    # 写失败被吞：决策照常放行，且首决生效语义不受影响（不因异常重试而二次记账）。
    assert broker.resolve(token, "approved") is True
    assert broker.complete_timeout(token, "rejected") == ("approved", "human")
    assert store.attempts == 1
    # 读路径不吞异常——历史读不出要让调用方看见，而不是伪装成空列表。
    with pytest.raises(RuntimeError, match="db down"):
        broker.list_decided()


def test_reset_survives_history_store_failure():
    class ExplodingClear(InMemoryApprovalHistoryStore):
        def clear(self) -> None:
            raise RuntimeError("db down")

    broker = ApprovalBroker(history_store=ExplodingClear())
    with pytest.raises(RuntimeError, match="db down"):
        broker.reset()


def test_list_decided_delegates_limit_to_store():
    seen: list[int] = []

    class RecordingStore(InMemoryApprovalHistoryStore):
        def list(self, limit: int = 50):
            seen.append(limit)
            return []

    broker = ApprovalBroker(history_store=RecordingStore())
    broker.list_decided(77)
    assert seen == [77]


def test_broker_default_history_store_is_in_memory_per_instance():
    first, second = ApprovalBroker(), ApprovalBroker()
    first.resolve(_request(first), "approved")
    assert len(first.list_decided()) == 1
    assert second.list_decided() == []  # 不共享类级单例


def test_concurrent_resolves_record_each_once():
    broker = ApprovalBroker()
    tokens = [_request(broker, node_id=f"n-{i}") for i in range(20)]

    def decide(chunk):
        for token in chunk:
            broker.resolve(token, "approved", resolved_by="human")

    threads = [threading.Thread(target=decide, args=(tokens[i::4],)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    items = broker.list_decided(200)
    assert len(items) == 20
    assert len({item["token"] for item in items}) == 20

# -*- coding: utf-8 -*-
"""docs/60 §6 G5：投递日志存储抽象的进程内档单测（始终运行，无集成门槛）。

PG 档的跨实例/落库/清表见 test_message_deliveries_pg.py；这里覆盖
InMemoryDeliveryStore 的 ring、倒序、clamp、clear，以及 MessageService 注入自定义
DeliveryStore 的委托语义（旧 deque 行为不回归）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.message.deliveries import DELIVERY_RING_SIZE, InMemoryDeliveryStore
from atlas.message.service import DELIVERY_RING_SIZE as SERVICE_RING_SIZE, DeliveryRecord, MessageService


def _rec(message_id: str, *, status: str = "in_process", error_code: str | None = None) -> DeliveryRecord:
    return DeliveryRecord(
        id=message_id,
        channel="email",
        to=["a@example.com"],
        subject="s",
        sentAt=datetime.now(timezone.utc).isoformat(),
        status=status,
        attempts=1,
        elapsedMs=1,
        errorCode=error_code,
        errorMessage=None if error_code is None else "boom",
    )


def test_ring_size_constant_reexported_from_service():
    # 向后兼容：常量迁至存储层后 service 仍可 import 同值
    assert SERVICE_RING_SIZE == DELIVERY_RING_SIZE == 200


def test_inmemory_list_desc_and_clamp():
    store = InMemoryDeliveryStore()
    for index in range(5):
        store.record(_rec(f"m-{index}"))
    items = store.list(100)
    assert [item["id"] for item in items] == ["m-4", "m-3", "m-2", "m-1", "m-0"]
    assert len(store.list(2)) == 2
    assert len(store.list(0)) == 1  # clamp 到 1
    assert len(store.list(99999)) == 5  # clamp 到 ring 上限，不报错


def test_inmemory_ring_evicts_oldest():
    store = InMemoryDeliveryStore()
    for index in range(DELIVERY_RING_SIZE + 3):
        store.record(_rec(f"m-{index:04d}"))
    items = store.list(DELIVERY_RING_SIZE + 10)
    assert len(items) == DELIVERY_RING_SIZE
    ids = {item["id"] for item in items}
    assert "m-0000" not in ids and "m-0002" not in ids  # 最旧 3 条被淘汰
    assert items[0]["id"] == f"m-{DELIVERY_RING_SIZE + 2:04d}"


def test_inmemory_clear():
    store = InMemoryDeliveryStore()
    store.record(_rec("m-1"))
    store.clear()
    assert store.list(100) == []


def test_message_service_uses_injected_store_and_reset_clears():
    store = InMemoryDeliveryStore()
    service = MessageService(delivery_store=store)
    service.send("email", "a@example.com", "hi", "body")
    assert len(store.list(100)) == 1
    # 默认构造（不注入）仍得到进程内 ring，零回归
    assert isinstance(MessageService()._delivery_store, InMemoryDeliveryStore)
    service.reset()
    assert store.list(100) == []


class _BoomStore:
    """模拟 PG 不可用：record 抛错。"""

    def record(self, rec) -> None:
        raise RuntimeError("db down")

    def list(self, limit: int):
        return []

    def clear(self) -> None:
        pass


def test_send_succeeds_when_delivery_store_raises():
    # docs/60 §11：投递日志是旁路，存储失败不得阻断消息发送主链路
    service = MessageService(delivery_store=_BoomStore())
    record = service.send("email", "a@example.com", "hi", "body")
    assert record["delivered"] == "in_process"

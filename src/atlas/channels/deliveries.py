# -*- coding: utf-8 -*-
"""入站 webhook 投递记录：去重、死信、指标（docs/40 §1A/§1D）。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

REASON_CODES = frozenset(
    {
        "NO_PUBLISHED_VERSION",
        "RESOLVE_FAILED",
        "TRIGGER_FAILED",
        "MISSING_GRAPH_ID",
    }
)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeliveryRecord:
    tenant_id: str
    webhook_id: str
    binding_id: str
    topic: str
    shop: str
    status: str = "received"
    reasons: list[dict[str, str]] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    duplicates: int = 0
    created_at: str = field(default_factory=utc_now_text)
    updated_at: str = field(default_factory=utc_now_text)
    replayed_at: str | None = None

    def dead_view(self) -> dict[str, Any]:
        return {
            "webhookId": self.webhook_id,
            "bindingId": self.binding_id,
            "topic": self.topic,
            "shop": self.shop,
            "reasons": list(self.reasons),
            "createdAt": self.created_at,
            "replayedAt": self.replayed_at,
        }


class InMemoryDeliveryStore:
    """进程内投递存储（demo/测试档）；单锁、不设上限，reset 由租户注册表统一清。"""

    def __init__(self, clock: Any = utc_now_text) -> None:
        self._clock = clock
        self._rows: dict[str, DeliveryRecord] = {}
        self._lock = threading.Lock()

    def note_duplicate_if_seen(self, tenant_id: str, webhook_id: str) -> bool:
        with self._lock:
            row = self._rows.get(webhook_id)
            if row is None:
                return False
            row.duplicates += 1
            row.updated_at = self._clock()
            return True

    def record_received(
        self,
        tenant_id: str,
        *,
        webhook_id: str,
        binding_id: str,
        topic: str,
        shop: str,
    ) -> None:
        with self._lock:
            existing = self._rows.get(webhook_id)
            if existing is not None:
                existing.duplicates += 1
                existing.updated_at = self._clock()
                return
            now = self._clock()
            self._rows[webhook_id] = DeliveryRecord(
                tenant_id=tenant_id,
                webhook_id=webhook_id,
                binding_id=binding_id,
                topic=topic,
                shop=shop,
                created_at=now,
                updated_at=now,
            )

    def record_dead(
        self,
        tenant_id: str,
        *,
        webhook_id: str,
        binding_id: str,
        topic: str,
        shop: str,
        reasons: list[dict[str, str]],
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            now = self._clock()
            self._rows[webhook_id] = DeliveryRecord(
                tenant_id=tenant_id,
                webhook_id=webhook_id,
                binding_id=binding_id,
                topic=topic,
                shop=shop,
                status="dead",
                reasons=list(reasons),
                payload=dict(payload),
                created_at=now,
                updated_at=now,
            )

    def get_dead(self, tenant_id: str, webhook_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(webhook_id)
            if row is None or row.status != "dead":
                return None
            return {
                "webhookId": row.webhook_id,
                "bindingId": row.binding_id,
                "topic": row.topic,
                "shop": row.shop,
                "data": dict(row.payload or {}),
            }

    def list_dead(
        self,
        tenant_id: str,
        *,
        topic: str | None = None,
        binding_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(100, limit))
        with self._lock:
            rows = [
                row
                for row in self._rows.values()
                if row.status == "dead"
                and (topic is None or row.topic == topic)
                and (binding_id is None or row.binding_id == binding_id)
            ]
            rows.sort(key=lambda r: r.created_at, reverse=True)
            return [row.dead_view() for row in rows[:limit]]

    def resolve_replay(
        self,
        tenant_id: str,
        webhook_id: str,
        *,
        received: bool,
        reasons: list[dict[str, str]] | None = None,
    ) -> None:
        with self._lock:
            row = self._rows.get(webhook_id)
            if row is None or row.status != "dead":
                raise KeyError(webhook_id)
            row.replayed_at = self._clock()
            if received:
                row.status = "received"
                row.reasons = []
                row.payload = None
            elif reasons is not None:
                row.reasons = list(reasons)
            row.updated_at = self._clock()

    def delete(self, tenant_id: str, webhook_id: str) -> bool:
        with self._lock:
            return self._rows.pop(webhook_id, None) is not None

    def metrics(self, tenant_id: str) -> dict[str, Any]:
        by_topic: dict[str, dict[str, int]] = {}
        totals = {"received": 0, "dead": 0, "duplicates": 0}
        with self._lock:
            for row in self._rows.values():
                bucket = by_topic.setdefault(
                    row.topic, {"received": 0, "dead": 0, "duplicates": 0}
                )
                bucket[row.status] += 1
                bucket["duplicates"] += row.duplicates
                totals[row.status] += 1
                totals["duplicates"] += row.duplicates
        return {"byTopic": by_topic, "totals": totals}

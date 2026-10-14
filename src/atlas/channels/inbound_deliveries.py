# -*- coding: utf-8 -*-
"""入站消息投递记录：去重、死信、指标（打包 BO，docs/131）。

复用 docs/40 webhook_deliveries 的范式与 API 形状，维度从
(tenant, webhook_id, topic, shop) 换为通用 (tenant, channel, message_id,
conversation_id)——本批只用假供应商（docs/73 1.3 ⛔），channel 为平台无关维度。
幂等键 (tenant_id, channel, message_id) 做冲突计数；payload 仅死信行落库。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InboundRecord:
    tenant_id: str
    message_id: str
    binding_id: str
    channel: str
    conversation_id: str
    status: str = "received"
    reasons: list[dict[str, str]] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    duplicates: int = 0
    created_at: str = field(default_factory=utc_now_text)
    updated_at: str = field(default_factory=utc_now_text)
    replayed_at: str | None = None

    def dead_view(self) -> dict[str, Any]:
        return {
            "messageId": self.message_id,
            "bindingId": self.binding_id,
            "channel": self.channel,
            "conversationId": self.conversation_id,
            "reasons": list(self.reasons),
            "createdAt": self.created_at,
            "replayedAt": self.replayed_at,
        }


class InMemoryInboundStore:
    """进程内入站投递存储（demo/测试档）；单锁、不设上限，reset 由租户注册表统一清。"""

    def __init__(self, clock: Any = utc_now_text) -> None:
        self._clock = clock
        self._rows: dict[str, InboundRecord] = {}
        self._lock = threading.Lock()

    def note_duplicate_if_seen(self, tenant_id: str, message_id: str) -> bool:
        with self._lock:
            row = self._rows.get(message_id)
            if row is None:
                return False
            row.duplicates += 1
            row.updated_at = self._clock()
            return True

    def record_received(
        self,
        tenant_id: str,
        *,
        message_id: str,
        binding_id: str,
        channel: str,
        conversation_id: str,
    ) -> None:
        with self._lock:
            existing = self._rows.get(message_id)
            if existing is not None:
                existing.duplicates += 1
                existing.updated_at = self._clock()
                return
            now = self._clock()
            self._rows[message_id] = InboundRecord(
                tenant_id=tenant_id,
                message_id=message_id,
                binding_id=binding_id,
                channel=channel,
                conversation_id=conversation_id,
                created_at=now,
                updated_at=now,
            )

    def record_dead(
        self,
        tenant_id: str,
        *,
        message_id: str,
        binding_id: str,
        channel: str,
        conversation_id: str,
        reasons: list[dict[str, str]],
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            now = self._clock()
            self._rows[message_id] = InboundRecord(
                tenant_id=tenant_id,
                message_id=message_id,
                binding_id=binding_id,
                channel=channel,
                conversation_id=conversation_id,
                status="dead",
                reasons=list(reasons),
                payload=dict(payload),
                created_at=now,
                updated_at=now,
            )

    def get_dead(self, tenant_id: str, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(message_id)
            if row is None or row.status != "dead":
                return None
            return {
                "messageId": row.message_id,
                "bindingId": row.binding_id,
                "channel": row.channel,
                "conversationId": row.conversation_id,
                "data": dict(row.payload or {}),
            }

    def list_dead(
        self,
        tenant_id: str,
        *,
        channel: str | None = None,
        binding_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(100, limit))
        with self._lock:
            rows = [
                row
                for row in self._rows.values()
                if row.status == "dead"
                and (channel is None or row.channel == channel)
                and (binding_id is None or row.binding_id == binding_id)
            ]
            rows.sort(key=lambda r: r.created_at, reverse=True)
            return [row.dead_view() for row in rows[:limit]]

    def resolve_replay(
        self,
        tenant_id: str,
        message_id: str,
        *,
        received: bool,
        reasons: list[dict[str, str]] | None = None,
    ) -> None:
        with self._lock:
            row = self._rows.get(message_id)
            if row is None or row.status != "dead":
                raise KeyError(message_id)
            row.replayed_at = self._clock()
            if received:
                row.status = "received"
                row.reasons = []
                row.payload = None
            elif reasons is not None:
                row.reasons = list(reasons)
            row.updated_at = self._clock()

    def delete(self, tenant_id: str, message_id: str) -> bool:
        with self._lock:
            return self._rows.pop(message_id, None) is not None

    def metrics(self, tenant_id: str) -> dict[str, Any]:
        by_channel: dict[str, dict[str, int]] = {}
        totals = {"received": 0, "dead": 0, "duplicates": 0}
        with self._lock:
            for row in self._rows.values():
                bucket = by_channel.setdefault(
                    row.channel, {"received": 0, "dead": 0, "duplicates": 0}
                )
                bucket[row.status] += 1
                bucket["duplicates"] += row.duplicates
                totals[row.status] += 1
                totals["duplicates"] += row.duplicates
        return {"byChannel": by_channel, "totals": totals}


class PgInboundStore:
    _DEAD_COLS = (
        "message_id, binding_id, channel, conversation_id, reasons, payload, "
        "created_at, replayed_at"
    )

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def note_duplicate_if_seen(self, tenant_id: str, message_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE inbound_deliveries SET duplicates = duplicates + 1, "
                    "updated_at = :now WHERE tenant_id = :t AND message_id = :id"
                ),
                {"now": utc_now_text(), "t": tenant_id, "id": message_id},
            )
        return result.rowcount > 0

    def record_received(
        self,
        tenant_id: str,
        *,
        message_id: str,
        binding_id: str,
        channel: str,
        conversation_id: str,
    ) -> None:
        now = utc_now_text()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO inbound_deliveries (tenant_id, message_id, binding_id, "
                    "channel, conversation_id, status, reasons, payload, duplicates, "
                    "created_at, updated_at) VALUES (:t, :id, :binding, :channel, "
                    ":conv, 'received', '[]'::jsonb, NULL, 0, :now, :now) "
                    "ON CONFLICT (tenant_id, message_id) "
                    "DO UPDATE SET duplicates = inbound_deliveries.duplicates + 1, "
                    "updated_at = :now"
                ),
                {
                    "t": tenant_id, "id": message_id, "binding": binding_id,
                    "channel": channel, "conv": conversation_id, "now": now,
                },
            )

    def record_dead(
        self,
        tenant_id: str,
        *,
        message_id: str,
        binding_id: str,
        channel: str,
        conversation_id: str,
        reasons: list[dict[str, str]],
        payload: dict[str, Any],
    ) -> None:
        now = utc_now_text()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO inbound_deliveries (tenant_id, message_id, binding_id, "
                    "channel, conversation_id, status, reasons, payload, duplicates, "
                    "created_at, updated_at) VALUES (:t, :id, :binding, :channel, :conv, "
                    "'dead', CAST(:reasons AS jsonb), CAST(:payload AS jsonb), 0, :now, :now)"
                ),
                {
                    "t": tenant_id, "id": message_id, "binding": binding_id,
                    "channel": channel, "conv": conversation_id,
                    "reasons": json.dumps(reasons, ensure_ascii=False),
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "now": now,
                },
            )

    def get_dead(self, tenant_id: str, message_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT message_id, binding_id, channel, conversation_id, payload "
            "FROM inbound_deliveries WHERE tenant_id = :t AND message_id = :id "
            "AND status = 'dead'"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": tenant_id, "id": message_id}).first()
        if row is None:
            return None
        return {
            "messageId": row[0], "bindingId": row[1], "channel": row[2],
            "conversationId": row[3], "data": row[4] or {},
        }

    def list_dead(
        self,
        tenant_id: str,
        *,
        channel: str | None = None,
        binding_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(100, limit))
        sql = (
            f"SELECT {self._DEAD_COLS} FROM inbound_deliveries "
            "WHERE tenant_id = :t AND status = 'dead'"
        )
        params: dict[str, Any] = {"t": tenant_id, "limit": limit}
        if channel is not None:
            sql += " AND channel = :channel"
            params["channel"] = channel
        if binding_id is not None:
            sql += " AND binding_id = :binding"
            params["binding"] = binding_id
        sql += " ORDER BY created_at DESC LIMIT :limit"
        with self._engine.connect() as db:
            rows = db.execute(text(sql), params).all()
        return [
            {
                "messageId": r[0], "bindingId": r[1], "channel": r[2],
                "conversationId": r[3], "reasons": r[4] or [],
                "createdAt": r[6], "replayedAt": r[7],
            }
            for r in rows
        ]

    def resolve_replay(
        self,
        tenant_id: str,
        message_id: str,
        *,
        received: bool,
        reasons: list[dict[str, str]] | None = None,
    ) -> None:
        now = utc_now_text()
        if received:
            sql = (
                "UPDATE inbound_deliveries SET status = 'received', reasons = '[]'::jsonb, "
                "payload = NULL, replayed_at = :now, updated_at = :now "
                "WHERE tenant_id = :t AND message_id = :id AND status = 'dead'"
            )
            params: dict[str, Any] = {"now": now, "t": tenant_id, "id": message_id}
        else:
            sql = (
                "UPDATE inbound_deliveries SET reasons = CAST(:reasons AS jsonb), "
                "replayed_at = :now, updated_at = :now "
                "WHERE tenant_id = :t AND message_id = :id AND status = 'dead'"
            )
            params = {
                "now": now, "t": tenant_id, "id": message_id,
                "reasons": json.dumps(reasons or [], ensure_ascii=False),
            }
        with self._engine.begin() as db:
            result = db.execute(text(sql), params)
        if result.rowcount == 0:
            raise KeyError(message_id)

    def delete(self, tenant_id: str, message_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "DELETE FROM inbound_deliveries WHERE tenant_id = :t "
                    "AND message_id = :id"
                ),
                {"t": tenant_id, "id": message_id},
            )
        return result.rowcount > 0

    def metrics(self, tenant_id: str) -> dict[str, Any]:
        sql = (
            "SELECT channel, status, COUNT(*) AS n, SUM(duplicates) AS dups "
            "FROM inbound_deliveries WHERE tenant_id = :t GROUP BY channel, status"
        )
        by_channel: dict[str, dict[str, int]] = {}
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": tenant_id}).all()
        for channel, status, count, dups in rows:
            bucket = by_channel.setdefault(
                channel, {"received": 0, "dead": 0, "duplicates": 0}
            )
            bucket[status] = int(count)
            bucket["duplicates"] = int(dups or 0)
        totals = {"received": 0, "dead": 0, "duplicates": 0}
        for bucket in by_channel.values():
            for key in totals:
                totals[key] += bucket[key]
        return {"byChannel": by_channel, "totals": totals}

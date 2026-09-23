# -*- coding: utf-8 -*-
"""Webhook 投递记录 PG 存储（docs/40 §1D）。

裸 SQL 对齐 channels/pg.py；reasons/payload JSONB（CAST 绑定参数，:p::type
与参数冲突）；PK(tenant_id, webhook_id) 做冲突计数；reset 不清。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from atlas.storage.pg import _now_iso


class PgDeliveryStore:
    _DEAD_COLS = (
        "webhook_id, binding_id, topic, shop, reasons, payload, "
        "created_at, replayed_at"
    )

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    def note_duplicate_if_seen(self, tenant_id: str, webhook_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE webhook_deliveries SET duplicates = duplicates + 1, "
                    "updated_at = :now WHERE tenant_id = :t AND webhook_id = :id"
                ),
                {"now": _now_iso(), "t": tenant_id, "id": webhook_id},
            )
        return result.rowcount > 0

    def record_received(
        self,
        tenant_id: str,
        *,
        webhook_id: str,
        binding_id: str,
        topic: str,
        shop: str,
    ) -> None:
        now = _now_iso()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO webhook_deliveries (tenant_id, webhook_id, binding_id, "
                    "topic, shop, status, reasons, payload, duplicates, created_at, "
                    "updated_at) VALUES (:t, :id, :binding, :topic, :shop, 'received', "
                    "'[]'::jsonb, NULL, 0, :now, :now) ON CONFLICT (tenant_id, webhook_id) "
                    "DO UPDATE SET duplicates = webhook_deliveries.duplicates + 1, "
                    "updated_at = :now"
                ),
                {
                    "t": tenant_id, "id": webhook_id, "binding": binding_id,
                    "topic": topic, "shop": shop, "now": now,
                },
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
        now = _now_iso()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO webhook_deliveries (tenant_id, webhook_id, binding_id, "
                    "topic, shop, status, reasons, payload, duplicates, created_at, "
                    "updated_at) VALUES (:t, :id, :binding, :topic, :shop, 'dead', "
                    "CAST(:reasons AS jsonb), CAST(:payload AS jsonb), 0, :now, :now)"
                ),
                {
                    "t": tenant_id, "id": webhook_id, "binding": binding_id,
                    "topic": topic, "shop": shop,
                    "reasons": json.dumps(reasons, ensure_ascii=False),
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "now": now,
                },
            )

    def get_dead(self, tenant_id: str, webhook_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT webhook_id, binding_id, topic, shop, payload "
            "FROM webhook_deliveries WHERE tenant_id = :t AND webhook_id = :id "
            "AND status = 'dead'"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": tenant_id, "id": webhook_id}).first()
        if row is None:
            return None
        return {
            "webhookId": row[0], "bindingId": row[1], "topic": row[2],
            "shop": row[3], "data": row[4] or {},
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
        sql = (
            f"SELECT {self._DEAD_COLS} FROM webhook_deliveries "
            "WHERE tenant_id = :t AND status = 'dead'"
        )
        params: dict[str, Any] = {"t": tenant_id, "limit": limit}
        if topic is not None:
            sql += " AND topic = :topic"
            params["topic"] = topic
        if binding_id is not None:
            sql += " AND binding_id = :binding"
            params["binding"] = binding_id
        sql += " ORDER BY created_at DESC LIMIT :limit"
        with self._engine.connect() as db:
            rows = db.execute(text(sql), params).all()
        return [
            {
                "webhookId": r[0], "bindingId": r[1], "topic": r[2], "shop": r[3],
                "reasons": r[4] or [], "createdAt": r[6], "replayedAt": r[7],
            }
            for r in rows
        ]

    def resolve_replay(
        self,
        tenant_id: str,
        webhook_id: str,
        *,
        received: bool,
        reasons: list[dict[str, str]] | None = None,
    ) -> None:
        now = _now_iso()
        if received:
            sql = (
                "UPDATE webhook_deliveries SET status = 'received', reasons = '[]'::jsonb, "
                "payload = NULL, replayed_at = :now, updated_at = :now "
                "WHERE tenant_id = :t AND webhook_id = :id AND status = 'dead'"
            )
            params: dict[str, Any] = {"now": now, "t": tenant_id, "id": webhook_id}
        else:
            sql = (
                "UPDATE webhook_deliveries SET reasons = CAST(:reasons AS jsonb), "
                "replayed_at = :now, updated_at = :now "
                "WHERE tenant_id = :t AND webhook_id = :id AND status = 'dead'"
            )
            params = {
                "now": now, "t": tenant_id, "id": webhook_id,
                "reasons": json.dumps(reasons or [], ensure_ascii=False),
            }
        with self._engine.begin() as db:
            result = db.execute(text(sql), params)
        if result.rowcount == 0:
            raise KeyError(webhook_id)

    def delete(self, tenant_id: str, webhook_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "DELETE FROM webhook_deliveries WHERE tenant_id = :t "
                    "AND webhook_id = :id"
                ),
                {"t": tenant_id, "id": webhook_id},
            )
        return result.rowcount > 0

    def metrics(self, tenant_id: str) -> dict[str, Any]:
        sql = (
            "SELECT topic, status, COUNT(*) AS n, SUM(duplicates) AS dups "
            "FROM webhook_deliveries WHERE tenant_id = :t GROUP BY topic, status"
        )
        by_topic: dict[str, dict[str, int]] = {}
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": tenant_id}).all()
        for topic, status, count, dups in rows:
            bucket = by_topic.setdefault(
                topic, {"received": 0, "dead": 0, "duplicates": 0}
            )
            bucket[status] = int(count)
            bucket["duplicates"] = int(dups or 0)
        totals = {"received": 0, "dead": 0, "duplicates": 0}
        for bucket in by_topic.values():
            for key in totals:
                totals[key] += bucket[key]
        return {"byTopic": by_topic, "totals": totals}

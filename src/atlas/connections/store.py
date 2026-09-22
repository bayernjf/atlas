# -*- coding: utf-8 -*-
"""OAuth2 连接内存存储（docs/35 §4.4，T4）；per-tenant 实例，conn-N 顺序 id。"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from atlas.connections.models import Connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectionStore:
    def __init__(self) -> None:
        self._items: dict[str, Connection] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def create(self, conn: Connection) -> Connection:
        with self._lock:
            self._seq += 1
            conn.id = f"conn-{self._seq}"
            now = _now_iso()
            conn.created_at = now
            conn.updated_at = now
            self._items[conn.id] = conn
            return conn

    def get(self, conn_id: str) -> Connection | None:
        return self._items.get(conn_id)

    def list(self) -> list[Connection]:
        return [self._items[key] for key in sorted(self._items, key=lambda k: int(k.split("-", 1)[1]))]

    def save(self, conn: Connection) -> Connection:
        with self._lock:
            conn.updated_at = _now_iso()
            self._items[conn.id] = conn
            return conn

    def delete(self, conn_id: str) -> bool:
        with self._lock:
            return self._items.pop(conn_id, None) is not None

    def reset(self) -> None:
        with self._lock:
            self._items.clear()
            self._seq = 0

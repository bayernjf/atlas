# -*- coding: utf-8 -*-
"""网页嵌入配置的 PG 持久化（docs/133 §2.1，迁移 054；与进程内 `EmbedStore` 签名一比一）。

行内 tenant_id 过滤（非 RLS）；upsert 用 ON CONFLICT（PK tenant_id+graph_id）；
reset 删本租户。JSONB 列经 psycopg 返回已是 dict（_loads 兜底字符串）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .store import EmbedConfig, EmbedStore  # noqa: F401  # re-export 联合导入对齐 pg_shadow 先例


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PgEmbedStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def get(self, graph_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT graph_id, enabled, token_ttl_seconds, created_at, updated_at "
            "FROM embed_configs WHERE tenant_id = :t AND graph_id = :g"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "g": graph_id}).first()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(self) -> list[dict[str, Any]]:
        sql = (
            "SELECT graph_id, enabled, token_ttl_seconds, created_at, updated_at "
            "FROM embed_configs WHERE tenant_id = :t ORDER BY graph_id"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id}).all()
        return [self._row_to_dict(row) for row in rows]

    def upsert(
        self,
        graph_id: str,
        *,
        enabled: bool,
        token_ttl_seconds: int,
        now: str | None = None,
    ) -> dict[str, Any]:
        """ON CONFLICT upsert：缺省现在时间由 SQL 端 created_at 保留（照 memory 先例不引时钟）。"""
        if now is None:
            # 时间戳由 Python 生成，避免 SQL 端两处时钟分叉（create/update 同一值）。
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO embed_configs (tenant_id, graph_id, enabled, "
                    "token_ttl_seconds, created_at, updated_at) "
                    "VALUES (:t, :g, :enabled, :ttl, :now, :now) "
                    "ON CONFLICT (tenant_id, graph_id) DO UPDATE SET "
                    "enabled = EXCLUDED.enabled, token_ttl_seconds = EXCLUDED.token_ttl_seconds, "
                    "updated_at = EXCLUDED.updated_at"
                ),
                {
                    "t": self._tenant_id,
                    "g": graph_id,
                    "enabled": bool(enabled),
                    "ttl": token_ttl_seconds,
                    "now": now,
                },
            )
        return self.get(graph_id) or {}

    def reset(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM embed_configs WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "graph_id": row[0],
            "enabled": bool(row[1]),
            "token_ttl_seconds": int(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }

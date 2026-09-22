# -*- coding: utf-8 -*-
"""渠道绑定 PG 存储（docs/38 §1D，ADR T28）。

裸 SQL 风格对齐 storage/pg.py PgConnectionStore；config 以 JSONB 存储；
id 用全局 storage_id_seq（ch-N）；行内 tenant_id 过滤；demo reset 不清。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from atlas.channels.base import ChannelBinding


def _now_iso() -> str:
    from atlas.storage.pg import _now_iso as _f

    return _f()


class PgChannelStore:
    _COLS = (
        "id, tenant_id, provider, connection_id, config, status, "
        "last_error, created_by, created_at, updated_at"
    )

    def __init__(self, engine: Engine, tenant_id: str):
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_binding(row: Any) -> ChannelBinding:
        # 列序：0 id,1 tenant_id,2 provider,3 connection_id,4 config,5 status,
        # 6 last_error,7 created_by,8 created_at,9 updated_at
        config = row[4]
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (TypeError, ValueError):
                config = {}
        if not isinstance(config, dict):
            config = {}
        return ChannelBinding(
            id=row[0],
            tenant_id=row[1],
            provider=row[2],
            connection_id=row[3],
            config=config,
            status=row[5],
            last_error=row[6],
            created_by=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    def create(self, binding: ChannelBinding) -> ChannelBinding:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            binding.id = f"ch-{seq}"
            now = _now_iso()
            binding.created_at = now
            binding.updated_at = now
            db.execute(
                text(
                    "INSERT INTO channel_bindings (id, tenant_id, provider, connection_id, "
                    "config, status, last_error, created_by, created_at, updated_at) VALUES "
                    "(:id, :tenant_id, :provider, :connection_id, CAST(:config AS jsonb), "
                    ":status, :last_error, :created_by, :created_at, :updated_at)"
                ),
                {
                    "id": binding.id,
                    "tenant_id": self._tenant_id,
                    "provider": binding.provider,
                    "connection_id": binding.connection_id,
                    "config": json.dumps(binding.config, ensure_ascii=False),
                    "status": binding.status,
                    "last_error": binding.last_error,
                    "created_by": binding.created_by,
                    "created_at": binding.created_at,
                    "updated_at": binding.updated_at,
                },
            )
        return binding

    def get(self, binding_id: str) -> ChannelBinding | None:
        sql = f"SELECT {self._COLS} FROM channel_bindings WHERE tenant_id = :t AND id = :id"
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": binding_id}).first()
        return self._row_to_binding(row) if row else None

    def list(self) -> list[ChannelBinding]:
        sql = (
            f"SELECT {self._COLS} FROM channel_bindings "
            "WHERE tenant_id = :t ORDER BY created_at"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id}).all()
        return [self._row_to_binding(row) for row in rows]

    def save(self, binding: ChannelBinding) -> ChannelBinding:
        binding.updated_at = _now_iso()
        with self._engine.begin() as db:
            db.execute(
                text(
                    "UPDATE channel_bindings SET provider = :provider, "
                    "connection_id = :connection_id, config = CAST(:config AS jsonb), "
                    "status = :status, last_error = :last_error, updated_at = :updated_at "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "provider": binding.provider,
                    "connection_id": binding.connection_id,
                    "config": json.dumps(binding.config, ensure_ascii=False),
                    "status": binding.status,
                    "last_error": binding.last_error,
                    "updated_at": binding.updated_at,
                    "t": self._tenant_id,
                    "id": binding.id,
                },
            )
        return binding

    def delete(self, binding_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "DELETE FROM channel_bindings WHERE tenant_id = :t AND id = :id"
                ),
                {"t": self._tenant_id, "id": binding_id},
            )
        return result.rowcount > 0

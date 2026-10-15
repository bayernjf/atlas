# -*- coding: utf-8 -*-
"""适配器草稿的 PG 持久化（docs/133 §2.2，迁移 055；与进程内 `AdapterDraftStore` 签名一比一）。

draft_id 数字部分取全局 `storage_id_seq`（与 autoapply_runs 同款）；行内 tenant_id 过滤；
definition/reachability JSONB；set_decision/set_reachability 就地 UPDATE；reset 删本租户。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .drafts import AdapterDraft, AdapterDraftStore  # noqa: F401  # re-export 对齐 pg_shadow 先例


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PgAdapterDraftStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        self._pending_seq: dict[str, int] = {}

    def next_draft_id(self) -> str:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
        draft_id = f"ad-{seq}"
        self._pending_seq[draft_id] = seq
        return draft_id

    def add(self, definition: dict[str, Any]) -> dict[str, Any]:
        """单步取号＋落库（草稿创建是一个动作，无需 next_draft_id/add 分离编排）。"""
        from .drafts import _now_iso

        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            draft_id = f"ad-{seq}"
            now = _now_iso()
            db.execute(
                text(
                    "INSERT INTO adapter_drafts (tenant_id, draft_id, name, definition, "
                    "decision_status, reachability, created_at, decided_at, updated_at) "
                    "VALUES (:t, :id, :name, CAST(:defn AS JSONB), NULL, NULL, :now, NULL, :now)"
                ),
                {
                    "t": self._tenant_id,
                    "id": draft_id,
                    "name": definition.get("name", ""),
                    "defn": json.dumps(definition, ensure_ascii=False),
                    "now": now,
                },
            )
        return self.get(draft_id) or {}

    def get(self, draft_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT draft_id, definition, decision_status, reachability, created_at, "
            "decided_at, updated_at FROM adapter_drafts "
            "WHERE tenant_id = :t AND draft_id = :id"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": draft_id}).first()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(self, *, adopted: bool | None = None) -> list[dict[str, Any]]:
        clauses = ["tenant_id = :t"]
        args: dict[str, Any] = {"t": self._tenant_id}
        if adopted is True:
            clauses.append("decision_status = 'adopted'")
        sql = (
            "SELECT draft_id, definition, decision_status, reachability, created_at, "
            "decided_at, updated_at FROM adapter_drafts "
            f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [self._row_to_dict(row) for row in rows]

    def set_decision(self, draft_id: str, status: str) -> dict[str, Any] | None:
        from .drafts import _now_iso

        now = _now_iso()
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE adapter_drafts SET decision_status = :status, "
                    "decided_at = :now, updated_at = :now "
                    "WHERE tenant_id = :t AND draft_id = :id"
                ),
                {"t": self._tenant_id, "id": draft_id, "status": status, "now": now},
            )
        if result.rowcount == 0:
            return None
        return self.get(draft_id)

    def set_reachability(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        from .drafts import _now_iso

        now = _now_iso()
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE adapter_drafts SET reachability = CAST(:payload AS JSONB), "
                    "updated_at = :now WHERE tenant_id = :t AND draft_id = :id"
                ),
                {
                    "t": self._tenant_id,
                    "id": draft_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "now": now,
                },
            )
        if result.rowcount == 0:
            return None
        return self.get(draft_id)

    def reset(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM adapter_drafts WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
        self._pending_seq.clear()

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "draft_id": row[0],
            "definition": _loads(row[1], {}),
            "decision_status": row[2],
            "reachability": _loads(row[3]),
            "created_at": row[4],
            "decided_at": row[5],
            "updated_at": row[6],
        }

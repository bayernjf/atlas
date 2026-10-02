"""PgUserTemplateStore：用户模板 PG 落库（打包 X，契约 docs/85）。

方法面与 `template/user_store.py UserTemplateStore` 逐字一致。
seq 在同租户行锁内取 COALESCE(MAX(seq),0)+1；tags/graph 走 JSONB。
"""

from __future__ import annotations

from typing import Any

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .user_store import UserTemplate


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class PgUserTemplateStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def add(
        self, *, name: str, description: str, tags: list[str], category: str = "", graph: dict[str, Any]
    ) -> UserTemplate:
        created_at = _now_iso()
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT COALESCE(MAX(seq), 0) AS next_seq "
                    "FROM user_templates WHERE tenant_id = :tenant_id FOR UPDATE"
                ),
                {"tenant_id": self._tenant_id},
            ).one()
            seq = int(row.next_seq) + 1
            template_id = f"utpl-{seq}"
            conn.execute(
                text(
                    "INSERT INTO user_templates "
                    "(tenant_id, id, seq, name, description, tags, category, graph, created_at) "
                    "VALUES (:tenant_id, :id, :seq, :name, :description, "
                    "CAST(:tags AS JSONB), :category, CAST(:graph AS JSONB), :created_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": template_id,
                    "seq": seq,
                    "name": name,
                    "description": description,
                    "tags": json.dumps(list(tags), ensure_ascii=False),
                    "category": category,
                    "graph": json.dumps(graph, ensure_ascii=False),
                    "created_at": created_at,
                },
            )
        return UserTemplate(
            id=template_id,
            name=name,
            description=description,
            tags=list(tags),
            category=category,
            graph=graph,
            created_at=created_at,
        )

    def get(self, template_id: str) -> UserTemplate | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, description, tags, category, graph, created_at "
                    "FROM user_templates WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": template_id},
            ).first()
        return self._to_model(row) if row is not None else None

    def update(
        self,
        template_id: str,
        *,
        name: str,
        description: str,
        tags: list[str],
        category: str = "",
        graph: dict[str, Any],
    ) -> UserTemplate | None:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE user_templates SET name = :name, description = :description, "
                    "tags = CAST(:tags AS JSONB), category = :category, graph = CAST(:graph AS JSONB) "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {
                    "name": name,
                    "description": description,
                    "tags": json.dumps(list(tags), ensure_ascii=False),
                    "category": category,
                    "graph": json.dumps(graph, ensure_ascii=False),
                    "tenant_id": self._tenant_id,
                    "id": template_id,
                },
            )
            if result.rowcount == 0:
                return None
        return self.get(template_id)

    def list(self) -> list[UserTemplate]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, name, description, tags, category, graph, created_at "
                    "FROM user_templates WHERE tenant_id = :tenant_id ORDER BY seq DESC"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [self._to_model(row) for row in rows]

    def delete(self, template_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM user_templates "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": template_id},
            )
            return result.rowcount > 0

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM user_templates WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )

    @staticmethod
    def _to_model(row: Any) -> UserTemplate:
        tags = row[3]
        if isinstance(tags, str):
            tags = json.loads(tags)
        category = row[4] if row[4] is not None else ""
        graph = row[5]
        if isinstance(graph, str):
            graph = json.loads(graph)
        return UserTemplate(
            id=row[0],
            name=row[1],
            description=row[2],
            tags=list(tags or []),
            category=str(category),
            graph=dict(graph or {}),
            created_at=row[6],
        )

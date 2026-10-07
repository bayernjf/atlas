"""PgUserTemplateStore：用户模板 PG 落库（打包 X，契约 docs/85；A1 增量 docs/97）。

方法面与 `template/user_store.py UserTemplateStore` 逐字一致。
seq 在同租户行锁内取 COALESCE(MAX(seq),0)+1；tags/graph/params 走 JSONB。
A1：version/updated_at/usage_count/params 列（迁移 043）；CAS 由 UPDATE 条件承担（rowcount=0 时同事务只读折 404/409）。
"""

from __future__ import annotations

from typing import Any

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .user_store import TemplateVersionConflict, UserTemplate


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_SELECT_COLS = (
    "id, name, description, tags, category, graph, created_at, "
    "version, updated_at, usage_count, params"
)


class PgUserTemplateStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def add(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        category: str = "",
        graph: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> UserTemplate:
        created_at = _now_iso()
        params = dict(params) if params else {}
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
                    "(tenant_id, id, seq, name, description, tags, category, graph, created_at, "
                    "version, updated_at, usage_count, params) "
                    "VALUES (:tenant_id, :id, :seq, :name, :description, "
                    "CAST(:tags AS JSONB), :category, CAST(:graph AS JSONB), :created_at, "
                    "1, :updated_at, 0, CAST(:params AS JSONB))"
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
                    "updated_at": created_at,
                    "params": json.dumps(params, ensure_ascii=False),
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
            version=1,
            updated_at=created_at,
            usage_count=0,
            params=params,
        )

    def get(self, template_id: str) -> UserTemplate | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT {_SELECT_COLS} "
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
        params: dict[str, Any] | None = None,
        if_match_version: int | None = None,
    ) -> UserTemplate | None:
        updated_at = _now_iso()
        params_sql = "params = CAST(:params AS JSONB)"
        params_val = params
        if params is None:
            # 缺省＝保留旧 params（照 tags 先例 docs/86 D-5），不写该列。
            params_sql = "params = params"
            params_val = {}
        with self._engine.begin() as conn:
            if if_match_version is not None:
                result = conn.execute(
                    text(
                        "UPDATE user_templates SET name = :name, description = :description, "
                        "tags = CAST(:tags AS JSONB), category = :category, graph = CAST(:graph AS JSONB), "
                        "version = version + 1, updated_at = :updated_at, "
                        f"{params_sql} "
                        "WHERE tenant_id = :tenant_id AND id = :id AND version = :if_match_version"
                    ),
                    {
                        "name": name,
                        "description": description,
                        "tags": json.dumps(list(tags), ensure_ascii=False),
                        "category": category,
                        "graph": json.dumps(graph, ensure_ascii=False),
                        "updated_at": updated_at,
                        "params": json.dumps(params_val, ensure_ascii=False),
                        "tenant_id": self._tenant_id,
                        "id": template_id,
                        "if_match_version": if_match_version,
                    },
                )
            else:
                result = conn.execute(
                    text(
                        "UPDATE user_templates SET name = :name, description = :description, "
                        "tags = CAST(:tags AS JSONB), category = :category, graph = CAST(:graph AS JSONB), "
                        "version = version + 1, updated_at = :updated_at, "
                        f"{params_sql} "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {
                        "name": name,
                        "description": description,
                        "tags": json.dumps(list(tags), ensure_ascii=False),
                        "category": category,
                        "graph": json.dumps(graph, ensure_ascii=False),
                        "updated_at": updated_at,
                        "params": json.dumps(params_val, ensure_ascii=False),
                        "tenant_id": self._tenant_id,
                        "id": template_id,
                    },
                )
            if result.rowcount == 0:
                # rowcount=0 无法区分"不存在"与"CAS 冲突"：同事务只读折 404/409，不承担互斥。
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM user_templates WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": self._tenant_id, "id": template_id},
                ).first()
                if exists is not None:
                    raise TemplateVersionConflict()
                return None
        return self.get(template_id)

    def list(self) -> list[UserTemplate]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {_SELECT_COLS} "
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

    def touch(self, template_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE user_templates SET usage_count = usage_count + 1 "
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
        params = row[10]
        if isinstance(params, str):
            params = json.loads(params)
        return UserTemplate(
            id=row[0],
            name=row[1],
            description=row[2],
            tags=list(tags or []),
            category=str(category),
            graph=dict(graph or {}),
            created_at=row[6],
            version=int(row[7]),
            updated_at=row[8] if row[8] is not None else "",
            usage_count=int(row[9]),
            params=dict(params or {}),
        )

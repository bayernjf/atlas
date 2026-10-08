"""PgUserRuleTemplateStore：用户告警规则模板 PG 落库（打包 ZS，契约 docs/102）。

方法面与 `monitoring/rule_user_store.py UserRuleTemplateStore` 逐字一致。
seq 在同租户行锁内取 COALESCE(MAX(seq),0)+1；tags/config 走 JSONB。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .rule_user_store import RuleTemplateNameConflict, UserRuleTemplate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SELECT_COLS = "id, name, description, tags, config, created_at"


class PgUserRuleTemplateStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def add(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        config: dict[str, Any],
    ) -> UserRuleTemplate:
        created_at = _now_iso()
        with self._engine.begin() as conn:
            if conn.execute(
                text(
                    "SELECT 1 FROM user_rule_templates WHERE tenant_id = :tenant_id AND name = :name"
                ),
                {"tenant_id": self._tenant_id, "name": name},
            ).first() is not None:
                raise RuleTemplateNameConflict(name)
            row = conn.execute(
                text(
                    "SELECT seq FROM user_rule_templates WHERE tenant_id = :tenant_id "
                    "ORDER BY seq DESC LIMIT 1 FOR UPDATE"
                ),
                {"tenant_id": self._tenant_id},
            ).first()
            seq = (int(row.seq) if row is not None else 0) + 1
            template_id = f"urt-{seq}"
            conn.execute(
                text(
                    "INSERT INTO user_rule_templates "
                    "(tenant_id, id, seq, name, description, tags, config, created_at) "
                    "VALUES (:tenant_id, :id, :seq, :name, :description, "
                    "CAST(:tags AS JSONB), CAST(:config AS JSONB), :created_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": template_id,
                    "seq": seq,
                    "name": name,
                    "description": description,
                    "tags": json.dumps(list(tags), ensure_ascii=False),
                    "config": json.dumps(config, ensure_ascii=False),
                    "created_at": created_at,
                },
            )
        return UserRuleTemplate(
            id=template_id,
            name=name,
            description=description,
            tags=list(tags),
            config=dict(config),
            created_at=created_at,
        )

    def get(self, template_id: str) -> UserRuleTemplate | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT {_SELECT_COLS} "
                    "FROM user_rule_templates WHERE tenant_id = :tenant_id AND id = :id"
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
        config: dict[str, Any],
    ) -> UserRuleTemplate | None:
        with self._engine.begin() as conn:
            if conn.execute(
                text(
                    "SELECT 1 FROM user_rule_templates WHERE tenant_id = :tenant_id "
                    "AND name = :name AND id <> :id"
                ),
                {"tenant_id": self._tenant_id, "name": name, "id": template_id},
            ).first() is not None:
                raise RuleTemplateNameConflict(name)
            result = conn.execute(
                text(
                    "UPDATE user_rule_templates SET name = :name, description = :description, "
                    "tags = CAST(:tags AS JSONB), config = CAST(:config AS JSONB) "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {
                    "name": name,
                    "description": description,
                    "tags": json.dumps(list(tags), ensure_ascii=False),
                    "config": json.dumps(config, ensure_ascii=False),
                    "tenant_id": self._tenant_id,
                    "id": template_id,
                },
            )
            if result.rowcount == 0:
                return None
        return self.get(template_id)

    def list(self) -> list[UserRuleTemplate]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {_SELECT_COLS} "
                    "FROM user_rule_templates WHERE tenant_id = :tenant_id ORDER BY seq DESC"
                ),
                {"tenant_id": self._tenant_id},
            ).all()
        return [self._to_model(row) for row in rows]

    def delete(self, template_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM user_rule_templates "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": template_id},
            )
            return result.rowcount > 0

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM user_rule_templates WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )

    @staticmethod
    def _to_model(row: Any) -> UserRuleTemplate:
        # _SELECT_COLS 顺序：id(0), name(1), description(2), tags(3), config(4), created_at(5)
        tags = row[3]
        if isinstance(tags, str):
            tags = json.loads(tags)
        config = row[4]
        if isinstance(config, str):
            config = json.loads(config)
        return UserRuleTemplate(
            id=row[0],
            name=row[1],
            description=row[2] if row[2] is not None else "",
            tags=list(tags or []),
            config=dict(config or {}),
            created_at=row[5],
        )

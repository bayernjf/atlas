"""PgMessageTemplateStore：消息模板 PG 落库（打包 A2，契约 docs/98）。

方法面与 `message/template_store.py MessageTemplateStore` 逐字一致。
seq 在同租户行锁内取（SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE，照 A1 修复先例）；
variables 走 JSONB；name 唯一由 `ux_message_templates_tenant_name`（LOWER）承担，
IntegrityError 转 `MessageTemplateNameConflict`（409）。
"""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .template_store import MessageTemplate, MessageTemplateNameConflict


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_SELECT_COLS = "id, name, kind, subject, body, variables, created_at, updated_at"


class PgMessageTemplateStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _to_model(row) -> MessageTemplate:
        variables = row.variables if isinstance(row.variables, list) else []
        return MessageTemplate(
            id=row.id,
            name=row.name,
            kind=row.kind,
            subject=row.subject,
            body=row.body,
            variables=list(variables),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def add(
        self,
        *,
        name: str,
        kind: str,
        subject: str,
        body: str,
        variables: list[str],
    ) -> MessageTemplate:
        created_at = _now_iso()
        with self._engine.begin() as conn:
            try:
                row = conn.execute(
                    text(
                        "SELECT seq FROM message_templates WHERE tenant_id = :tenant_id "
                        "ORDER BY seq DESC LIMIT 1 FOR UPDATE"
                    ),
                    {"tenant_id": self._tenant_id},
                ).first()
                seq = (int(row.seq) if row is not None else 0) + 1
                template_id = f"mtpl-{seq}"
                conn.execute(
                    text(
                        "INSERT INTO message_templates "
                        "(tenant_id, id, seq, name, kind, subject, body, variables, created_at, updated_at) "
                        "VALUES (:tenant_id, :id, :seq, :name, :kind, :subject, :body, "
                        "CAST(:variables AS JSONB), :created_at, :updated_at)"
                    ),
                    {
                        "tenant_id": self._tenant_id,
                        "id": template_id,
                        "seq": seq,
                        "name": name,
                        "kind": kind,
                        "subject": subject,
                        "body": body,
                        "variables": json.dumps(list(variables), ensure_ascii=False),
                        "created_at": created_at,
                        "updated_at": created_at,
                    },
                )
            except IntegrityError:
                raise MessageTemplateNameConflict(name) from None
        return MessageTemplate(
            id=template_id,
            name=name,
            kind=kind,
            subject=subject,
            body=body,
            variables=list(variables),
            created_at=created_at,
            updated_at=created_at,
        )

    def get(self, template_id: str) -> MessageTemplate | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT {_SELECT_COLS} "
                    "FROM message_templates WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": template_id},
            ).first()
        return self._to_model(row) if row is not None else None

    def list(self, kind: str | None = None) -> list[MessageTemplate]:
        with self._engine.connect() as conn:
            if kind is None:
                rows = conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} "
                        "FROM message_templates WHERE tenant_id = :tenant_id ORDER BY seq DESC"
                    ),
                    {"tenant_id": self._tenant_id},
                ).all()
            else:
                rows = conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} "
                        "FROM message_templates WHERE tenant_id = :tenant_id AND kind = :kind "
                        "ORDER BY seq DESC"
                    ),
                    {"tenant_id": self._tenant_id, "kind": kind},
                ).all()
        return [self._to_model(row) for row in rows]

    def update(
        self,
        template_id: str,
        *,
        name: str,
        kind: str,
        subject: str,
        body: str,
        variables: list[str],
    ) -> MessageTemplate | None:
        updated_at = _now_iso()
        with self._engine.begin() as conn:
            try:
                result = conn.execute(
                    text(
                        "UPDATE message_templates SET name = :name, kind = :kind, "
                        "subject = :subject, body = :body, variables = CAST(:variables AS JSONB), "
                        "updated_at = :updated_at "
                        "WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {
                        "name": name,
                        "kind": kind,
                        "subject": subject,
                        "body": body,
                        "variables": json.dumps(list(variables), ensure_ascii=False),
                        "updated_at": updated_at,
                        "tenant_id": self._tenant_id,
                        "id": template_id,
                    },
                )
            except IntegrityError:
                raise MessageTemplateNameConflict(name) from None
            if result.rowcount == 0:
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM message_templates WHERE tenant_id = :tenant_id AND id = :id"
                    ),
                    {"tenant_id": self._tenant_id, "id": template_id},
                ).first()
                if exists is None:
                    return None
                # rowcount=0 且存在＝name 唯一冲突（同租户撞名）已被 UPDATE 条件外的约束挡住；
                # IntegrityError 分支已覆盖，此处兜底不返回假成功。
                raise MessageTemplateNameConflict(name)
        return self.get(template_id)

    def delete(self, template_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM message_templates "
                    "WHERE tenant_id = :tenant_id AND id = :id"
                ),
                {"tenant_id": self._tenant_id, "id": template_id},
            )
            return result.rowcount > 0

    def clear(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM message_templates WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )

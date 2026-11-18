"""PgMessageTemplateStore：消息模板 PG 落库（打包 A2，契约 docs/98）。

方法面与 `message/template_store.py MessageTemplateStore` 逐字一致。
seq 在同租户行锁内取（SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE，照 A1 修复先例）；
variables 走 JSONB；name 唯一由 `ux_message_templates_tenant_name`（LOWER）承担，
IntegrityError 转 `MessageTemplateNameConflict`（409）。

渐进 ORM 化批 1（方案 C）：内部 SQL 迁到 SQLAlchemy ORM（`storage/models.py`
`MessageTemplateORM`，同一 sync Engine 上叠 sessionmaker），对外构造签名
``(engine, tenant_id)`` 与方法面不变；行锁取 seq / 表达式唯一索引 / JSONB 往返
语义逐条保持。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from atlas.storage.models import MessageTemplateORM

from .template_store import MessageTemplate, MessageTemplateNameConflict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PgMessageTemplateStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        self._session_factory = sessionmaker(bind=engine)

    @staticmethod
    def _to_model(row: MessageTemplateORM) -> MessageTemplate:
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
        try:
            with self._session_factory.begin() as session:
                row = session.execute(
                    select(MessageTemplateORM.seq)
                    .where(MessageTemplateORM.tenant_id == self._tenant_id)
                    .order_by(MessageTemplateORM.seq.desc())
                    .limit(1)
                    .with_for_update()
                ).first()
                seq = (int(row.seq) if row is not None else 0) + 1
                template_id = f"mtpl-{seq}"
                session.add(
                    MessageTemplateORM(
                        tenant_id=self._tenant_id,
                        id=template_id,
                        seq=seq,
                        name=name,
                        kind=kind,
                        subject=subject,
                        body=body,
                        variables=list(variables),
                        created_at=created_at,
                        updated_at=created_at,
                    )
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
        with self._session_factory() as session:
            row = session.get(MessageTemplateORM, (self._tenant_id, template_id))
        return self._to_model(row) if row is not None else None

    def list(self, kind: str | None = None) -> list[MessageTemplate]:
        stmt = select(MessageTemplateORM).where(MessageTemplateORM.tenant_id == self._tenant_id)
        if kind is not None:
            stmt = stmt.where(MessageTemplateORM.kind == kind)
        stmt = stmt.order_by(MessageTemplateORM.seq.desc())
        with self._session_factory() as session:
            rows = session.scalars(stmt).all()
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
        try:
            with self._session_factory.begin() as session:
                # Session.execute 标注返回 Result；写语句运行时是 CursorResult，
                # cast 取 rowcount（与既存 text() 档 Connection.execute 行为一致）。
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(MessageTemplateORM)
                        .where(
                            MessageTemplateORM.tenant_id == self._tenant_id,
                            MessageTemplateORM.id == template_id,
                        )
                        .values(
                            name=name,
                            kind=kind,
                            subject=subject,
                            body=body,
                            variables=list(variables),
                            updated_at=updated_at,
                        )
                    ),
                )
                if result.rowcount == 0:
                    exists = session.execute(
                        select(MessageTemplateORM.id).where(
                            MessageTemplateORM.tenant_id == self._tenant_id,
                            MessageTemplateORM.id == template_id,
                        )
                    ).first()
                    if exists is None:
                        return None
                    # rowcount=0 且存在＝name 唯一冲突（同租户撞名）已被 UPDATE 条件外的
                    # 约束挡住；IntegrityError 分支已覆盖，此处兜底不返回假成功。
                    raise MessageTemplateNameConflict(name)
        except IntegrityError:
            raise MessageTemplateNameConflict(name) from None
        return self.get(template_id)

    def delete(self, template_id: str) -> bool:
        with self._session_factory.begin() as session:
            result = cast(
                "CursorResult[Any]",
                session.execute(
                    delete(MessageTemplateORM).where(
                        MessageTemplateORM.tenant_id == self._tenant_id,
                        MessageTemplateORM.id == template_id,
                    )
                ),
            )
            return bool(result.rowcount > 0)

    def clear(self) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                delete(MessageTemplateORM).where(MessageTemplateORM.tenant_id == self._tenant_id)
            )

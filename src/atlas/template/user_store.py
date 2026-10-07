"""UserTemplateStore：租户私有的用户自建流程模板（打包 X，契约 docs/85；A1 增量 docs/97）。

与内置只读目录（template/catalog.py）并列、不混写：id 空间 `utpl-<租户内 seq>`。
PG 档见 template/pg_store.py，方法面与本文件逐字一致。
A1：增 version/updated_at/usage_count/params（版本 CAS 防覆盖、显式 touch 计数、参数化声明）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TemplateVersionConflict(Exception):
    """CAS 冲突：PUT 携带的 if_match_version 与当前 version 不匹配（docs/97 E-1/E-2）。"""


class UserTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = ""
    graph: dict[str, Any]
    created_at: str
    version: int = 1
    updated_at: str = ""
    usage_count: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


class UserTemplateRepository(Protocol):
    def add(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        category: str = "",
        graph: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> UserTemplate: ...

    def get(self, template_id: str) -> UserTemplate | None: ...

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
    ) -> UserTemplate | None: ...

    def list(self) -> list[UserTemplate]: ...

    def delete(self, template_id: str) -> bool: ...

    def touch(self, template_id: str) -> bool: ...

    def clear(self) -> None: ...


class UserTemplateStore:
    def __init__(self) -> None:
        self._items: dict[str, UserTemplate] = {}
        self._seq = 0
        self._lock = threading.Lock()

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
        now = _now_iso()
        with self._lock:
            self._seq += 1
            template = UserTemplate(
                id=f"utpl-{self._seq}",
                name=name,
                description=description,
                tags=list(tags),
                category=category,
                graph=graph,
                created_at=now,
                version=1,
                updated_at=now,
                usage_count=0,
                params=dict(params) if params else {},
            )
            self._items[template.id] = template
            return template

    def get(self, template_id: str) -> UserTemplate | None:
        return self._items.get(template_id)

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
        with self._lock:
            current = self._items.get(template_id)
            if current is None:
                return None
            if if_match_version is not None and current.version != if_match_version:
                raise TemplateVersionConflict()
            updated = UserTemplate(
                id=current.id,
                name=name,
                description=description,
                tags=list(tags),
                category=category,
                graph=graph,
                created_at=current.created_at,
                version=current.version + 1,
                updated_at=_now_iso(),
                usage_count=current.usage_count,
                params=dict(current.params) if params is None else dict(params),
            )
            self._items[template_id] = updated
            return updated

    def list(self) -> list[UserTemplate]:
        # 新模板在前（id 序倒序等价创建序倒序）。
        return [
            self._items[key]
            for key in sorted(self._items, key=lambda item: int(item.split("-", 1)[1]), reverse=True)
        ]

    def delete(self, template_id: str) -> bool:
        with self._lock:
            return self._items.pop(template_id, None) is not None

    def touch(self, template_id: str) -> bool:
        with self._lock:
            current = self._items.get(template_id)
            if current is None:
                return False
            self._items[template_id] = current.model_copy(update={"usage_count": current.usage_count + 1})
            return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._seq = 0

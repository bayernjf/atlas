"""UserTemplateStore：租户私有的用户自建流程模板（打包 X，契约 docs/85）。

与内置只读目录（template/catalog.py）并列、不混写：id 空间 `utpl-<租户内 seq>`。
PG 档见 template/pg_store.py，方法面与本文件逐字一致。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    graph: dict[str, Any]
    created_at: str


class UserTemplateRepository(Protocol):
    def add(
        self, *, name: str, description: str, tags: list[str], graph: dict[str, Any]
    ) -> UserTemplate: ...

    def get(self, template_id: str) -> UserTemplate | None: ...

    def list(self) -> list[UserTemplate]: ...

    def delete(self, template_id: str) -> bool: ...

    def clear(self) -> None: ...


class UserTemplateStore:
    def __init__(self) -> None:
        self._items: dict[str, UserTemplate] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def add(
        self, *, name: str, description: str, tags: list[str], graph: dict[str, Any]
    ) -> UserTemplate:
        with self._lock:
            self._seq += 1
            template = UserTemplate(
                id=f"utpl-{self._seq}",
                name=name,
                description=description,
                tags=list(tags),
                graph=graph,
                created_at=_now_iso(),
            )
            self._items[template.id] = template
            return template

    def get(self, template_id: str) -> UserTemplate | None:
        return self._items.get(template_id)

    def list(self) -> list[UserTemplate]:
        # 新模板在前（id 序倒序等价创建序倒序）。
        return [
            self._items[key]
            for key in sorted(self._items, key=lambda item: int(item.split("-", 1)[1]), reverse=True)
        ]

    def delete(self, template_id: str) -> bool:
        with self._lock:
            return self._items.pop(template_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._seq = 0

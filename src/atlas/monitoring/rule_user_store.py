"""UserRuleTemplateStore：租户私有的用户自建告警规则模板（打包 ZS，契约 docs/102）。

与内置只读目录（monitoring/rule_templates.py RULE_TEMPLATES）并列、不混写：
id 空间 `urt-<租户内 seq>`；config 必须是完整 RuleConfig dict（由 API 层过
alerts.validate_rules 后入库，本模块不承担校验）。
PG 档见 monitoring/pg_rule_user_store.py，方法面与本文件逐字一致。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserRuleTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any]
    created_at: str


class UserRuleTemplateRepository(Protocol):
    def add(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        config: dict[str, Any],
    ) -> UserRuleTemplate: ...

    def get(self, template_id: str) -> UserRuleTemplate | None: ...

    def update(
        self,
        template_id: str,
        *,
        name: str,
        description: str,
        tags: list[str],
        config: dict[str, Any],
    ) -> UserRuleTemplate | None: ...

    def list(self) -> list[UserRuleTemplate]: ...

    def delete(self, template_id: str) -> bool: ...

    def clear(self) -> None: ...


class UserRuleTemplateStore:
    def __init__(self) -> None:
        self._items: dict[str, UserRuleTemplate] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def add(
        self,
        *,
        name: str,
        description: str,
        tags: list[str],
        config: dict[str, Any],
    ) -> UserRuleTemplate:
        with self._lock:
            self._seq += 1
            template = UserRuleTemplate(
                id=f"urt-{self._seq}",
                name=name,
                description=description,
                tags=list(tags),
                config=dict(config),
                created_at=_now_iso(),
            )
            self._items[template.id] = template
            return template

    def get(self, template_id: str) -> UserRuleTemplate | None:
        return self._items.get(template_id)

    def update(
        self,
        template_id: str,
        *,
        name: str,
        description: str,
        tags: list[str],
        config: dict[str, Any],
    ) -> UserRuleTemplate | None:
        with self._lock:
            current = self._items.get(template_id)
            if current is None:
                return None
            updated = UserRuleTemplate(
                id=current.id,
                name=name,
                description=description,
                tags=list(tags),
                config=dict(config),
                created_at=current.created_at,
            )
            self._items[template_id] = updated
            return updated

    def list(self) -> list[UserRuleTemplate]:
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

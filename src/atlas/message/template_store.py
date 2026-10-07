"""MessageTemplateStore：租户消息通知模板（打包 A2，契约 docs/98；D24 最小切片）。

独立于 user_template（template/user_store.py）：通知正文模板（subject/body 含 `{{var}}` 占位），
id 空间 `mtpl-<租户内 seq>`；kind ∈ {approval, alert} 分别供审批通知与告警通知消费，
未配置模板回退默认正文逐字不变（纯超集）。PG 档见 message/pg_template_store.py，
方法面与本文件逐字一致。占位一致性（正文占位 ⊆ variables 声明）由 API 层校验
（本文件提供 `validate_message_template` 供复用）。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KINDS = {"approval", "alert"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_placeholders(text: str) -> list[str]:
    """提取正文中的 `{{var}}` 占位名（去重、保序）；路径形态（a.b[0]）按原样返回。"""
    seen: list[str] = []
    for match in _TEMPLATE_VAR_RE.findall(text):
        name = match.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


class MessageTemplateNameConflict(Exception):
    """name 租户内重复：创建/更新撞名（docs/98 E-1：409 MESSAGE_TEMPLATE_NAME_CONFLICT）。"""


class MessageTemplate(BaseModel):
    id: str
    name: str
    kind: str
    subject: str
    body: str
    variables: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


def validate_message_template(
    *,
    name: str,
    kind: str,
    subject: str,
    body: str,
    variables: list[str],
) -> list[str]:
    """创建/更新共用形状校验（docs/98 §2.3）：返回中文错误列表（空＝通过）。

    规则：kind 枚举；name 非空 ≤64；subject/body 非空；subject ≤200、body ≤4000；
    variables ≤20、白名单正则、去重；正文占位 ⊆ variables 声明（否则 422
    MESSAGE_TEMPLATE_UNDECLARED_VAR，detail 带变量名）。
    """
    errors: list[str] = []
    if not isinstance(kind, str) or kind not in _KINDS:
        errors.append("kind 必须是 approval 或 alert")
    if not isinstance(name, str) or not name.strip():
        errors.append("模板名称不能为空")
    elif len(name.strip()) > 64:
        errors.append("模板名称长度须在 64 字符以内")
    if not isinstance(subject, str) or not subject.strip():
        errors.append("邮件主题不能为空")
    elif len(subject) > 200:
        errors.append("邮件主题长度须在 200 字符以内")
    if not isinstance(body, str) or not body.strip():
        errors.append("正文不能为空")
    elif len(body) > 4000:
        errors.append("正文长度须在 4000 字符以内")
    if not isinstance(variables, list):
        errors.append("variables 必须是字符串列表")
    else:
        if len(variables) > 20:
            errors.append("模板变量声明不能超过 20 个")
        seen: set[str] = set()
        for var in variables:
            if not isinstance(var, str) or not _VAR_NAME_RE.match(var):
                errors.append(f"变量名 {var!r} 不合法（须 [A-Za-z_][A-Za-z0-9_]*）")
            elif var in seen:
                errors.append(f"变量名重复：{var}")
            else:
                seen.add(var)
    # 正文占位一致性（subject/body 出现的占位 ⊆ variables 声明）。
    declared = {v for v in variables if isinstance(v, str)}
    for part_name, part in (("邮件主题", subject), ("正文", body)):
        if not isinstance(part, str):
            continue
        for placeholder in extract_placeholders(part):
            if placeholder not in declared:
                errors.append(
                    f"{part_name} 含未声明变量 {placeholder}（须在 variables 中声明）"
                )
    return errors


class MessageTemplateRepository(Protocol):
    def add(
        self,
        *,
        name: str,
        kind: str,
        subject: str,
        body: str,
        variables: list[str],
    ) -> MessageTemplate: ...

    def get(self, template_id: str) -> MessageTemplate | None: ...

    def list(self, kind: str | None = None) -> list[MessageTemplate]: ...

    def update(
        self,
        template_id: str,
        *,
        name: str,
        kind: str,
        subject: str,
        body: str,
        variables: list[str],
    ) -> MessageTemplate | None: ...

    def delete(self, template_id: str) -> bool: ...

    def clear(self) -> None: ...


class MessageTemplateStore:
    def __init__(self) -> None:
        self._items: dict[str, MessageTemplate] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def add(
        self,
        *,
        name: str,
        kind: str,
        subject: str,
        body: str,
        variables: list[str],
    ) -> MessageTemplate:
        now = _now_iso()
        with self._lock:
            if any(item.name.casefold() == name.casefold() for item in self._items.values()):
                raise MessageTemplateNameConflict(name)
            self._seq += 1
            template = MessageTemplate(
                id=f"mtpl-{self._seq}",
                name=name,
                kind=kind,
                subject=subject,
                body=body,
                variables=list(variables),
                created_at=now,
                updated_at=now,
            )
            self._items[template.id] = template
            return template

    def get(self, template_id: str) -> MessageTemplate | None:
        return self._items.get(template_id)

    def list(self, kind: str | None = None) -> list[MessageTemplate]:
        items = list(self._items.values())
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        # 新模板在前（id 序倒序等价创建序倒序）。
        return sorted(
            items, key=lambda item: int(item.id.split("-", 1)[1]), reverse=True
        )

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
        with self._lock:
            current = self._items.get(template_id)
            if current is None:
                return None
            for item in self._items.values():
                if (
                    item.id != template_id
                    and item.name.casefold() == name.casefold()
                ):
                    raise MessageTemplateNameConflict(name)
            updated = MessageTemplate(
                id=current.id,
                name=name,
                kind=kind,
                subject=subject,
                body=body,
                variables=list(variables),
                created_at=current.created_at,
                updated_at=_now_iso(),
            )
            self._items[template_id] = updated
            return updated

    def delete(self, template_id: str) -> bool:
        with self._lock:
            return self._items.pop(template_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._seq = 0

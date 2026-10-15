# -*- coding: utf-8 -*-
"""NL 造工具的草稿 store（docs/133 打包 BQ＝D71；docs/127 里程碑 F）。

草稿生命周期：LLM 结构化输出 AdapterDefinition → 落草稿（decision_status=NULL）
→ dry-run 连通校验（reachability 更新，不改变 decision）→ 人工采纳
（adopted/dismissed，复用 D51 采纳形态：可改判）→ adopted 后 runtime 动态装配。

内存档进程内 dict（每租户；reset 清本租户）；PG 档见 `PgAdapterDraftStore`
（迁移 055 `adapter_drafts`），两档方法签名一比一。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


class AdapterDraft(BaseModel):
    draft_id: str
    definition: dict[str, Any]
    decision_status: str | None = None  # NULL=待决策；adopted/dismissed
    reachability: dict[str, Any] | None = None  # {checked_at, ok, reason}
    created_at: str
    decided_at: str | None = None
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdapterDraftStore:
    """进程内 per-tenant 适配器草稿（随 TenantServices 装配；reset 清本租户）。"""

    def __init__(self) -> None:
        self._drafts: dict[str, AdapterDraft] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def next_draft_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"ad-{self._counter}"

    def add(self, definition: dict[str, Any]) -> dict[str, Any]:
        draft = AdapterDraft(
            draft_id=self.next_draft_id(),
            definition=definition,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        with self._lock:
            self._drafts[draft.draft_id] = draft
        return draft.model_dump()

    def get(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            draft = self._drafts.get(draft_id)
        return draft.model_dump() if draft is not None else None

    def list(self, *, adopted: bool | None = None) -> list[dict[str, Any]]:
        """adopted=True 只回已采纳（runtime 装配用）；None 回全部（按创建倒序）。"""
        with self._lock:
            drafts = list(self._drafts.values())
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        if adopted is True:
            drafts = [d for d in drafts if d.decision_status == "adopted"]
        return [d.model_dump() for d in drafts]

    def set_decision(self, draft_id: str, status: str) -> dict[str, Any] | None:
        """adopted/dismissed（可改判）；不存在返回 None。已终态重复改判仍写（D51 可改判）。"""
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                return None
            draft.decision_status = status
            draft.decided_at = _now_iso()
            draft.updated_at = _now_iso()
            return draft.model_dump()

    def set_reachability(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                return None
            draft.reachability = payload
            draft.updated_at = _now_iso()
            return draft.model_dump()

    def reset(self) -> None:
        with self._lock:
            self._drafts.clear()
            self._counter = 0

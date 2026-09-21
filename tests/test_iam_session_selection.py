# -*- coding: utf-8 -*-
"""会话存储后端选择纯逻辑测试（docs/30 §7，U223；无 DB）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas.iam.deps import select_session_store
from atlas.iam.sessions import SessionStore


def test_select_memory_store_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_STORAGE_BACKEND", raising=False)
    assert isinstance(select_session_store(), SessionStore)


def test_select_pg_store_when_backend_is_pg(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "pg")

    import atlas.storage.pg as pg_module

    monkeypatch.setattr(
        pg_module,
        "get_pg_backend",
        lambda: SimpleNamespace(session_store=lambda: sentinel),
    )

    assert select_session_store() is sentinel

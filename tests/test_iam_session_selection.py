# -*- coding: utf-8 -*-
"""会话存储后端选择纯逻辑测试（docs/30 §7，U223；无 DB）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.iam.deps import select_session_store
from atlas.iam.sessions import SessionStore
from atlas.security.bootstrap import read_storage_backend


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


# --- docs/77 R4：ATLAS_STORAGE_BACKEND 统一档位读取器 ------------------------

def test_read_storage_backend_defaults_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_STORAGE_BACKEND", raising=False)
    assert read_storage_backend() == "memory"
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "PG")
    assert read_storage_backend() == "pg", "大小写不敏感（PROD 大写曾把 demo 面误判成非 prod）"
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", " memory ")
    assert read_storage_backend() == "memory"


def test_read_storage_backend_rejects_illegal_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法值拒启：写 postgres/拼错时不得静默降级成内存档（全状态在 RAM）。"""
    monkeypatch.setenv("ATLAS_STORAGE_BACKEND", "postgres")
    with pytest.raises(ValueError, match="ATLAS_STORAGE_BACKEND"):
        read_storage_backend()


def test_no_bare_storage_backend_env_read_remains() -> None:
    """R4 防复发：三处裸比较（registry/deps）不得回归成直读 os.environ。"""
    root = Path(__file__).resolve().parents[1] / "src" / "atlas"
    offenders = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if 'os.environ.get("ATLAS_STORAGE_BACKEND"' in path.read_text(encoding="utf-8")
    )
    assert not offenders, f"仍有裸档位读取（应走 read_storage_backend）：{offenders}"

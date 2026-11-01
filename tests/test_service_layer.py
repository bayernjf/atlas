# -*- coding: utf-8 -*-
"""Service layer pure helpers extracted from api/main.py (Tier0 review P2).

- audit_query: since/until bounds and cursor validation keep their old 422 shapes
- model_config_service: request -> ModelConfig, key sealing and masked view
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from atlas.llm.config import ModelConfig
from atlas.services import audit_query as aq
from atlas.services import model_config_service as mcs


# ---------------- audit_query ----------------

def test_audit_bounds_clean_blank_and_passthrough():
    assert aq.audit_bounds(None, "  ") == (None, None)
    lo, hi = aq.audit_bounds("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert lo is not None and hi is not None


def test_audit_bounds_reject_bad_iso_and_reversed_range():
    with pytest.raises(HTTPException) as e1:
        aq.audit_bounds("not-a-date", None)
    assert e1.value.status_code == 422
    assert isinstance(e1.value.detail, list) and e1.value.detail

    with pytest.raises(HTTPException) as e2:
        aq.audit_bounds("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")
    assert e2.value.status_code == 422


def test_audit_cursor_rules():
    assert aq.audit_cursor(None) is None
    assert aq.audit_cursor(7) == 7
    with pytest.raises(HTTPException) as e:
        aq.audit_cursor(0)
    assert e.value.status_code == 422


# ---------------- model_config_service ----------------

class _FakeSecretProvider:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def encrypt(self, plaintext: str) -> str:
        token = f"enc$v1${plaintext}"
        self.store[token] = plaintext
        return token

    def decrypt(self, token: str) -> str:
        return self.store[token]


def _patch_provider(monkeypatch, provider: _FakeSecretProvider) -> None:
    import atlas.connections.service as conn_service

    monkeypatch.setattr(conn_service, "get_secret_provider", lambda: provider)


def _existing_cfg(enc: str = "") -> ModelConfig:
    return ModelConfig(
        mode="byok",  # type: ignore[arg-type]
        model="old-model",
        api_key_enc=enc,
        base_url="https://old.example",
        enabled=True,
        updated_by="old",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_build_config_seals_key_and_fills_defaults(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_provider(monkeypatch, provider)
    req = mcs.ModelConfigRequest(model="  new-model  ", apiKey="sk-secret-1234")
    cfg = mcs.build_model_config("byok", req, None, updated_by="admin-a")
    assert cfg.model == "new-model"
    assert cfg.api_key_enc == "enc$v1$sk-secret-1234"
    assert cfg.enabled is True
    assert cfg.updated_by == "admin-a"


def test_blank_key_and_missing_fields_keep_existing(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_provider(monkeypatch, provider)
    old = _existing_cfg(enc="enc$v1$keep-me")
    req = mcs.ModelConfigRequest(model=None, apiKey="  ", enabled=False)
    cfg = mcs.build_model_config("byok", req, old, updated_by="admin-a")
    assert cfg.model == "old-model"
    assert cfg.api_key_enc == "enc$v1$keep-me"
    assert cfg.base_url == "https://old.example"
    assert cfg.enabled is False


def test_empty_model_name_is_422(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_provider(monkeypatch, provider)
    with pytest.raises(HTTPException) as e:
        mcs.build_model_config("byok", mcs.ModelConfigRequest(model=" "), None, updated_by="x")
    assert e.value.status_code == 422


def test_masked_view_last4_and_bad_envelope_fallback(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_provider(monkeypatch, provider)
    cfg = mcs.build_model_config(
        "byok",
        mcs.ModelConfigRequest(model="m", apiKey="sk-abcd1234"),
        None,
        updated_by="x",
    )
    view = mcs.masked_view(cfg)
    assert view["apiKey"] == "***1234"

    broken = _existing_cfg(enc="enc$missing")
    assert mcs.masked_view(broken)["apiKey"] == "***"

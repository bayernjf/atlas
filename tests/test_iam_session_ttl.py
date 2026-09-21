# -*- coding: utf-8 -*-
"""U229 会话绝对 TTL 纯逻辑（docs/31 §4，ADR T25）。"""

from __future__ import annotations

from atlas.iam.principals import Principal, Role
from atlas.iam.sessions import SessionStore, session_ttl_seconds


def _principal() -> Principal:
    return Principal(
        tenant_id="t1",
        tenant_name="演示企业 A",
        username="ttl-user",
        display_name="TTL 用户",
        role=Role.VIEWER,
    )


def test_ttl_config_default_and_env(monkeypatch) -> None:
    monkeypatch.delenv("ATLAS_SESSION_TTL_HOURS", raising=False)
    assert session_ttl_seconds() == 12 * 3600

    monkeypatch.setenv("ATLAS_SESSION_TTL_HOURS", "2")
    assert session_ttl_seconds() == 2 * 3600

    for bad in ("abc", "0", "200", "-3"):
        monkeypatch.setenv("ATLAS_SESSION_TTL_HOURS", bad)
        assert session_ttl_seconds() == 12 * 3600

    monkeypatch.setenv("ATLAS_SESSION_TTL_HOURS", "  ")
    assert session_ttl_seconds() == 12 * 3600


def test_unexpired_token_passes() -> None:
    store = SessionStore()
    token = store.issue(_principal(), now=1000.0, ttl_seconds=10)
    assert store.principal_for_token(token, now=1009.0) is not None


def test_expired_token_rejected_and_removed() -> None:
    store = SessionStore()
    token = store.issue(_principal(), now=1000.0, ttl_seconds=10)
    assert store.principal_for_token(token, now=1010.0) is None
    assert store.principal_for_token(token, now=0.0) is None


def test_revoked_and_other_users_unaffected() -> None:
    store = SessionStore()
    token = store.issue(_principal(), now=0.0, ttl_seconds=10)
    store.revoke(token)
    assert store.principal_for_token(token, now=5.0) is None

    other = Principal(
        tenant_id="t1",
        tenant_name="演示企业 A",
        username="other-user",
        display_name="另一用户",
        role=Role.VIEWER,
    )
    token_b = store.issue(other, now=0.0, ttl_seconds=10)
    assert store.principal_for_token(token_b, now=5.0) is not None
